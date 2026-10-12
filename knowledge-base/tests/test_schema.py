import hashlib
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from food_kb.common import KBError,digest,identity
from food_kb.db.session import insert,migrate
from food_kb.db.repository import register_parse


def uid():return str(uuid4())


def base(conn,suffix='a'):
    sha=hashlib.sha256(suffix.encode()).hexdigest();doc=uid();version=uid()
    insert(conn,'source_asset',{'asset_id':sha,'sha256':sha,'media_type':'text/plain','size':1,'storage_key':f'objects/{sha[:2]}/{sha}'})
    insert(conn,'source_document',{'document_id':doc,'source_key':doc,'kind':'standard','title':'SYNTHETIC'})
    insert(conn,'document_version',{'version_id':version,'document_id':doc,'designation':'SYNTHETIC','revision':suffix,'title':'SYNTHETIC','identity_status':'verified'})
    insert(conn,'version_asset',{'version_id':version,'asset_id':sha,'role':'original'})
    return sha,doc,version


def knowledge(conn,version,synthetic=True):
    rid=uid();insert(conn,'knowledge_record',{'record_id':rid,'logical_key':rid,'revision':'1','record_type':'requirement',
        'version_id':version,'payload':{'synthetic':True},'extraction_run':'synthetic-test','synthetic':synthetic})
    return rid


def anchor(conn,asset,quality='synthetic',**extra):
    eid=uid();insert(conn,'evidence_anchor',{'evidence_id':eid,'asset_id':asset,'locator':{'pdf_page':1},'locator_quality':quality}|extra)
    return eid


def approve(conn,rid,eid,actor='synthetic-reviewer'):
    insert(conn,'reviewer',{'actor':actor,'identity_reference':'SYNTHETIC test fixture; not an expert','synthetic':True})
    insert(conn,'review_record',{'review_id':uid(),'record_id':rid,'action':'review_pending','actor':actor,'reason':'SYNTHETIC'})
    review=uid();insert(conn,'review_record',{'review_id':review,'record_id':rid,'action':'approved','actor':actor,'reason':'SYNTHETIC','evidence_id':eid})
    return review


def release(conn,rid,review):
    rel=uid();manifest={'knowledge':[rid],'bindings':[]}
    h=conn.execute("SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS h",(Jsonb(manifest),)).fetchone()['h']
    with conn.transaction():
        insert(conn,'kb_release',{'release_id':rel,'manifest_hash':h,'manifest':manifest,'state':'published','synthetic':True,'index_readiness':{'semantic':False}})
        insert(conn,'release_knowledge',{'release_id':rel,'record_id':rid,'review_id':review})
    return rel


def test_migration_idempotency(conn):
    first=migrate(conn);assert migrate(conn)==first and len(first)==12


def test_migration_checksum_mismatch_rejected(conn):
    conn.execute("UPDATE schema_migration SET checksum='bad' WHERE version='001_registry.sql'")
    with pytest.raises(KBError,match='checksum'):migrate(conn)


def test_ddl_transaction_rolls_back(conn):
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            conn.execute('CREATE TABLE synthetic_failed_migration (id int)');conn.execute('SELECT 1/0')
    assert conn.execute("SELECT to_regclass('synthetic_failed_migration') AS t").fetchone()['t'] is None


def test_parse_identity_includes_configuration_and_checks_retry(conn):
    a,_,_=base(conn)
    p=register_parse(conn,a,'parser-1','config-A','output-A','usable')
    assert p==register_parse(conn,a,'parser-1','config-A','output-A','usable')
    assert p!=register_parse(conn,a,'parser-1','config-B','output-B','usable')
    with pytest.raises(KBError,match='different'):register_parse(conn,a,'parser-1','config-A','changed','usable')


def test_same_version_two_scans_have_distinct_page_anchors(conn):
    a,_,v=base(conn);b=hashlib.sha256(b'other').hexdigest()
    insert(conn,'source_asset',{'asset_id':b,'sha256':b,'media_type':'application/pdf','size':1,'storage_key':f'objects/{b[:2]}/{b}'})
    insert(conn,'version_asset',{'version_id':v,'asset_id':b,'role':'original'})
    e1=anchor(conn,a);e2=anchor(conn,b)
    rows=conn.execute('SELECT e.evidence_id,va.version_id FROM evidence_anchor e JOIN version_asset va USING(asset_id)').fetchall()
    assert len(rows)==2 and {str(r['version_id']) for r in rows}=={v}


