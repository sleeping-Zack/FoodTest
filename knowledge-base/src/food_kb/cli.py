import argparse
import json
import sys

import psycopg

from .common import KBError, file_hash, read_json
from .config import Config
from .db.session import connect, migrate
from .ingest.importer import Importer
from .ingest.inventory import inventory


def trace(conn, config, identifier):
    rows=conn.execute('''SELECT l.legacy_id,l.source_key,l.json_pointer,l.legacy_key,l.index_asset_id,
        l.adapter,l.importer_version,l.unknown_fields,k.record_id,k.record_type,k.version_id,
        k.review_state,dc.version_id AS catalog_version_id
        FROM legacy_record l LEFT JOIN legacy_knowledge lk USING(legacy_id)
        LEFT JOIN knowledge_record k ON k.record_id=lk.record_id
        LEFT JOIN document_claim dc ON dc.legacy_id=l.legacy_id
        WHERE l.legacy_id::text=%s OR k.record_id::text=%s OR l.legacy_key=%s''',(identifier,identifier,identifier)).fetchall()
    if not rows:raise KBError('NOT_FOUND','No registration or candidate matches that identifier.')
    for row in rows:
        version=row.get('version_id') or row.get('catalog_version_id')
        row['assets']=conn.execute('''SELECT a.*,va.role FROM source_asset a
            JOIN version_asset va USING(asset_id) WHERE va.version_id=%s''',(version,)).fetchall() if version else []
        for asset in row['assets']:
            path=config.output_root/asset['storage_key']
            asset['preserved_file_valid']=path.is_file() and file_hash(path)==asset['sha256']
        row['evidence']=conn.execute('''SELECT e.*,ke.role FROM evidence_anchor e
            JOIN knowledge_evidence ke USING(evidence_id) WHERE ke.record_id=%s''',(row.get('record_id'),)).fetchall()
    return rows


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(prog='food-kb',description='Local food standards registry; M1 imports candidates, not approved rules.')
    parser.add_argument('--config',help='Explicit JSON configuration file')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('validate-config');sub.add_parser('doctor');sub.add_parser('migrate')
    inv=sub.add_parser('inventory');inv.add_argument('--run',required=True);inv.add_argument('--previous')
    imp=sub.add_parser('import');imp.add_argument('--dry-run',action='store_true');imp.add_argument('--source',action='append');imp.add_argument('--retry-job')
    tr=sub.add_parser('trace');tr.add_argument('identifier')
    par=sub.add_parser('parse');par.add_argument('asset_id',nargs='?');par.add_argument('--pages',help='Comma-separated actual PDF page numbers')
    par.add_argument('--ocr',choices=['off','auto','force'],default='off');par.add_argument('--tessdata')
    par.add_argument('--dpi',type=int,default=200);par.add_argument('--language',default='chi_sim+eng')
    par.add_argument('--timeout-seconds',type=float,default=60);par.add_argument('--cache-source');par.add_argument('--retry-attempt')
    show=sub.add_parser('parse-report');show.add_argument('attempt_id')
    diff=sub.add_parser('parse-diff');diff.add_argument('left');diff.add_argument('right')
    chunk=sub.add_parser('chunk-manual');chunk.add_argument('directory')
    chunks=sub.add_parser('chunk-report');chunks.add_argument('run_id')
    chunk_diff=sub.add_parser('chunk-diff');chunk_diff.add_argument('left');chunk_diff.add_argument('right')
    context=sub.add_parser('expand-context');context.add_argument('run_id');context.add_argument('chunk_id')
    context.add_argument('--max-blocks',type=int,default=60);context.add_argument('--max-chars',type=int,default=24000)
    knowledge=sub.add_parser('extract-knowledge');knowledge.add_argument('chunk_run_id');knowledge.add_argument('--version-id')
    knowledge_report=sub.add_parser('knowledge-report');knowledge_report.add_argument('extraction_id')
    facts=sub.add_parser('knowledge-facts');facts.add_argument('extraction_id')
    facts.add_argument('--project');facts.add_argument('--basis');facts.add_argument('--record-type',default='requirement')
    facts.add_argument('--anchor')
    sub.add_parser('taxonomy-build')
    taxonomy_report=sub.add_parser('taxonomy-report');taxonomy_report.add_argument('snapshot_id')
    classify=sub.add_parser('classify-food');classify.add_argument('snapshot_id')
    classify_input=classify.add_mutually_exclusive_group(required=True)
    classify_input.add_argument('--name');classify_input.add_argument('--facts',help='Source-preserving fact object JSON; declarations remain hints.')
    classify.add_argument('--namespace',action='append')
    sub.add_parser('versions-build')
    versions_report=sub.add_parser('versions-report');versions_report.add_argument('snapshot_id')
    resolve=sub.add_parser('resolve-versions');resolve.add_argument('snapshot_id')
    resolve.add_argument('--context',required=True,help='Source-preserving dates JSON; date role and provenance are required.')
    resolve.add_argument('--document-id');resolve.add_argument('--version-id');resolve.add_argument('--facts')
    references=sub.add_parser('expand-references');references.add_argument('snapshot_id')
    references.add_argument('--version-id',action='append',required=True)
    references.add_argument('--mode',choices=['dependencies','candidates'],default='dependencies')
    references.add_argument('--max-depth',type=int,default=8);references.add_argument('--max-nodes',type=int,default=100)
    references.add_argument('--max-edges',type=int,default=200);references.add_argument('--facts')
    impact=sub.add_parser('versions-impact');impact.add_argument('left');impact.add_argument('right')
    impact.add_argument('--max-nodes',type=int,default=10000);impact.add_argument('--max-edges',type=int,default=50000)
    retrieval=sub.add_parser('retrieval-build');retrieval.add_argument('--context',required=True,help='JSON with four explicit source snapshot IDs.')
    retrieval_report=sub.add_parser('retrieval-report');retrieval_report.add_argument('generation_id')
    exact=sub.add_parser('query-exact');exact.add_argument('generation_id');exact.add_argument('--filters',required=True)
    lexical=sub.add_parser('query-lexical');lexical.add_argument('generation_id');lexical.add_argument('--query',required=True)
    lexical.add_argument('--mode',choices=['literal_raw','normalized_phrase','zh_ngram_phrase'],default='zh_ngram_phrase')
    lexical.add_argument('--filters')
    for query_parser in (exact,lexical):
        query_parser.add_argument('--confirmed-conditions');query_parser.add_argument('--limit',type=int,default=50)
        query_parser.add_argument('--offset',type=int,default=0)
    queue=sub.add_parser('knowledge-review-queue');queue.add_argument('--extraction-id')
    review=sub.add_parser('review-knowledge');review.add_argument('record_id')
    review.add_argument('--action',required=True,choices=['approved','rejected','supplement_requested','comment','disputed','withdrawn','reinstated','superseded'])
    review.add_argument('--actor',required=True);review.add_argument('--reason',required=True);review.add_argument('--evidence-id')
    revision=sub.add_parser('revise-knowledge');revision.add_argument('record_id');revision.add_argument('candidate_file')
    revision.add_argument('--actor',required=True);revision.add_argument('--reason',required=True)
    publish=sub.add_parser('publish-knowledge');publish.add_argument('--record-id',action='append',required=True)
    publish.add_argument('--actor',required=True);publish.add_argument('--parent-release-id')
    sub.add_parser('counts')
    args=parser.parse_args(argv)
    try:
        config=Config.load(args.config)
        if args.command=='validate-config':
            config.connection_args();result={'status':'valid','config_hash':config.fingerprint,'provider':config.provider}
        elif args.command=='inventory':
            if not args.run.replace('-','').replace('_','').isalnum():raise KBError('PATH_BOUNDARY','Invalid run identifier.')
            result=inventory(config,config.output_root/'inventories'/args.run,args.previous)
        elif args.command=='import' and args.dry_run:
            result=Importer(config,dry_run=True).run(args.source)
        else:
            with connect(config) as conn:
                if args.command=='doctor':
                    result={'database':'available','server_version':conn.info.server_version,'provider':'disabled',
                        'sources':{'allowed':len(config.allowed_sources),'missing':sum(not p.exists() for p in config.allowed_sources)},
                        'pgvector_available':bool(conn.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'").fetchone()),
                        'schema':conn.execute("SELECT to_regclass('schema_migration') IS NOT NULL AS ready").fetchone()['ready']}
                elif args.command=='migrate':result={'migrations':migrate(conn)}
                elif args.command=='import':
                    only=None
                    if args.retry_job:
                        only={x['item_key'] for x in conn.execute("SELECT item_key FROM ingest_result WHERE job_id=%s AND status NOT IN ('success','duplicate')",(args.retry_job,)).fetchall()}
                        if not only:raise KBError('NOTHING_TO_RETRY','No failed or unresolved record IDs in that job.')
                    result=Importer(config,conn).run(args.source,only)
                elif args.command=='trace':result=trace(conn,config,args.identifier)
                elif args.command=='parse':
                    from .parsing.service import parse_asset
                    if not args.asset_id and not args.retry_attempt:raise KBError('SOURCE_MISSING','Provide an asset ID or --retry-attempt.')
                    pages=[int(p.strip()) for p in args.pages.split(',')] if args.pages else None
                    result=parse_asset(conn,config,args.asset_id,pages=pages,cache_source=args.cache_source,retry_attempt=args.retry_attempt,
                        options={'ocr':args.ocr,'dpi':args.dpi,'language':args.language,'tessdata':args.tessdata,'timeout_seconds':args.timeout_seconds})
                elif args.command=='parse-report':
                    from .parsing.service import read_attempt
                    result=read_attempt(conn,config,args.attempt_id)
                elif args.command=='parse-diff':
                    from .parsing.service import compare_parses
                    result=compare_parses(conn,args.left,args.right)
                elif args.command=='chunk-manual':
                    from .chunking.service import chunk_manual
                    result=chunk_manual(conn,config,args.directory)
                elif args.command=='chunk-diff':
                    from .chunking.service import compare_runs
                    result=compare_runs(conn,config,args.left,args.right)
                elif args.command=='extract-knowledge':
                    from .knowledge.service import extract_knowledge
                    result=extract_knowledge(conn,config,args.chunk_run_id,version_id=args.version_id)
                elif args.command=='knowledge-report':
                    from .knowledge.service import read_extraction
                    result=read_extraction(conn,config,args.extraction_id)
                elif args.command=='knowledge-facts':
                    from .knowledge.service import read_extraction
                    from .knowledge.facts import query_facts
                    result=query_facts(read_extraction(conn,config,args.extraction_id),project=args.project,
                        basis=args.basis,record_type=args.record_type,anchor=args.anchor)
                elif args.command=='taxonomy-build':
                    from .taxonomy.repository import build_snapshot
                    result=build_snapshot(conn,config)
                elif args.command=='taxonomy-report':
                    from .taxonomy.repository import read_snapshot
                    result=read_snapshot(conn,args.snapshot_id)
                elif args.command=='classify-food':
                    from .taxonomy.repository import read_snapshot
                    from .taxonomy.classify import classify_food
                    input_facts=read_json(args.facts) if args.facts else {'food_name':{
                        'raw_value':args.name,'normalized_value':args.name,'type':'string',
                        'source_ref':'cli:--name','role':'sample_name','verification_state':'unverified'}}
                    result=classify_food(read_snapshot(conn,args.snapshot_id),input_facts,namespaces=args.namespace)
                elif args.command=='versions-build':
                    from .versions.repository import build_snapshot
                    result=build_snapshot(conn,config)
                elif args.command=='versions-report':
                    from .versions.repository import read_snapshot
                    result=read_snapshot(conn,args.snapshot_id)
                elif args.command=='resolve-versions':
                    from .versions.repository import read_snapshot
                    from .versions.resolve import resolve_versions
                    result=resolve_versions(read_snapshot(conn,args.snapshot_id),read_json(args.context),
                        document_id=args.document_id,version_id=args.version_id,
                        facts=read_json(args.facts) if args.facts else None)
                elif args.command=='expand-references':
                    from .versions.repository import read_snapshot
                    from .versions.references import expand_references
                    result=expand_references(read_snapshot(conn,args.snapshot_id),args.version_id,mode=args.mode,
                        max_depth=args.max_depth,max_nodes=args.max_nodes,max_edges=args.max_edges,
                        facts=read_json(args.facts) if args.facts else None)
                elif args.command=='versions-impact':
                    from .versions.repository import read_snapshot
                    from .versions.impact import impact_snapshots
                    result=impact_snapshots(read_snapshot(conn,args.left),read_snapshot(conn,args.right),
                        max_nodes=args.max_nodes,max_edges=args.max_edges)
                elif args.command=='retrieval-build':
                    from .retrieval.repository import build_generation
                    result=build_generation(conn,config,read_json(args.context))
                elif args.command=='retrieval-report':
                    from .retrieval.repository import read_generation
                    result=read_generation(conn,args.generation_id)
                elif args.command in {'query-exact','query-lexical'}:
                    from .retrieval.service import search
                    result=search(conn,args.generation_id,query=getattr(args,'query',None),
                        mode='exact' if args.command=='query-exact' else args.mode,
                        filters=read_json(args.filters) if args.filters else None,limit=args.limit,offset=args.offset,
                        confirmed_conditions=read_json(args.confirmed_conditions) if args.confirmed_conditions else None)
                elif args.command=='knowledge-review-queue':
                    from .knowledge.service import review_queue
                    result=review_queue(conn,args.extraction_id)
                elif args.command=='review-knowledge':
                    from .review.service import append_review
                    result=append_review(conn,args.record_id,args.action,args.actor,args.reason,args.evidence_id)
                elif args.command=='revise-knowledge':
                    from .knowledge.service import revise
                    result=revise(conn,config,args.record_id,read_json(args.candidate_file),actor=args.actor,reason=args.reason)
                elif args.command=='publish-knowledge':
                    from .releases.service import publish
                    result=publish(conn,args.record_id,args.actor,parent_release_id=args.parent_release_id)
                elif args.command in {'chunk-report','expand-context'}:
                    from .chunking.service import read_run
                    result=read_run(conn,config,args.run_id)
                    if args.command=='expand-context':
                        from .chunking.context import expand_context
                        result=expand_context(result,args.chunk_id,max_blocks=args.max_blocks,max_chars=args.max_chars)
                else:
                    tables=['source_document','document_version','source_asset','legacy_record','knowledge_record','category_node','relation','ingest_job']
                    result={t:conn.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in tables}
        if isinstance(result,dict) and 'items' in result:
            result={k:v for k,v in result.items() if k!='items'}
        print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
        if isinstance(result,dict) and result.get('status')=='failed':return 1
        return 2 if isinstance(result,dict) and result.get('status')=='completed_with_issues' else 0
    except (KBError,OSError,ValueError,psycopg.Error) as exc:
        code=getattr(exc,'code',None) or ('DATABASE_ERROR' if isinstance(exc,psycopg.Error) else 'CONFIG_OR_IO_ERROR')
        print(json.dumps({'status':'failed','error_code':code,'message':str(exc) if isinstance(exc,KBError) else 'Operation failed; inspect configuration and controlled evidence.'}),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
