from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path

import psycopg
from psycopg import sql
import pytest

from food_kb.common import read_json,write_json,file_hash
from food_kb.db.session import connect
from food_kb.ingest.importer import Importer
from food_kb.ingest.inventory import CATALOG


def count(conn,table):return conn.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']


def test_dry_run_has_no_database_or_file_side_effect(config):
    before=file_hash(config.source(CATALOG))
    r=Importer(config,dry_run=True).run([CATALOG])
    assert r['counts']['success']==1 and not config.output_root.exists()
    assert file_hash(config.source(CATALOG))==before


def test_repeat_import_retains_identity_and_records_attempt(config,conn):
    first=Importer(config,conn).run([CATALOG]);counts={t:count(conn,t) for t in ('source_asset','source_document','document_version','legacy_record','document_claim')}
    second=Importer(config,conn).run([CATALOG])
    assert second['counts']['duplicate']==1
    assert first['job_id']!=second['job_id'] and count(conn,'ingest_job')==2
    assert counts=={t:count(conn,t) for t in counts}
    assert sum(second['counts'].values())==second['total']


def test_same_file_multiple_origins_and_multiple_files_one_version(config,conn):
    data=read_json(config.source(CATALOG));second=config.source('files/copy.txt');second.write_bytes(config.source('files/original.txt').read_bytes())
    third=config.source('files/scan2.txt');third.write_text('SYNTHETIC alternative scan')
    data[0]['attachments'] += [{'path':'files/copy.txt','sha256':file_hash(second)},{'path':'files/scan2.txt','sha256':file_hash(third)}]
    write_json(config.source(CATALOG),data);r=Importer(config,conn).run([CATALOG])
    assert r['counts']['success']==1 and count(conn,'version_asset')==2
    assert count(conn,'source_registration')==3


def test_hash_tampering_is_conflict(config,conn):
    config.source('files/original.txt').write_text('tampered')
    r=Importer(config,conn).run([CATALOG]);assert r['counts']['identity_conflict']==1
    assert count(conn,'version_asset')==0


def test_metadata_conflict_does_not_overwrite_first(config,conn):
    data=read_json(config.source(CATALOG));data.append(data[0]|{'id':'other','title':'Conflicting title'})
    write_json(config.source(CATALOG),data);r=Importer(config,conn).run([CATALOG])
    assert r['counts']['success']==1 and r['counts']['identity_conflict']==1
    assert count(conn,'document_version')==1


def test_draft_cannot_be_formal(config,conn):
    import pymupdf
    p=config.source('files/draft.pdf')
    doc=pymupdf.open();page=doc.new_page();page.insert_text((50,60),'报批稿',fontname='china-s');doc.save(p);doc.close()
    data=read_json(config.source(CATALOG));data[0]['attachments']=[{'path':'files/draft.pdf','sha256':file_hash(p)}]
    write_json(config.source(CATALOG),data);r=Importer(config,conn).run([CATALOG])
    assert r['counts']['quarantined']==1
    assert conn.execute('SELECT role FROM version_asset').fetchone()['role']=='draft'


def test_record_failure_rolls_back_and_next_record_continues(config,conn):
    data=read_json(config.source(CATALOG));bad=data[0]|{'id':'bad','code':'SYNTH 9','family':'SYNTH 9','publication_date':'not-a-date'}
    write_json(config.source(CATALOG),[bad,data[0]])
    r=Importer(config,conn).run([CATALOG]);assert r['counts']['failed']==1 and r['counts']['success']==1
    assert count(conn,'legacy_record')==2 and count(conn,'source_document')==1


def test_unknown_fields_preserved_and_accounted(config,conn):
    data=read_json(config.source(CATALOG));data[0]['future_field']={'raw':'untouched'};write_json(config.source(CATALOG),data)
    r=Importer(config,conn).run([CATALOG]);assert r['counts']['unrecognized']==1
    raw=conn.execute('SELECT raw_payload,unknown_fields FROM legacy_record').fetchone()
    assert raw['raw_payload']['future_field']=={'raw':'untouched'} and raw['unknown_fields']==['future_field']


def test_metadata_correction_appends_version(config,conn):
    Importer(config,conn).run([CATALOG]);old=conn.execute('SELECT version_id FROM document_version').fetchone()['version_id']
    data=read_json(config.source(CATALOG));data[0]['publication_date']='2020-01-02';write_json(config.source(CATALOG),data)
    Importer(config,conn).run([CATALOG]);assert count(conn,'document_version')==2
    assert conn.execute('SELECT publication_date FROM document_version WHERE version_id=%s',(old,)).fetchone()['publication_date'].isoformat()=='2020-01-01'


def test_concurrent_same_batch(config,conn):
    schema=conn.execute('SELECT current_schema() AS s').fetchone()['s']
    def work():
        with connect(config) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            return Importer(config,c).run([CATALOG])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:work(),range(2)))
    assert count(conn,'document_version')==1 and count(conn,'legacy_record')==1 and count(conn,'ingest_job')==2
    assert sum(r['counts']['success'] for r in results)==1 and sum(r['counts']['duplicate'] for r in results)==1


