"""Real-data M1 replay; no synthetic results are substituted for PostgreSQL."""
import argparse
from pathlib import Path
import time
from datetime import datetime

from food_kb.cli import trace
from food_kb.common import digest,file_hash,read_json,write_json
from food_kb.config import Config
from food_kb.db.session import connect,migrate
from food_kb.ingest.importer import Importer,VERSION

TABLES=['source_document','document_version','source_asset','version_asset','source_registration','legacy_record',
        'document_claim','knowledge_record','knowledge_evidence','evidence_anchor','legacy_knowledge','category_namespace','category_node','relation']


def counts(conn):return {t:conn.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in TABLES}


def pointer(value,path):
    if path=='':return value
    for token in path[1:].split('/'):
        token=token.replace('~1','/').replace('~0','~')
        value=value[int(token)] if isinstance(value,list) else value[token]
    return value


def verify(config,output,resume=False):
    output=Path(output)
    if output.exists() and (not resume or (output/'result.json').exists()):raise ValueError('Use a new evidence directory, or resume an unfinished validation.')
    output.mkdir(parents=True,exist_ok=True)
    with connect(config) as conn:
        migrations=migrate(conn);write_json(output/('migrations-final.json' if resume else 'migrations.json'),migrations)
        initial=counts(conn);timings=[]
        for name in ('first','repeat'):
            if resume:
                result=read_json(output/f'import-{name}.json');snapshot=read_json(output/f'counts-{name}.json')
                timings.append((datetime.fromisoformat(result['finished_at'])-datetime.fromisoformat(result['started_at'])).total_seconds())
            else:
                started=time.monotonic();result=Importer(config,conn).run();timings.append(time.monotonic()-started)
                write_json(output/f'import-{name}.json',result)
                snapshot=counts(conn);write_json(output/f'counts-{name}.json',snapshot)
            print(name,result['counts'],flush=True)
            assert result['counts']['failed']==0, 'Unexpected import execution failure; inspect preserved report'
            assert result['total']==sum(result['counts'].values())
            if name=='first':first=snapshot
            else:assert snapshot==first, 'Repeated import changed immutable object counts'
        assert counts(conn)==snapshot,'Database changed since recorded replay; use fresh verification'
        checks=[{'check':'repeat_counts_stable','expected':first,'actual':snapshot,'passed':snapshot==first},
                {'check':'exclusive_outcomes','expected':result['total'],'actual':sum(result['counts'].values()),'passed':True}]
        bad=[];n=0;cache={}
        rows=conn.execute('SELECT l.*,a.storage_key FROM legacy_record l JOIN source_asset a ON a.asset_id=l.index_asset_id WHERE importer_version=%s',(VERSION,)).fetchall()
        for row in rows:
            key=row['index_asset_id']
            if key not in cache:cache[key]=read_json(config.output_root/row['storage_key'])
            if pointer(cache[key],row['json_pointer'])!=row['raw_payload']:bad.append(str(row['legacy_id']))
            n+=1
        checks.append({'check':'all_legacy_pointers_roundtrip','checked':n,'failures':bad,'passed':not bad})
        assert not bad
        asset_errors=[]
        assets=conn.execute('SELECT * FROM source_asset').fetchall()
        for asset in assets:
            path=config.output_root/asset['storage_key']
            if not path.is_file() or file_hash(path)!=asset['sha256']:asset_errors.append(asset['asset_id'])
        checks.append({'check':'all_preserved_asset_hashes','checked':len(assets),'failures':asset_errors,'passed':not asset_errors})
        assert not asset_errors
        row_trace=trace(conn,config,'G-P006-T01-R002')
        assert row_trace and all(x['evidence'] for x in row_trace)
        write_json(output/'trace-example.json',row_trace)
        approvals=conn.execute("SELECT count(*) AS n FROM review_record WHERE action='approved'").fetchone()['n']
        assert approvals==0
        checks.append({'check':'no_real_auto_approvals','expected':0,'actual':approvals,'passed':True})
        checks.append({'check':'trace_example','legacy_key':'G-P006-T01-R002','passed':True})
        write_json(output/'checks.json',checks)
        summary={'status':'passed','processor_version':VERSION,'schema_version':migrations[-1]['version'],'initial_counts':initial,
                 'final_counts':snapshot,'repeat_report':{k:v for k,v in result.items() if k!='items'},
                 'durations_seconds':timings,'pointer_checks':n,'asset_checks':len(assets),'expert_approvals':approvals,'resumed_validation':resume}
        write_json(output/'result.json',summary)
        return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);parser.add_argument('--resume-validation',action='store_true');args=parser.parse_args()
    result=verify(Config.load(args.config),args.output,args.resume_validation)
    print({k:v for k,v in result.items() if k not in ('initial_counts','repeat_report')})
