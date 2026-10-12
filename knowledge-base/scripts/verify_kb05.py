"""Bounded real-source parsing acceptance; preserves originals and previous evidence."""
import argparse
from collections import Counter
from pathlib import Path

from food_kb.common import file_hash,write_json
from food_kb.config import Config
from food_kb.db.session import connect,migrate
from food_kb.parsing.service import parse_asset,read_attempt,compare_parses


def verify(config,out,tessdata):
    out=Path(out)
    if out.exists():raise ValueError('Use a new evidence output directory.')
    out.mkdir(parents=True)
    root=config.source_root
    cases=[{'id':'guide-cross-page','source':'references/国家食品安全抽检实施细则（2026年版 上册）.pdf',
        'pages':[5,6,7,257,258],'cache_source':'food-classifier/resources/guide-pages.json'},
        {'id':'real-scan','source':'references/食品标准资料库/标准原文/GB 23200.108-2018_食品安全国家标准 植物源性食品中草铵膦残留量的测定 液相色谱-质谱联用法_73078BBE.pdf','pages':None},
        {'id':'font-decoding','source':'references/食品标准资料库/标准原文/GB 10133-2014_食品安全国家标准 水产调味品_766451C8.pdf','pages':None}]
    for p in sorted((root/'references/食品标准资料库/标准原文').glob('*.docx')):
        cases.append({'id':f'word-{len(cases)-2}','source':p.relative_to(root).as_posix(),'pages':None})
    for case in cases:case['asset_id']=file_hash(config.source(case['source']))
    write_json(out/'samples.json',cases)
    options={'ocr':'auto','dpi':200,'language':'chi_sim','tessdata':str(Path(tessdata).resolve()),'timeout_seconds':60}
    results=[];checks=[]
    with connect(config) as conn:
        write_json(out/'migrations.json',migrate(conn))
        for case in cases:
            one=parse_asset(conn,config,case['asset_id'],pages=case['pages'],options=options,cache_source=case.get('cache_source'))
            report=read_attempt(conn,config,one['attempt_id'])
            write_json(out/f"{case['id']}-report.json",report)
            write_json(out/f"{case['id']}-summary.json",one)
            print(case['id'],one['status'],one['processed_pages'],one['block_count'],one['error'],flush=True)
            # Explicitly unsupported or failed pages must not be disguised by the acceptance summary.
            if one['status']=='failed':
                write_json(out/'failed-case.json',{'case':case,'result':one})
                raise AssertionError(f"Unexpected parse failure: {case['id']}")
            before=conn.execute('SELECT (SELECT count(*) FROM parse_run) AS runs,(SELECT count(*) FROM content_block) AS blocks').fetchone()
            repeated=parse_asset(conn,config,case['asset_id'],pages=case['pages'],options=options,cache_source=case.get('cache_source'))
            after=conn.execute('SELECT (SELECT count(*) FROM parse_run) AS runs,(SELECT count(*) FROM content_block) AS blocks').fetchone()
            assert before==after and one['parse_id']==repeated['parse_id'] and repeated['status']=='duplicate'
            assert file_hash(config.source(case['source']))==case['asset_id']
            summary=one|{'id':case['id'],'repeat':repeated,'page_states':dict(Counter(p['status'] for p in report['document']['pages'])),
                'methods':dict(Counter(p['method'] for p in report['document']['pages']))}
            results.append(summary)
        # Actual page errors and unsupported Word are negative acceptance cases with preserved diagnostics.
        missing=parse_asset(conn,config,cases[0]['asset_id'],pages=[9999],options=options)
        assert missing['status']=='failed' and missing['parse_id'] is None
        doc=next((root/'references/食品标准资料库/标准原文').glob('*.doc'))
        unsupported=parse_asset(conn,config,file_hash(doc),options=options)
        assert unsupported['status']=='failed' and unsupported['error']['code']=='UNSUPPORTED_WORD_FORMAT'
        scan=cases[1]
        off=parse_asset(conn,config,scan['asset_id'],pages=[3],options=options|{'ocr':'off'})
        off_report=read_attempt(conn,config,off['attempt_id'])
        assert off_report['document']['pages'][0]['status']=='needs_ocr'
        alternate=parse_asset(conn,config,scan['asset_id'],pages=[3],options=options|{'dpi':300})
        assert alternate['parse_id'] and alternate['parse_id']!=off['parse_id']
        difference=compare_parses(conn,off['parse_id'],alternate['parse_id'])
        write_json(out/'parse-diff.json',difference)
        write_json(out/'negative-cases.json',{'missing_page':missing,'unsupported_word':unsupported,'ocr_disabled':off})
        checks=[{'check':'real_sources_have_no_execution_failures','passed':all(x['status']!='failed' for x in results)},
            {'check':'real_repeat_idempotent','passed':all(x['repeat']['parse_id']==x['parse_id'] for x in results)},
            {'check':'originals_unchanged','passed':all(file_hash(config.source(c['source']))==c['asset_id'] for c in cases)},
            {'check':'negative_cases_fail_explicitly','passed':True},
            {'check':'new_configuration_preserves_old_parse','passed':bool(difference['added'] or difference['removed'] or difference['changed'])},
            {'check':'no_expert_approval_created','passed':conn.execute("SELECT count(*) AS n FROM review_record WHERE action='approved'").fetchone()['n']==0}]
        assert all(x['passed'] for x in checks)
        write_json(out/'checks.json',checks)
        result={'status':'engineering_passed','cases':len(cases),'results':results,'checks':checks,
            'scope':'Selected real documents and pages; not full-library parsing or expert OCR accuracy acceptance.',
            'human_scan_spot_check':'pending','human_expert_approvals':0}
        write_json(out/'result.json',result)
        return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--output',required=True);p.add_argument('--tessdata',required=True)
    args=p.parse_args()
    result=verify(Config.load(args.config),args.output,args.tessdata)
    print({k:v for k,v in result.items() if k not in {'results','checks'}})
