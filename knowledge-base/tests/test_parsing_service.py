import pytest
import psycopg

from food_kb.common import KBError
from food_kb.db.session import insert
from food_kb.parsing.contracts import validate_document
from food_kb.parsing.service import parse_asset,read_attempt,compare_parses
from food_kb.storage.assets import preserve


def page(asset,number=1,status='usable',text='SYNTHETIC source'):
    return {'page':number,'width':600,'height':800,'printed_page':None,'status':status,'method':'pdf_text',
        'blocks':[{'structural_key':f'p{number}/b0','parent_key':None,'kind':'text','ordinal':0,'text_raw':text,
            'locator':{'asset_id':asset,'pdf_page':number,'printed_page':None,'bbox':[10,10,90,30],
                'coordinate_system':'pdf_points_top_left'},'confidence':None,'metadata':{}}] if status!='failed' else [],
        'issues':[{'code':'OCR_TIMEOUT','message':'SYNTHETIC timeout'}] if status=='failed' else [],'image':None}


@pytest.fixture
def parsing_source(config,conn,monkeypatch):
    from food_kb.parsing import pdf
    # The adapter is mocked in orchestration tests; real PDFs are tested in test_parsing_pdf.
    p=config.source('files/source.pdf');p.write_bytes(b'%PDF-SYNTHETIC adapter fixture')
    asset=preserve(config,'files/source.pdf');insert(conn,'source_asset',asset)
    monkeypatch.setattr(pdf,'describe_engine',lambda options:{'adapter':'synthetic-test-engine'})
    return asset['asset_id']


def test_parse_retry_reuses_only_successful_pages(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    calls=[]
    def fake(path,*,asset_id,options,work_dir,pages):
        calls.append(pages)
        if len(calls)==1:return {'page_count':2,'pages':[page(asset_id),page(asset_id,2,'failed')]}
        assert pages==[2]
        return {'page_count':2,'pages':[page(asset_id,2)]}
    monkeypatch.setattr(pdf,'parse_pdf',fake)
    first=parse_asset(conn,config,parsing_source)
    assert first['status']=='failed' and first['parse_id'] is None and first['failed_pages']==[2]
    assert conn.execute('SELECT count(*) AS n FROM parse_run').fetchone()['n']==0
    second=parse_asset(conn,config,retry_attempt=first['attempt_id'])
    assert second['status']=='completed' and second['reused_pages']==[1]
    third=parse_asset(conn,config,parsing_source)
    assert third['status']=='duplicate' and third['parse_id']==second['parse_id'] and len(calls)==2
    assert conn.execute('SELECT count(*) AS n FROM parse_attempt').fetchone()['n']==3
    assert conn.execute('SELECT count(*) AS n FROM content_block').fetchone()['n']==2
    assert read_attempt(conn,config,first['attempt_id'])['document']['pages'][1]['status']=='failed'


def test_new_configuration_keeps_old_blocks_and_compares(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    def fake(path,*,asset_id,options,work_dir,pages):
        return {'page_count':1,'pages':[page(asset_id,text=f"SYNTHETIC dpi {options['dpi']}")]}
    monkeypatch.setattr(pdf,'parse_pdf',fake)
    first=parse_asset(conn,config,parsing_source,options={'dpi':200})
    second=parse_asset(conn,config,parsing_source,options={'dpi':300})
    assert first['parse_id']!=second['parse_id']
    diff=compare_parses(conn,first['parse_id'],second['parse_id'])
    assert diff['changed']==['p1/b0']
    old=conn.execute('SELECT text_raw FROM content_block WHERE parse_id=%s',(first['parse_id'],)).fetchone()
    assert old['text_raw']=='SYNTHETIC dpi 200'
    with pytest.raises(psycopg.Error):conn.execute('DELETE FROM parse_attempt WHERE attempt_id=%s',(first['attempt_id'],))


def test_parser_engine_change_rejects_retry(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source,status='failed')]})
    first=parse_asset(conn,config,parsing_source)
    monkeypatch.setattr(pdf,'describe_engine',lambda options:{'adapter':'changed-engine'})
    with pytest.raises(KBError,match='changed'):parse_asset(conn,config,retry_attempt=first['attempt_id'])


def test_corrupted_parse_report_cannot_supply_retry_pages(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source,status='failed')]})
    result=parse_asset(conn,config,parsing_source)
    (config.output_root/result['report_key']).write_text('{}')
    with pytest.raises(KBError,match='changed'):parse_asset(conn,config,retry_attempt=result['attempt_id'])


