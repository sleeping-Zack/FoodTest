"""M1 registration: imported statements remain unreviewed candidates."""
from importlib.resources import files
import json
import hashlib
import re

import psycopg
from psycopg.types.json import Jsonb

from ..common import KBError, digest, file_hash, identity, now, write_json
from ..contracts.jobs import JobReport
from ..db.session import insert
from ..storage.assets import inspect_file, preserve
from . import legacy_catalog, legacy_guide, legacy_taxonomy, legacy_indexes
from .inventory import CATALOG, GUIDE, LIBRARY, cover_quality

VERSION = 'legacy-2'


def sources(config):
    result=[CATALOG,GUIDE]
    result += [p.relative_to(config.source_root).as_posix() for p in sorted(config.source(LIBRARY+'/分类体系').glob('*.json'))]
    result += [LIBRARY+'/索引/'+name for name in legacy_indexes.LISTS]
    result += [LIBRARY+'/索引/产品种类定义表.json','food-classifier/resources/guide-pages.json',
               'food-classifier/resources/inspection-bridges.json','backend/standard_rules.json','backend/method_references.json']
    return result


def adapter(source):
    if source==CATALOG:return legacy_catalog
    if source==GUIDE:return legacy_guide
    if '/分类体系/' in source:return legacy_taxonomy
    name=source.rsplit('/',1)[-1]
    if name in legacy_indexes.LISTS or name in legacy_indexes.GROUPS or name in ('standard_rules.json','method_references.json'):
        return legacy_indexes
    raise KBError('UNSUPPORTED_SOURCE','No standard-domain adapter is registered for this source.')


def normalize_code(value):
    return re.sub(r'\s+','',value).replace('—','-').replace('－','-')