def test_null_codepoint_preserved_losslessly_and_quarantined(config,conn):
    source='files/标准逐层引用关系.json'
    raw={'source_id':'synthetic-v1','source_code':'SYNTH 1-2020','target_code':'SYNTH 2','excerpt':'SYNTHETIC\u0000text'}
    write_json(config.source(source),[raw])
    result=Importer(config,conn).run([source])
    assert result['counts']['quarantined']==1 and result['counts']['failed']==0
    assert conn.execute('SELECT raw_payload FROM legacy_record').fetchone()['raw_payload']==raw
    assert count(conn,'knowledge_record')==0


def test_attempt_fingerprint_includes_original_bytes(config,conn):
    data=read_json(config.source(CATALOG));data[0]['attachments'][0].pop('sha256');write_json(config.source(CATALOG),data)
    first=Importer(config,conn).run([CATALOG]);config.source('files/original.txt').write_text('SYNTHETIC new bytes')
    second=Importer(config,conn).run([CATALOG])
    assert first['input_fingerprint']!=second['input_fingerprint']
    assert second['counts']['success']==1 and count(conn,'version_asset')==2


def test_business_json_cannot_enter_standard_domain(config,conn):
    write_json(config.source('files/business.json'),{'meta':{},'samples':[{'result':'合格'}]})
    result=Importer(config,conn).run(['files/business.json'])
    assert result['counts']['failed']==1 and result['items'][0]['error_code']=='UNSUPPORTED_SOURCE'
    assert count(conn,'source_asset')==0 and count(conn,'knowledge_record')==0


def test_trace_returns_real_json_pointer_and_asset(config,conn):
    from food_kb.cli import trace
    Importer(config,conn).run([CATALOG])
    rows=trace(conn,config,'synthetic-v1')
    assert len(rows)==1 and rows[0]['json_pointer']=='/0'
    assert rows[0]['assets'][0]['preserved_file_valid'] is True


def test_malformed_file_can_be_retried_without_duplicate_good_rows(config,conn):
    source='files/标准逐层引用关系.json';config.source(source).write_text('[{"unfinished":')
    first=Importer(config,conn).run([source]);assert first['counts']['failed']==1
    key=conn.execute('SELECT item_key FROM ingest_result WHERE job_id=%s',(first['job_id'],)).fetchone()['item_key']
    write_json(config.source(source),[{'source_id':'synthetic-v1','target_code':'SYNTH 2','excerpt':'SYNTHETIC'}])
    second=Importer(config,conn).run([source],{key});assert second['counts']['success']==1
    third=Importer(config,conn).run([source]);assert third['counts']['duplicate']==1


def test_wrong_file_signature_is_quarantined(config,conn):
    p=config.source('files/not-pdf.pdf');p.write_text('this is not a PDF')
    data=read_json(config.source(CATALOG));data[0]['attachments']=[{'path':'files/not-pdf.pdf','sha256':file_hash(p)}]
    write_json(config.source(CATALOG),data)
    r=Importer(config,conn).run([CATALOG]);assert r['counts']['quarantined']==1
    assert count(conn,'version_asset')==0


def test_source_change_after_fingerprinting_is_explicit_failure(config,conn,monkeypatch):
    import food_kb.ingest.importer as module
    original=module.inspect_file
    data=read_json(config.source(CATALOG));data[0]['attachments'][0].pop('sha256');write_json(config.source(CATALOG),data)
    def changing(path,expected=None):
        if path.name=='original.txt':path.write_text('SYNTHETIC changed during import')
        return original(path,expected)
    monkeypatch.setattr(module,'inspect_file',changing)
    report=Importer(config,conn).run([CATALOG])
    assert report['counts']['failed']==1 and report['items'][0]['error_code']=='SOURCE_CHANGED'
    assert report['items'][0]['retryable'] is True and count(conn,'version_asset')==0


@pytest.mark.parametrize('bad_parent',[False,True])
def test_category_import_is_order_independent_and_failure_is_local(config,conn,bad_parent):
    from food_kb.ingest.inventory import LIBRARY
    source=LIBRARY+'/分类体系/synthetic.json'
    data={'namespace':'SYNTHETIC','standard':'SYNTH 1-2020','title':'SYNTHETIC classification',
        'source':'files/original.txt','sha256':file_hash(config.source('files/original.txt')),
        'coded_nodes':[{'id':'child','classification_code':'1.1','label':'child','parent_id':'missing' if bad_parent else 'parent'},
                       {'id':'parent','classification_code':'1','label':'parent','parent_id':None}],
        'tables':[],'texts':[]}
    write_json(config.source(source),data);result=Importer(config,conn).run([source])
    assert result['counts']['failed']==int(bad_parent)
    assert count(conn,'category_node')==(1 if bad_parent else 2)
    assert count(conn,'legacy_record')==3
    assert count(conn,'knowledge_record')==(2 if bad_parent else 3)