def test_dangling_asset_and_asset_deletion_rejected(conn):
    with pytest.raises(psycopg.Error):anchor(conn,'0'*64)
    a,_,_=base(conn);anchor(conn,a)
    with pytest.raises(psycopg.Error):conn.execute('DELETE FROM source_asset WHERE asset_id=%s',(a,))


def test_parse_asset_and_block_generation_must_match(conn):
    a,_,_=base(conn);b,_,_=base(conn,'b')
    p=register_parse(conn,a,'p','c','o','usable')
    with pytest.raises(psycopg.Error):anchor(conn,b,parse_id=p)
    block=uid();insert(conn,'content_block',{'block_id':block,'parse_id':p,'structural_key':'page/1','kind':'paragraph','ordinal':0,'text_raw':'SYNTHETIC quote','locator':{'pdf_page':1}})
    other=register_parse(conn,a,'p','d','o','usable')
    with pytest.raises(psycopg.Error):anchor(conn,a,parse_id=other,block_id=block)


def test_verified_quote_must_match_actual_block(conn):
    a,_,_=base(conn);p=register_parse(conn,a,'p','c','o','usable');block=uid()
    insert(conn,'content_block',{'block_id':block,'parse_id':p,'structural_key':'p1','kind':'paragraph','ordinal':0,'text_raw':'SYNTHETIC actual text','locator':{'pdf_page':1}})
    with pytest.raises(psycopg.Error):anchor(conn,a,'verified')
    q='invented text'
    with pytest.raises(psycopg.Error):anchor(conn,a,'verified',parse_id=p,block_id=block,quote=q,quote_hash=hashlib.sha256(q.encode()).hexdigest())
    q='actual text';assert anchor(conn,a,'verified',parse_id=p,block_id=block,quote=q,quote_hash=hashlib.sha256(q.encode()).hexdigest())


def test_cross_version_evidence_rejected(conn):
    a,_,v=base(conn);b,_,other=base(conn,'b');rid=knowledge(conn,v);ev=anchor(conn,b)
    with pytest.raises(psycopg.Error):insert(conn,'knowledge_evidence',{'record_id':rid,'evidence_id':ev,'version_id':v,'asset_id':b,'role':'direct'})
    with pytest.raises(psycopg.Error):insert(conn,'knowledge_evidence',{'record_id':rid,'evidence_id':ev,'version_id':other,'asset_id':b,'role':'direct'})


def test_category_parent_cannot_cross_namespace(conn):
    a,_,v=base(conn);b,_,v2=base(conn,'b');r1=knowledge(conn,v);r2=knowledge(conn,v2)
    for ns,version in [('A',v),('B',v2)]:insert(conn,'category_namespace',{'namespace':ns,'version_id':version})
    parent=uid();insert(conn,'category_node',{'category_id':parent,'namespace':'A','version_id':v,'code':'01','label':'SYNTHETIC','definition_record_id':r1})
    with pytest.raises(psycopg.Error):insert(conn,'category_node',{'category_id':uid(),'namespace':'B','version_id':v2,'code':'01.1','label':'SYNTHETIC','parent_id':parent,'definition_record_id':r2})


def test_approved_state_cannot_be_inserted_directly(conn):
    _,_,v=base(conn)
    with pytest.raises(psycopg.Error):insert(conn,'knowledge_record',{'record_id':uid(),'logical_key':'bad','revision':'1','record_type':'requirement','version_id':v,'payload':{},'extraction_run':'x','review_state':'approved'})


def test_no_reviewer_or_evidence_cannot_approve(conn):
    _,_,v=base(conn);rid=knowledge(conn,v)
    with pytest.raises(psycopg.Error):insert(conn,'review_record',{'review_id':uid(),'record_id':rid,'action':'approved','actor':'invented-expert','reason':'bad'})
    with pytest.raises(psycopg.Error):approve(conn,rid,None)


def test_review_cannot_skip_pending_or_change_approved_revision(conn):
    a,_,v=base(conn);rid=knowledge(conn,v);ev=anchor(conn,a)
    insert(conn,'reviewer',{'actor':'fixture','identity_reference':'SYNTHETIC','synthetic':True})
    with pytest.raises(psycopg.Error):insert(conn,'review_record',{'review_id':uid(),'record_id':rid,'action':'approved','actor':'fixture','reason':'bad'})
    insert(conn,'knowledge_evidence',{'record_id':rid,'evidence_id':ev,'version_id':v,'asset_id':a,'role':'direct'})
    approve(conn,rid,ev)
    with pytest.raises(psycopg.Error):conn.execute('UPDATE knowledge_record SET payload=%s WHERE record_id=%s',(Jsonb({'limit':'changed'}),rid))


