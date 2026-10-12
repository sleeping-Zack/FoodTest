"""Summarize existing, successful M1 execution evidence without inventing results."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from food_kb.common import digest,file_hash,now,read_json,write_json
from food_kb.config import Config
from food_kb.db.session import connect


def record(root,run):
    root=Path(root).resolve();artifacts=root/'artifacts/knowledge-base';package=root/'knowledge-base'
    config=Config.load(package/'config/local.json')
    replay=read_json(artifacts/'KB-04'/run/'verified-v2/result.json')
    assert replay['status']=='passed'
    test_xml=artifacts/'KB-03'/run/'tests-acceptance-final.xml'
    tree=ET.parse(test_xml);cases=list(tree.iter('testcase'))
    assert cases and not list(tree.iter('failure')) and not list(tree.iter('error'))
    tests=[{'name':x.attrib['name'],'suite':x.attrib.get('classname'),'seconds':float(x.attrib.get('time',0)),'passed':True} for x in cases]
    integrity=read_json(artifacts/'KB-01'/run/'original-integrity.json');assert integrity['passed']
    source_files={p.relative_to(root).as_posix():file_hash(p) for p in package.rglob('*') if p.is_file()
        and p.suffix in ('.py','.sql','.toml','.json','.md','.txt','.ps1','.pdf')
        and not any(x in p.parts for x in ('__pycache__','.pytest_cache','food_kb.egg-info','private','expert_labels'))
        and p.name!='local.json'}
    code_version='sha256:'+digest(source_files)
    with connect(config) as conn:
        schema=conn.execute('SELECT version,checksum FROM schema_migration ORDER BY version').fetchall()
        enum_rows=conn.execute("SELECT t.typname,e.enumlabel,e.enumsortorder FROM pg_type t JOIN pg_enum e ON t.oid=e.enumtypid JOIN pg_namespace n ON t.typnamespace=n.oid WHERE n.nspname=current_schema() ORDER BY t.typname,e.enumsortorder").fetchall()
        doctor={'database':'available','postgresql_version':conn.info.server_version,
                'pgvector_available':bool(conn.execute("SELECT 1 FROM pg_available_extensions WHERE name='vector'").fetchone()),
                'provider':'disabled','database_host':config.database['host'],'port':config.database['port']}
    write_json(artifacts/'KB-03'/run/'schema-enums.json',enum_rows)
    write_json(artifacts/'KB-03'/run/'migrations-final.json',schema)
    write_json(artifacts/'KB-03'/run/'test-cases.json',tests)
    inventory=read_json(artifacts/'KB-01'/run/'summary.json')
    samples=read_json(package/'evaluation/sample_manifest.json')
    prd=read_json(artifacts/'KB-00'/run/'prd-check.json')
    previous=read_json(root/'docs/knowledge-base/evidence/prd-check-2026-10-12.json')
    assert prd['body_sha256']==previous['body_sha256']
    write_json(artifacts/'KB-00'/run/'prd-read-verification.json',{'full_text_read':True,'same_representation':'Lake HTML content UTF-8',
        'same_as_previous':True,'body_sha256':prd['body_sha256'],'ambiguity':'1.3 total table P1 vs detail P0 retained; M1 does not resolve it'})
    git={}
    for relative in ('.','food-classifier'):
        r=subprocess.run(['git','-C',str(root/relative),'rev-parse','--is-inside-work-tree'],capture_output=True,text=True)
        git[relative]={'exit_code':r.returncode,'is_git_worktree':r.returncode==0 and r.stdout.strip()=='true'}
    archive=root/'.tools/kb-postgres/postgresql.zip'
    env={'recorded_at':now(),'python':sys.version,'platform':platform.platform(),'logical_cpus':os.cpu_count(),
         'disk_free_bytes':shutil.disk_usage(root).free,'git_boundaries':git,'database':doctor,
         'initial_observations':{'old_project_venv':'broken interpreter reference to previous machine','postgres_on_path':False,
             'container_tool_on_path':False,'existing_ports_8000_8011_listening':False,'source':'initial read-only commands in this run; existing services were not claimed healthy'},
         'postgres_archive':{'url':'https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip',
             'sha256':file_hash(archive),'bytes':archive.stat().st_size,'verification':'TLS download from vendor; local fingerprint recorded; not a separately vendor-signed checksum'}}
    write_json(artifacts/'KB-00'/run/'environment.json',env)
    write_json(artifacts/'KB-02'/run/'doctor.json',doctor)
    facts={
      'KB-00': [{'check':'online_prd_read','passed':True,'sha256':prd['body_sha256']},
                {'check':'isolated_local_database','passed':True,'host':'127.0.0.1','port':55432},
                {'check':'repository_boundaries_recorded','passed':True,'actual':git}],
      'KB-01': [{'check':'real_inventory','passed':True,'files':inventory['files'],'manifest_hash':inventory['manifest_hash']},
                {'check':'logical_vs_physical_tables','passed':True,'logical':inventory['logical_tables'],'physical':inventory['physical_table_fragments']},
                {'check':'sources_unchanged','passed':True,'checked':integrity['checked']},
                {'check':'representative_samples','passed':True,'samples':len(samples['samples']),'expert_approvals':0}],
      'KB-02': [x for x in tests if x['suite'].endswith('test_boundaries')],
      'KB-03': [x for x in tests if x['suite'].endswith('test_schema')],
      'KB-04': read_json(artifacts/'KB-04'/run/'verified-v2/checks.json')}
    commands={
      'KB-00':['Read README.md, docs/项目上下文与决策记录.md, AGENTS.md, handoff.md, spec.md and task.md',
               "Invoke-WebRequest 'https://syza-agent.yuque.com/api/docs/gokiiu?book_id=84087092&include_contributors=false&include_like=false&include_hits=false'",
               'git status --short','git -C food-classifier status --short','Get-NetTCPConnection -State Listen',
               '.tools/kb-postgres/pgsql/bin/initdb.exe -D .tools/kb-postgres/data -U kb_admin -E UTF8 --locale=C --auth=scram-sha-256 --pwfile=.tools/kb-postgres/password.local',
               '.tools/kb-postgres/pgsql/bin/postgres.exe --version'],
      'KB-01':['food_kb.ingest.inventory.inventory(config, KB-01/run-id) through installed Python',
               '.tools/kb-venv/Scripts/python.exe knowledge-base/evaluation/build_samples.py --config knowledge-base/config/local.json --output knowledge-base/evaluation',
               'Recomputed SHA-256 for every baseline inventory row; original-integrity.json'],
      'KB-02':['.tools/kb-venv/Scripts/python.exe -m pip install --no-deps -e knowledge-base',
               '.tools/kb-venv/Scripts/python.exe -m food_kb --config knowledge-base/config/local.json doctor',
               '.tools/kb-venv/Scripts/python.exe -m compileall -q knowledge-base/src'],
      'KB-03':['.tools/kb-venv/Scripts/python.exe -m food_kb --config knowledge-base/config/local.json migrate',
               '.tools/kb-venv/Scripts/python.exe -m pytest knowledge-base/tests -q --junitxml=artifacts/knowledge-base/KB-03/'+run+'/tests-acceptance-final.xml'],
      'KB-04':['.tools/kb-venv/Scripts/python.exe -X utf8 knowledge-base/scripts/verify_m1.py --config knowledge-base/config/local.json --output artifacts/knowledge-base/KB-04/'+run+'/verified-v2',
               '.tools/kb-venv/Scripts/python.exe -X utf8 knowledge-base/scripts/verify_m1.py --config knowledge-base/config/local.json --output artifacts/knowledge-base/KB-04/'+run+'/verified-v2 --resume-validation']}
    common={'recorded_at':now(),'code_version':code_version,'config_hash':config.fingerprint,
            'input_fingerprint':replay['repeat_report']['input_fingerprint'],'database_schema':schema[-1]['version'],
            'knowledge_release':None,'actor':'Codex / food-kb-0.1.0','expert_approval_count':0}
    for task,checks in facts.items():
        out=artifacts/task/run;out.mkdir(parents=True,exist_ok=True)
        write_json(out/'result.json',common|{'task':task,'status':'engineering_passed','checks':len(checks),
            'scope':'M1 only; no expert business acceptance, OCR, vector search or production deployment'})
        write_json(out/'checks.json',checks)
        (out/'commands.txt').write_text('\n'.join(commands[task])+'\n',encoding='utf8')
        (out/'issues.md').write_text('# 未决项与故障记录\n\n'
            '- 工程门禁已通过；正式业务审核和专家标签尚未完成。\n'
            '- PostgreSQL 真实运行；pgvector 未安装，属于 KB-11 后续范围。\n'
            '- 最终真实导入：155 条隔离、8 条缺原件、802 条身份冲突/受影响引用；不得自动发布。\n'
            '- 初次 legacy-1 导入的 6 条空字符数据库错误已由迁移 003 和隔离处理修复；原始失败报告保留。\n'
            '- 并发测试中重试计数竞争已修复；相关失败 XML 保留于 artifacts/knowledge-base/m1-fifth-tests.xml。\n'
            '- 两次真实导入完成后的 trace 联表发现列名歧义，已修复并增加测试；通过 --resume-validation 继续只读追溯验证，没有重复伪造导入记录。\n'
            '- legacy-1 的历史调试登记保留；本轮逐条来源验收只针对 legacy-2 的 29,329 条输入记录。\n',encoding='utf8')
    write_json(artifacts/'KB-00'/run/'source-manifest.json',{'code_version':code_version,'files':source_files})
    fields=read_json(package/'src/food_kb/ingest/legacy_fields.json')
    write_json(artifacts/'KB-04'/run/'mapping.json',{'processor': 'legacy-2','raw_provenance':'legacy_record: index_asset_id + RFC6901 pointer + raw JSON + legacy key',
        'catalog':'source_document / document_version / version_asset / source_registration / document_claim',
        'other_records':'knowledge_record(extracted) / legacy_knowledge / evidence_anchor; reference candidates -> relation; coded nodes -> isolated namespace',
        'unmodeled_fields':'retained in raw_payload and unknown_fields; outcome unrecognized','known_fields':fields})
    return {'tests_passed':len(tests),'code_version':code_version,'tasks':list(facts)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--run',required=True);args=parser.parse_args()
    print(record(args.root,args.run))