class Importer:
    def __init__(self,config,conn=None,dry_run=False):
        self.config=config;self.conn=conn;self.dry_run=dry_run
        self.assets={};self.pdf_checks={};self.catalog={};self.codes={}
        self.seen_versions={}
        self.inserted_assets=0
        self.observed_inputs={}
        self.field_map=json.loads(files('food_kb.ingest').joinpath('legacy_fields.json').read_text(encoding='utf8'))

    def asset(self,relative,expected=None):
        if relative not in self.assets:
            path=self.config.source(relative)
            info=inspect_file(path,expected)
            observed=self.observed_inputs.get(relative)
            if observed and observed not in ('missing','unavailable') and observed!=info['sha256']:
                raise KBError('SOURCE_CHANGED','Source changed after the batch fingerprint was computed.')
            if not self.dry_run:info=preserve(self.config,relative,expected)
            self.assets[relative]=info
        info=self.assets[relative]
        if expected and info['sha256']!=expected.lower():raise KBError('HASH_MISMATCH','Registered fingerprint mismatch.')
        if not self.dry_run:
            if insert(self.conn,'source_asset',info):self.inserted_assets+=1
        return info

    def register_asset(self,relative,index_asset,pointer,metadata,expected=None):
        info=self.asset(relative,expected)
        if not self.dry_run:
            quality='partial' if metadata.get('unreliable_text_pages') else 'needs_ocr' if metadata.get('empty_text_pages') else 'not_extracted'
            insert(self.conn,'source_registration',{
                'registration_id':identity('registration',relative,info['asset_id'],index_asset,pointer),
                'asset_id':info['asset_id'],'source_key':relative,'source_url':metadata.get('url'),
                'retrieved_at':metadata.get('retrieved_at'),'index_asset_id':index_asset,'json_pointer':pointer,
                'quality':quality,'identity_status':'unverified','metadata':metadata})
        return info

    def version(self,raw):
        code=raw.get('code')
        if not isinstance(code,str) or not code.strip() or not raw.get('title'):
            raise KBError('UNRECOGNIZED_FIELDS','Standard designation and title are required.')
        status='draft' if raw.get('is_draft') else 'conflicting' if raw.get('identity_conflict') else 'unverified'
        family=normalize_code(raw.get('family') or code)
        key=normalize_code(code)
        if raw.get('is_amendment'):key += ':amendment:'+str(raw.get('id'))
        # Conflicting claims within the same input are not silently last-write-wins.
        claim={k:raw.get(k) for k in ('title','publication_date','implementation_date')}
        claim['title']=''.join(claim['title'].split())
        previous=self.seen_versions.get(key)
        if previous and any(previous[k] and claim[k] and previous[k]!=claim[k] for k in claim):
            raise KBError('IDENTITY_CONFLICT','Incompatible metadata for the same designation.')
        self.seen_versions.setdefault(key,claim)
        document_id=identity('document',family)
        semantic={k:raw.get(k) for k in ('title','publication_date','implementation_date','official_status','is_amendment','is_draft','identity_conflict')}
        revision=digest(semantic)
        version_id=identity('version',document_id,key,revision)
        if not self.dry_run:
            insert(self.conn,'source_document',{'document_id':document_id,'source_key':family,'kind':'standard','standard_family':raw.get('family'),'title':raw['title']})
            insert(self.conn,'document_version',{'version_id':version_id,'document_id':document_id,'designation':code,
                'revision':revision,'title':raw['title'],'publication_date':raw.get('publication_date'),
                'implementation_date':raw.get('implementation_date'),'status_claim':raw.get('official_status'),
                'identity_status':status,'metadata_state':'present','metadata':semantic})
        return version_id,status

    def legacy(self,record,index_asset):
        legacy_id=identity('legacy',index_asset,record.pointer,VERSION)
        if not self.dry_run:
            insert(self.conn,'legacy_record',{'legacy_id':legacy_id,'index_asset_id':index_asset,
                'source_key':record.source,'json_pointer':record.pointer,'legacy_key':record.key,
                'adapter':record.kind,'importer_version':VERSION,'raw_payload':record.raw,'unknown_fields':record.unknown_fields})
        return legacy_id

    def anchor(self,asset_id,locator,quality='page_claim',quote=None):
        evidence_id=identity('evidence',asset_id,locator,quality,quote)
        if not self.dry_run:
            insert(self.conn,'evidence_anchor',{'evidence_id':evidence_id,'asset_id':asset_id,
                'locator':locator,'locator_quality':quality,'quote':quote,
                'quote_hash':hashlib.sha256(quote.encode('utf8')).hexdigest() if quote is not None else None})
        return evidence_id

    def catalog_record(self,record,index_asset,legacy_id):
        raw=record.raw
        version_id,status=self.version(raw)
        problems=[];available=0
        for i,a in enumerate(raw.get('attachments',[])):
            if not a.get('path'):
                problems.append('SOURCE_MISSING');continue
            try:
                info=self.register_asset(a['path'],index_asset,record.pointer+f'/attachments/{i}',a,a.get('sha256'))
                cover={}
                if info['media_type']=='application/pdf':
                    if info['asset_id'] not in self.pdf_checks:self.pdf_checks[info['asset_id']]=cover_quality(self.config.source(a['path']))
                    cover=self.pdf_checks[info['asset_id']]
                draft=raw.get('is_draft') or cover.get('cover_draft_signal')
                if cover.get('pdf_error'):problems.append('PDF_UNREADABLE')
                elif draft:problems.append('DRAFT')
                elif raw.get('identity_conflict'):problems.append('IDENTITY_CONFLICT')
                else:available+=1
                if not self.dry_run:
                    insert(self.conn,'version_asset',{'version_id':version_id,'asset_id':info['asset_id'],
                        'role':'draft' if draft else 'amendment' if raw.get('is_amendment') else 'original'})
            except KBError as exc:problems.append(exc.code)
        if not raw.get('attachments'):problems.append('SOURCE_MISSING')
        if not self.dry_run:insert(self.conn,'document_claim',{'legacy_id':legacy_id,'version_id':version_id})
        if 'HASH_MISMATCH' in problems or 'IDENTITY_CONFLICT' in problems:
            return 'identity_conflict',{'version_id':version_id,'issues':problems,'available_assets':available}
        if any(p in problems for p in ('DRAFT','PDF_UNREADABLE','MEDIA_MISMATCH','PATH_BOUNDARY')):
            return 'quarantined',{'version_id':version_id,'issues':problems,'available_assets':available}
        if any(p!='SOURCE_MISSING' for p in problems):
            return 'failed',{'version_id':version_id,'issues':problems,'available_assets':available}
        if problems:return 'source_missing',{'version_id':version_id,'issues':problems,'available_assets':available}
        return 'success',{'version_id':version_id,'available_assets':available}

    def source_refs(self,record):
        raw=record.raw;context=record.context;refs=[]
        if record.kind.startswith(('guide_','inspection_')) or record.kind in ('category','classification_table','classification_text','taxonomy_metadata'):
            path=context.get('source')
            if isinstance(path,str):refs.append({'path':path,'sha256':context.get('sha256'),'pdf_page':raw.get('pdf_page')})
        if record.kind=='reference_candidate':refs.append({'path':raw.get('source_path'),'sha256':raw.get('source_sha256'),'pdf_page':raw.get('pdf_page')})
        if record.kind=='announcement':refs.append({'path':raw.get('source_path'),'url':raw.get('source_url')})
        if record.kind=='method_entry':refs.extend(raw.get('conditions_and_scope',[]))
        if record.kind=='nonstandard_basis':refs.extend(raw.get('sources',[]))
        if record.kind=='definition_export':
            refs.append({'path':context.get('source_path'),'sha256':context.get('source_sha256'),'pdf_page':raw.get('pdf_page')})
        return [r for r in refs if r.get('path')]

    def associated_version(self,record):
        context=record.context;raw=record.raw
        catalog_id=raw.get('standard_id') or raw.get('source_id')
        if catalog_id in self.catalog:return self.version(self.catalog[catalog_id])[0]
        code=context.get('standard')
        if code:
            candidates=self.codes.get(normalize_code(code),[])
            if len(candidates)==1:return self.version(candidates[0])[0]
        if record.kind.startswith(('guide_','inspection_','definition_')):
            doc=identity('document','inspection-guide');version=identity('version',doc,'2026-upper')
            if not self.dry_run:
                insert(self.conn,'source_document',{'document_id':doc,'source_key':'inspection-guide','kind':'guide','title':'国家食品安全抽检实施细则'})
                insert(self.conn,'document_version',{'version_id':version,'document_id':doc,'designation':'2026 上册',
                    'revision':'legacy-source-1','title':'国家食品安全抽检实施细则（2026年版 上册）','identity_status':'unverified'})
            return version
        return None

    def candidate(self,record,index_asset,legacy_id):
        if record.context.get('invalid_parent'):
            raise KBError('CATEGORY_PARENT_MISSING','Category hierarchy has a missing parent or a cycle; raw staging retained.')
        def has_null(value):
            if isinstance(value,str):return '\x00' in value
            if isinstance(value,dict):return any(has_null(k) or has_null(v) for k,v in value.items())
            if isinstance(value,list):return any(has_null(v) for v in value)
            return False
        if has_null(record.raw):
            return 'quarantined',{'issues':['UNREPRESENTABLE_TEXT_CODEPOINT'],'raw_preserved':True}
        version=self.associated_version(record)
        logical_key=record.source+'#'+record.kind+':'+record.key
        revision=digest({'raw':record.raw,'context':{k:v for k,v in record.context.items() if k not in ('pages','definitions','chapters','sections','tables','inspection_rows')}})
        record_id=identity('knowledge',logical_key,revision)
        if not self.dry_run:
            insert(self.conn,'knowledge_record',{'record_id':record_id,'logical_key':logical_key,'revision':revision,
                'record_type':record.kind,'version_id':version,'legacy_id':legacy_id,'payload':record.raw,
                'extraction_run':VERSION,'review_state':'extracted'})
            insert(self.conn,'legacy_knowledge',{'legacy_id':legacy_id,'record_id':record_id})
        origin=self.anchor(index_asset,{'json_pointer':record.pointer},'source_pointer')
        if not self.dry_run:
            insert(self.conn,'knowledge_evidence',{'record_id':record_id,'evidence_id':origin,'asset_id':index_asset,'role':'provenance'})
        issues=[];page_anchors=[]
        for r in self.source_refs(record):
            try:
                info=self.register_asset(r['path'],index_asset,record.pointer,r,r.get('sha256'))
                locator={k:r[k] for k in ('pdf_page','pdf_pages') if r.get(k) is not None}
                if not locator:locator={'source_record':record.pointer,'location_status':'file_only'}
                ev=self.anchor(info['asset_id'],locator,quote=r.get('excerpt'))
                page_anchors.append(ev)
                if not self.dry_run:
                    if version:insert(self.conn,'version_asset',{'version_id':version,'asset_id':info['asset_id'],'role':'original'})
                    insert(self.conn,'knowledge_evidence',{'record_id':record_id,'evidence_id':ev,'version_id':version,
                        'asset_id':info['asset_id'],'role':'supporting'})
            except KBError as exc:issues.append(exc.code)
        if record.kind=='category' and version:
            if not self.dry_run:
                ns=record.context['namespace']+':'+version
                parent=record.raw.get('parent_id')
                insert(self.conn,'category_namespace',{'namespace':ns,'version_id':version})
                insert(self.conn,'category_node',{'category_id':identity('category',ns,record.raw['id']),
                    'namespace':ns,'version_id':version,'code':record.raw['classification_code'],'label':record.raw['label'],
                    'parent_id':identity('category',ns,parent) if parent else None,'definition_record_id':record_id})
        if record.kind=='reference_candidate' and not self.dry_run:
            insert(self.conn,'relation',{'relation_id':identity('relation',record_id),'from_record':record_id,
                'target_claim':record.raw.get('target_code'),'kind':'legacy_reference_candidate',
                'scope':{'claim':record.raw.get('relation')},'version_binding':record.raw.get('resolution',{}),
                'evidence_id':page_anchors[0] if page_anchors else origin,'review_state':'extracted'})
        status='identity_conflict' if 'HASH_MISMATCH' in issues else 'source_missing' if issues else 'success'
        return status,{'record_id':record_id,'version_id':version,'issues':issues,'review_state':'extracted'}

    def run(self,selected=None,only_keys=None):
        selected=selected or sources(self.config)
        inputs=[];snapshots={};observed={}
        # Snapshot index bytes once. Fingerprint referenced original bytes as well as metadata.
        for source in dict.fromkeys([CATALOG]+selected):
            path=self.config.source(source)
            if not path.is_file():inputs.append((source,'missing'));continue
            blob=path.read_bytes();sha=hashlib.sha256(blob).hexdigest();inputs.append((source,sha))
            try:
                data=json.loads(blob.decode('utf-8-sig'));snapshots[source]=data
                for r in adapter(source).records(source,data):
                    refs=r.raw.get('attachments',[]) if r.kind=='catalog' else self.source_refs(r)
                    for ref in refs:
                        relative=ref.get('path')
                        if not relative or relative in observed:continue
                        try:
                            target=self.config.source(relative)
                            observed[relative]=file_hash(target) if target.is_file() else 'missing'
                        except (KBError,OSError):observed[relative]='unavailable'
            except (ValueError,KeyError,TypeError,AttributeError):pass
        inputs += sorted(observed.items())
        self.observed_inputs=dict(inputs)
        report=JobReport('dry-run' if self.dry_run else 'import',digest(inputs),self.config.fingerprint)
        report.processor_version=VERSION
        if not self.dry_run:
            report.schema_version=self.conn.execute('SELECT max(version) AS v FROM schema_migration').fetchone()['v']
        job=report.job_id;task_key=digest([inputs,VERSION,self.config.fingerprint])
        if not self.dry_run:
            insert(self.conn,'ingest_job',{'job_id':job,'task_key':task_key,'stage':'import','input_fingerprint':report.input_fingerprint,
                'config_hash':report.config_hash,'processor_version':VERSION,'started_at':report.started_at,'status':'running'})
        try:
            catalog=snapshots[CATALOG]
            self.catalog={r['id']:r for r in catalog if isinstance(r,dict) and 'id' in r}
            for r in catalog:
                if isinstance(r,dict) and r.get('code') and not r.get('is_amendment') and not r.get('is_draft'):
                    self.codes.setdefault(normalize_code(r['code']),[]).append(r)
            for source in selected:
                try:
                    chosen_adapter=adapter(source)
                    index=self.asset(source,dict(inputs).get(source))
                    data=snapshots[source]
                    records=list(chosen_adapter.records(source,data))
                except (KBError,ValueError,OSError,KeyError,TypeError,AttributeError) as exc:
                    failure={'key':source,'status':'failed','error_code':getattr(exc,'code','INVALID_JSON_OR_SCHEMA'),'retryable':False}
                    report.items.append(failure)
                    if not self.dry_run:
                        insert(self.conn,'ingest_result',{'job_id':job,'item_key':source,'status':'failed','error_code':failure['error_code'],'retryable':False,'details':{}})
                    continue
                for record in records:
                    record.unknown_fields=sorted(record.raw.keys()-set(self.field_map.get(record.kind,[])))
                    item_key=source+'#'+record.pointer
                    if only_keys is not None and item_key not in only_keys and source not in only_keys:continue
                    item={'key':item_key,'legacy_key':record.key,'status':'failed','retryable':False}
                    try:
                        if self.dry_run:
                            lid=self.legacy(record,index['asset_id'])
                            status,details=self.catalog_record(record,index['asset_id'],lid) if record.kind=='catalog' else self.candidate(record,index['asset_id'],lid)
                        else:
                            # Lossless staging survives semantic rejection; authoritative links are atomic below.
                            lid=self.legacy(record,index['asset_id'])
                            item['legacy_id']=lid
                            with self.conn.transaction():
                                # One transaction per record; concurrent retries serialize this identity.
                                lock=int(digest([index['asset_id'],record.pointer])[:15],16)
                                self.conn.execute('SELECT pg_advisory_xact_lock(%s)',(lock,))
                                before_assets=self.inserted_assets
                                lid=identity('legacy',index['asset_id'],record.pointer,VERSION)
                                exists=self.conn.execute('SELECT 1 FROM document_claim WHERE legacy_id=%s UNION ALL SELECT 1 FROM legacy_knowledge WHERE legacy_id=%s LIMIT 1',(lid,lid)).fetchone()
                                status,details=self.catalog_record(record,index['asset_id'],lid) if record.kind=='catalog' else self.candidate(record,index['asset_id'],lid)
                                if status=='success' and exists and self.inserted_assets==before_assets:status='duplicate'
                        if record.unknown_fields and status in ('success','duplicate'):
                            status='unrecognized';details['unknown_fields']=record.unknown_fields
                        item.update(status=status,legacy_id=lid,details=details)
                        if status=='failed':
                            item['error_code']=details.get('issues',['INVALID_RECORD'])[0]
                            item['retryable']='SOURCE_CHANGED' in details.get('issues',[])
                    except (KBError,ValueError,KeyError,TypeError,OSError,psycopg.Error) as exc:
                        code=getattr(exc,'code',None) or ('DB_CONSTRAINT' if isinstance(exc,psycopg.Error) else 'INVALID_RECORD')
                        item.update(error_code=code,status='identity_conflict' if code in ('IDENTITY_CONFLICT','HASH_MISMATCH') else 'failed')
                        if isinstance(exc,psycopg.Error):
                            item['details']={'sqlstate':exc.sqlstate,'constraint':exc.diag.constraint_name,'table':exc.diag.table_name}
                    report.items.append(item)
                    if not self.dry_run:
                        insert(self.conn,'ingest_result',{'job_id':job,'item_key':item_key,'legacy_id':item.get('legacy_id'),
                            'status':item['status'],'error_code':item.get('error_code'),'retryable':item['retryable'],'details':item.get('details',{})})
            result=report.finish()
        except Exception:
            failure=report.__dict__|{'status':'failed','error_code':'JOB_FAILED','finished_at':now()}
            if not self.dry_run:
                write_json(self.config.output_root/'jobs'/f'{job}.json',failure)
                try:
                    self.conn.execute("UPDATE ingest_job SET status='failed',finished_at=now(),report=%s WHERE job_id=%s",
                                      (Jsonb({'error_code':'JOB_FAILED','processed':len(report.items)}),job))
                except psycopg.Error:
                    pass  # The file remains an explicit failure record if the database is disconnected.
            raise
        result['observed_assets_fingerprint']=digest(sorted((p,a['sha256']) for p,a in self.assets.items()))
        result['observed_assets']=len(self.assets)
        if not self.dry_run:
            self.conn.execute('UPDATE ingest_job SET status=%s,finished_at=now(),report=%s WHERE job_id=%s',
                              (result['status'],Jsonb({k:v for k,v in result.items() if k!='items'}),job))
            write_json(self.config.output_root/'jobs'/f'{job}.json',result)
        return result