def test_unreviewed_and_empty_releases_rejected(conn):
    _,_,v=base(conn);rid=knowledge(conn,v)
    with pytest.raises(psycopg.Error):release(conn,rid,uid())
    with pytest.raises(psycopg.Error):insert(conn,'kb_release',{'release_id':uid(),'manifest_hash':'bad','manifest':{},'state':'published','synthetic':True,'index_readiness':{}})


def test_historical_release_survives_new_version_and_withdrawal(conn):
    a,doc,v=base(conn);rid=knowledge(conn,v);ev=anchor(conn,a)
    insert(conn,'knowledge_evidence',{'record_id':rid,'evidence_id':ev,'version_id':v,'asset_id':a,'role':'direct'})
    review=approve(conn,rid,ev);rel=release(conn,rid,review)
    insert(conn,'document_version',{'version_id':uid(),'document_id':doc,'designation':'SYNTHETIC new','revision':'2','title':'SYNTHETIC'})
    insert(conn,'governance_event',{'event_id':uid(),'record_id':rid,'action':'withdrawn','actor':'synthetic-reviewer','reason':'SYNTHETIC withdrawal'})
    assert conn.execute('SELECT record_id FROM release_knowledge WHERE release_id=%s',(rel,)).fetchone()['record_id']==__import__('uuid').UUID(rid)
    # A new manifest with an additional field avoids the content-hash uniqueness shortcut.
    with pytest.raises(psycopg.Error):
        with conn.transaction():
            rel2=uid();manifest={'knowledge':[rid],'bindings':[],'generation':2}
            h=conn.execute("SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex') AS h",(Jsonb(manifest),)).fetchone()['h']
            insert(conn,'kb_release',{'release_id':rel2,'manifest_hash':h,'manifest':manifest,'state':'published','synthetic':True,'index_readiness':{}})
            insert(conn,'release_knowledge',{'release_id':rel2,'record_id':rid,'review_id':review})


def test_approved_evidence_membership_is_sealed(conn):
    a,_,v=base(conn);rid=knowledge(conn,v);ev=anchor(conn,a)
    link={'record_id':rid,'evidence_id':ev,'version_id':v,'asset_id':a,'role':'direct'}
    insert(conn,'knowledge_evidence',link);approve(conn,rid,ev)
    assert insert(conn,'knowledge_evidence',link) is None
    ev2=anchor(conn,a)
    with pytest.raises(psycopg.Error):insert(conn,'knowledge_evidence',link|{'evidence_id':ev2})


def test_category_definition_cannot_use_another_version(conn):
    _,_,v=base(conn);_,_,v2=base(conn,'b');rid=knowledge(conn,v2)
    insert(conn,'category_namespace',{'namespace':'N','version_id':v})
    with pytest.raises(psycopg.Error):insert(conn,'category_node',{'category_id':uid(),'namespace':'N','version_id':v,'code':'1','label':'SYNTHETIC','definition_record_id':rid})


def test_review_events_in_one_transaction_follow_insertion_order(conn):
    a,_,v=base(conn);rid=knowledge(conn,v);ev=anchor(conn,a)
    insert(conn,'knowledge_evidence',{'record_id':rid,'evidence_id':ev,'version_id':v,'asset_id':a,'role':'direct'})
    insert(conn,'reviewer',{'actor':'fixture','identity_reference':'SYNTHETIC','synthetic':True})
    with conn.transaction():
        insert(conn,'review_record',{'review_id':'ffffffff-ffff-ffff-ffff-ffffffffffff','record_id':rid,'action':'review_pending','actor':'fixture','reason':'SYNTHETIC'})
        insert(conn,'review_record',{'review_id':'00000000-0000-0000-0000-000000000001','record_id':rid,'action':'approved','actor':'fixture','reason':'SYNTHETIC','evidence_id':ev})
    assert conn.execute('SELECT effective_review_state FROM knowledge_status WHERE record_id=%s',(rid,)).fetchone()['effective_review_state']=='approved'
