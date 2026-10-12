import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from food_kb.common import file_hash,write_json
from food_kb.config import Config
from food_kb.db.session import connect,migrate
from food_kb.ingest.inventory import CATALOG,GUIDE


def pytest_configure(config):
    dbname=os.environ.get('FOOD_KB_TEST_DBNAME','food_kb_m1')
    port=int(os.environ.get('FOOD_KB_TEST_PORT','55432'))
    if not (dbname.startswith('food_kb_test_') or (dbname=='food_kb_m1' and port==55432)):
        raise pytest.UsageError('Tests require a food_kb_test_ database or the legacy food_kb_m1 on port 55432; business databases are forbidden.')


@pytest.fixture
def config(tmp_path):
    root=tmp_path/'sources';root.mkdir()
    index=root/Path(CATALOG).parent;index.mkdir(parents=True)
    (root/'files').mkdir()
    (root/'files/original.txt').write_text('SYNTHETIC original standard text, not a real food requirement.',encoding='utf8')
    sha=file_hash(root/'files/original.txt')
    catalog=[{'id':'synthetic-v1','code':'SYNTH 1-2020','family':'SYNTH 1','title':'SYNTHETIC standard',
        'publication_date':'2020-01-01','implementation_date':'2020-02-01','official_status':'synthetic',
        'attachments':[{'path':'files/original.txt','sha256':sha}]}]
    write_json(root/CATALOG,catalog)
    write_json(root/GUIDE,{'source':'files/original.txt','sha256':sha,'chapters':[],'sections':[],'tables':[],'inspection_rows':[]})
    db={'host':'127.0.0.1','port':int(os.environ.get('FOOD_KB_TEST_PORT','55432')),
        'dbname':os.environ.get('FOOD_KB_TEST_DBNAME','food_kb_m1'),
        'user':os.environ.get('FOOD_KB_TEST_USER','kb_app'),'password_env':'FOOD_KB_PASSWORD'}
    p=tmp_path/'config.json'
    write_json(p,{'source_root':'sources','allowed_sources':['files','references'],'output_root':'output','database':db})
    return Config.load(p)


@pytest.fixture
def conn(config):
    # Every test gets a new schema. No reset, truncate or cleanup of existing user data.
    c=connect(config);schema='test_'+uuid4().hex
    c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
    migrate(c)
    yield c
    c.close()