def test_corrupted_original_cannot_be_reused(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source)]})
    parse_asset(conn,config,parsing_source)
    (config.output_root/'objects'/parsing_source[:2]/parsing_source).write_bytes(b'changed')
    with pytest.raises(KBError,match='fingerprint'):parse_asset(conn,config,parsing_source)


def test_global_parser_error_is_recorded_and_retryable(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf
    def fail(*a,**kw):raise KBError('PDF_READ_FAILED','Controlled read failure')
    monkeypatch.setattr(pdf,'parse_pdf',fail)
    first=parse_asset(conn,config,parsing_source)
    assert first['status']=='failed' and first['error']['code']=='PDF_READ_FAILED'
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source)]})
    assert parse_asset(conn,config,retry_attempt=first['attempt_id'])['status']=='completed'


@pytest.mark.parametrize('fault',['missing_page','wrong_asset','outside_bbox','nan_bbox','wrong_page','fake_word_page','empty_usable','nul_text','parent_missing'])
def test_bad_layout_contract_is_rejected(fault):
    document={'page_count':1,'pages':[page('abc')]}
    p=document['pages'][0];b=p['blocks'][0]
    if fault=='missing_page':document['pages']=[]
    elif fault=='wrong_asset':b['locator']['asset_id']='other'
    elif fault=='outside_bbox':b['locator']['bbox']=[0,0,900,900]
    elif fault=='nan_bbox':b['locator']['bbox'][0]=float('nan')
    elif fault=='wrong_page':b['locator']['pdf_page']=2
    elif fault=='fake_word_page':document['page_count']=None;p['page']=None
    elif fault=='empty_usable':b['text_raw']=''
    elif fault=='nul_text':b['text_raw']='bad\x00text'
    elif fault=='parent_missing':b['parent_key']='missing'
    with pytest.raises(KBError):validate_document(document,'abc')


def test_page_only_locations_must_declare_partial():
    doc={'page_count':1,'pages':[page('abc')]}
    doc['pages'][0]['blocks'][0]['locator']['bbox']=None
    with pytest.raises(KBError):validate_document(doc,'abc')
    doc['pages'][0]['status']='partial'
    assert validate_document(doc,'abc') is doc


@pytest.mark.parametrize('fault',['bad_bbox','missing_locator','missing_page'])
def test_malformed_page_keeps_good_page_for_retry(config,conn,monkeypatch,parsing_source,fault):
    from food_kb.parsing import pdf
    calls=[]
    def fake(*a,**kw):
        calls.append(kw['pages'])
        if len(calls)>1:
            assert kw['pages']==[2]
            return {'page_count':2,'pages':[page(parsing_source,2)]}
        bad=page(parsing_source,2)
        if fault=='bad_bbox':bad['blocks'][0]['locator']['bbox']=[0,0,9999,9999]
        if fault=='missing_locator':del bad['blocks'][0]['locator']
        return {'page_count':2,'pages':[page(parsing_source)]+([] if fault=='missing_page' else [bad])}
    monkeypatch.setattr(pdf,'parse_pdf',fake)
    first=parse_asset(conn,config,parsing_source)
    assert first['failed_pages']==[2] and first['processed_pages']==2
    report=read_attempt(conn,config,first['attempt_id'])
    assert report['document']['pages'][0]['blocks'][0]['text_raw']=='SYNTHETIC source'
    assert parse_asset(conn,config,retry_attempt=first['attempt_id'])['reused_pages']==[1]


def test_contract_rejects_nonexistent_actual_page():
    with pytest.raises(KBError,match='does not exist'):
        validate_document({'page_count':1,'pages':[page('abc',2)]},'abc',[2])


def test_cache_retry_uses_preserved_bytes_after_source_disappears(config,conn,monkeypatch,parsing_source):
    from food_kb.parsing import pdf,legacy
    source=config.source('files/cache.json');source.write_text('{"synthetic":true}')
    def cached(*a,**kw):
        p=page(parsing_source);p['status']='partial';p['issues']=[{'code':'cache_page_missing','message':'SYNTHETIC missing cache'}]
        return {'page_count':1,'pages':[p]}
    monkeypatch.setattr(legacy,'parse_cache',cached)
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source,status='failed')]})
    first=parse_asset(conn,config,parsing_source,cache_source='files/cache.json')
    report=read_attempt(conn,config,first['attempt_id'])
    assert any(i['code']=='cache_page_missing' for i in report['document']['pages'][0]['issues'])
    source.unlink()
    monkeypatch.setattr(pdf,'parse_pdf',lambda *a,**kw:{'page_count':1,'pages':[page(parsing_source)]})
    assert parse_asset(conn,config,retry_attempt=first['attempt_id'])['status']=='completed'
