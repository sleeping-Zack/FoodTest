"""Run adapters against preserved assets, keeping attempts separate from immutable output."""
from uuid import uuid4
import zipfile

from ..common import KBError,digest,file_hash,identity,read_json,write_json
from ..db.repository import register_parse
from ..db.session import insert
from ..storage.assets import preserve
from .contracts import CONTRACT_VERSION,isolate_invalid_pages,quality,validate_document

VERSION='kb-parser-1'


def stored_path(config,key):
    path=(config.output_root/key).resolve()
    if not path.is_relative_to(config.output_root.resolve()):
        raise KBError('PATH_BOUNDARY','Parsed artifact lies outside the output directory.')
    return path


def read_attempt(conn,config,attempt_id):
    row=conn.execute('SELECT * FROM parse_attempt WHERE attempt_id=%s',(attempt_id,)).fetchone()
    if not row:raise KBError('NOT_FOUND','Parse attempt does not exist.')
    path=stored_path(config,row['report_key'])
    if not path.is_file() or file_hash(path)!=row['report_sha256']:
        raise KBError('PARSE_REPORT_CHANGED','Stored parse report is missing or changed.')
    report=read_json(path)
    for page in report['document']['pages']:
        image=page.get('image')
        if image:
            image_path=stored_path(config,image['storage_key'])
            if not image_path.is_file() or file_hash(image_path)!=image['sha256']:
                raise KBError('PAGE_IMAGE_CHANGED','Stored source page image is missing or changed.')
    return report


def _adapter(path,cache_source):
    if cache_source:return 'cache'
    with path.open('rb') as f:header=f.read(8)
    if header.startswith(b'%PDF-'):return 'pdf'
    if header.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):return 'word'
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if 'word/document.xml' in z.namelist():return 'word'
    raise KBError('PARSER_UNSUPPORTED','No parser for this registered file type; legacy .doc requires an approved conversion.')


def parse_asset(conn,config,asset_id=None,*,pages=None,options=None,cache_source=None,retry_attempt=None):
    options={'ocr':'off','dpi':200,'language':'chi_sim+eng','tessdata':None,'timeout_seconds':60}|(options or {})
    previous=read_attempt(conn,config,retry_attempt) if retry_attempt else None
    if previous:
        if asset_id and asset_id!=previous['asset_id']:raise KBError('RETRY_MISMATCH','Retry must use the original source.')
        asset_id=previous['asset_id'];pages=previous['requested_pages'];options=previous['runtime_options'];cache_source=previous['cache_source']
    row=conn.execute('SELECT * FROM source_asset WHERE asset_id=%s',(asset_id,)).fetchone()
    if not row:raise KBError('NOT_FOUND','Register the source asset before parsing it.')
    path=stored_path(config,row['storage_key'])
    if not path.is_file() or file_hash(path)!=asset_id:raise KBError('STORAGE_CORRUPT','Preserved source bytes do not match their registered fingerprint.')
    if pages is not None:
        if not pages or any(type(p) is not int or p<1 for p in pages):raise KBError('PARSE_PAGES','Pages must be positive actual PDF page numbers.')
        pages=sorted(set(pages))
    adapter=_adapter(path,cache_source)
    cache_path=None;cache_id=None
    if adapter=='pdf':
        from .pdf import describe_engine,parse_pdf
        engine=describe_engine(options)
    elif adapter=='cache':
        from .legacy import LEGACY_VERSION,parse_cache
        from .pdf import describe_engine,parse_pdf
        if previous:
            cache_info=conn.execute('SELECT * FROM source_asset WHERE asset_id=%s',(previous['cache_asset_id'],)).fetchone()
            if not cache_info:raise KBError('CACHE_SOURCE_MISSING','Preserved cache registration is missing.')
        else:
            cache_info=preserve(config,cache_source);insert(conn,'source_asset',cache_info)
        cache_id=cache_info['asset_id'];cache_path=stored_path(config,cache_info['storage_key'])
        if not cache_path.is_file() or file_hash(cache_path)!=cache_id:
            raise KBError('CACHE_SOURCE_CHANGED','Preserved cache bytes changed.')
        engine={'adapter':LEGACY_VERSION,'cache_sha256':cache_id,'fallback':describe_engine(options)}
    else:
        from .word import WORD_VERSION,parse_word
        if pages is not None:raise KBError('PARSE_PAGES','Word uses structural positions, not PDF page numbers.')
        engine={'adapter':WORD_VERSION}
    parser_version=VERSION+'/'+adapter
    semantic_options={k:v for k,v in options.items() if k!='tessdata'} if adapter in {'pdf','cache'} else {}
    config_hash=digest({'contract':CONTRACT_VERSION,'engine':engine,'options':semantic_options,'pages':pages})
    if previous and (previous['config_hash']!=config_hash or previous['parser_version']!=parser_version):
        raise KBError('RETRY_ENGINE_CHANGED','Engine or source cache changed; start a new parse instead of reusing old pages.')
    existing=conn.execute('SELECT parse_id FROM parse_run WHERE asset_id=%s AND parser_version=%s AND config_hash=%s',
        (asset_id,parser_version,config_hash)).fetchone()
    duplicate=False;reused_pages=[]
    if existing:
        saved=conn.execute('SELECT attempt_id FROM parse_attempt WHERE parse_id=%s ORDER BY created_at LIMIT 1',(existing['parse_id'],)).fetchone()
        if not saved:raise KBError('PARSE_REPORT_MISSING','An immutable parse exists without its processing report.')
        document=read_attempt(conn,config,saved['attempt_id'])['document'];duplicate=True
        reused_pages=[p['page'] for p in document['pages']]
    else:
        requested=pages
        if previous:
            requested=[p['page'] for p in previous['document']['pages'] if p['status']=='failed']
            if not requested:
                if 'error' in previous['document']:requested=pages
                else:raise KBError('NOTHING_TO_RETRY','No failed pages; use a new configuration to improve partial or needs_ocr results.')
        try:
            if adapter=='pdf':document=parse_pdf(path,asset_id=asset_id,options=options,work_dir=config.output_root,pages=requested)
            elif adapter=='cache':
                document=parse_cache(cache_path,original_path=path,asset_id=asset_id,pages=requested)
                degraded=[p['page'] for p in document['pages'] if p['status']!='usable']
                if degraded:
                    fallback=parse_pdf(path,asset_id=asset_id,options=options,work_dir=config.output_root,pages=degraded)
                    cache_issues={p['page']:p['issues'] for p in document['pages']}
                    for page in fallback['pages']:
                        page['issues'].extend(cache_issues[page['page']])
                        page['issues'].append({'code':'CACHE_REPARSED','message':'Cached text had incomplete location or quality; reparsed the preserved original.'})
                    document['pages']=sorted([p for p in document['pages'] if p['page'] not in degraded]+fallback['pages'],key=lambda p:p['page'])
            else:document=parse_word(path,asset_id=asset_id)
            if previous and 'error' not in previous['document']:
                if previous['document']['page_count']!=document['page_count']:raise KBError('PARSE_CONTRACT','Page count changed during retry.')
                preserved=[p for p in previous['document']['pages'] if p['status']!='failed']
                reused_pages=[p['page'] for p in preserved]
                document['pages']=sorted(preserved+document['pages'],key=lambda p:p['page'] or 0)
            document=isolate_invalid_pages(document,asset_id,pages)
            validate_document(document,asset_id,pages)
        except (KBError,OSError,ValueError) as exc:
            document={'page_count':None,'pages':[],'error':{'code':getattr(exc,'code','PARSER_ERROR'),
                'message':str(exc) if isinstance(exc,KBError) else 'Parser could not read the preserved source.'}}
    if file_hash(path)!=asset_id:raise KBError('SOURCE_CHANGED','Preserved source changed while parsing.')
    attempt_id=str(uuid4());failed='error' in document or any(p['status']=='failed' for p in document['pages'])
    output_hash=digest(document);parse_id=None;state=quality(document)
    status='failed' if failed else ('duplicate' if duplicate else 'completed' if state=='usable' else 'completed_with_issues')
    report={'attempt_id':attempt_id,'asset_id':asset_id,'parser_version':parser_version,'engine':engine,
        'contract_version':CONTRACT_VERSION,'config_hash':config_hash,'runtime_options':options,'requested_pages':pages,
        'cache_source':cache_source,'cache_asset_id':cache_id,'retry_of':str(retry_attempt) if retry_attempt else None,
        'status':status,'quality':state,'output_hash':output_hash,'reused_pages':reused_pages,'document':document}
    report_key=f'parsing/reports/{attempt_id}.json'
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(%s)',(int(digest([asset_id,config_hash])[:15],16),))
        if not failed:
            parse_id=register_parse(conn,asset_id,parser_version,config_hash,output_hash,state)
            ordinal=0
            for page in document['pages']:
                for block in page['blocks']:
                    block_id=identity('block',parse_id,block['structural_key'])
                    locator=block['locator']|{'parser_method':page['method'],'page_quality':page['status'],
                        'recognition_confidence':block.get('confidence'),'metadata':block.get('metadata',{})}
                    insert(conn,'content_block',{'block_id':block_id,'parse_id':parse_id,'structural_key':block['structural_key'],
                        'parent_id':identity('block',parse_id,block['parent_key']) if block.get('parent_key') else None,
                        'kind':block['kind'],'ordinal':ordinal,'text_raw':block['text_raw'],'text_search':block['text_raw'],'locator':locator})
                    ordinal+=1
        report['parse_id']=parse_id
        report_path=stored_path(config,report_key);write_json(report_path,report)
        insert(conn,'parse_attempt',{'attempt_id':attempt_id,'asset_id':asset_id,'parser_version':parser_version,'config_hash':config_hash,
            'parse_id':parse_id,'retry_of':retry_attempt,'status':status,'report_key':report_key,'report_sha256':file_hash(report_path)})
    return {k:v for k,v in report.items() if k not in {'runtime_options','document'}}|{'report_key':report_key,
        'page_count':document['page_count'],'processed_pages':len(document['pages']),
        'failed_pages':[p['page'] for p in document['pages'] if p['status']=='failed'],
        'block_count':sum(len(p['blocks']) for p in document['pages']),'error':document.get('error')}


def compare_parses(conn,left,right):
    runs=conn.execute('SELECT parse_id,asset_id FROM parse_run WHERE parse_id IN (%s,%s)',(left,right)).fetchall()
    if len({str(r['parse_id']) for r in runs})!=len({left,right}) or len({r['asset_id'] for r in runs})!=1:
        raise KBError('PARSE_COMPARE','Compare two existing parses of the same source asset.')
    def blocks(pid):
        return {r['structural_key']:r for r in conn.execute('SELECT structural_key,text_raw,locator FROM content_block WHERE parse_id=%s',(pid,)).fetchall()}
    a=blocks(left);b=blocks(right)
    return {'left':left,'right':right,'added':sorted(b.keys()-a.keys()),'removed':sorted(a.keys()-b.keys()),
        'changed':[k for k in sorted(a.keys()&b.keys()) if a[k]!=b[k]],'unchanged':sum(a[k]==b[k] for k in a.keys()&b.keys())}
