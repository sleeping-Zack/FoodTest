from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from food_kb.common import KBError,file_hash,read_json,write_json
from food_kb.config import Config
from food_kb.ingest.inventory import CATALOG,inventory
from food_kb.storage.assets import preserve


def test_missing_config():
    with pytest.raises(KBError,match='Provide'):Config.load(None)


@pytest.mark.parametrize('host,dbname,accepted',[
    ('127.0.0.1','foodtest',True),('::1','foodtest',True),
    ('127.0.0.1','food_kb_m1',True),('127.0.0.1','postgres',False),
    ('192.0.2.1','foodtest',False),
])
def test_database_destination_boundary(config,tmp_path,host,dbname,accepted):
    p=tmp_path/'destination.json'
    write_json(p,{'source_root':'sources','allowed_sources':['files'],'output_root':'output',
        'database':config.database|{'host':host,'dbname':dbname}})
    if accepted:
        assert Config.load(p).database['dbname']==dbname
    else:
        with pytest.raises(KBError,match='loopback'):Config.load(p)


@pytest.mark.parametrize('relative',['../escape','files/../../escape','C:/Windows/system.ini'])
def test_path_traversal(config,relative):
    with pytest.raises(KBError):config.source(relative)


def test_output_cannot_overlap_original(config,tmp_path):
    p=tmp_path/'bad.json';write_json(p,{'source_root':'sources','allowed_sources':['files'],'output_root':'sources/files/original.txt','database':config.database})
    before=file_hash(config.source('files/original.txt'))
    with pytest.raises(KBError):Config.load(p)
    assert file_hash(config.source('files/original.txt'))==before


def test_password_missing_does_not_create_output(config,monkeypatch):
    monkeypatch.delenv('FOOD_KB_PASSWORD',raising=False)
    with pytest.raises(KBError,match='password'):config.connection_args()
    assert not config.output_root.exists()


def test_provider_cannot_be_silently_enabled(config,tmp_path):
    p=tmp_path/'bad.json';write_json(p,{'source_root':'sources','allowed_sources':['files'],'output_root':'output','database':config.database,'provider':'external'})
    with pytest.raises(KBError,match='disabled'):Config.load(p)


def test_installed_help_from_other_directory(tmp_path):
    r=subprocess.run([sys.executable,'-m','food_kb','--help'],cwd=tmp_path,capture_output=True,text=True)
    assert r.returncode==0 and 'trace' in r.stdout


def test_preserved_original_survives_source_change(config):
    source=config.source('files/original.txt');old=file_hash(source)
    one=preserve(config,'files/original.txt');source.write_text('SYNTHETIC replacement')
    two=preserve(config,'files/original.txt')
    assert one['asset_id']!=two['asset_id']
    assert file_hash(config.output_root/one['storage_key'])==old


def test_corrupt_preserved_file_is_not_overwritten(config):
    info=preserve(config,'files/original.txt');p=config.output_root/info['storage_key'];p.write_text('tampered')
    with pytest.raises(KBError,match='changed'):preserve(config,'files/original.txt')
    assert p.read_text()=='tampered'


def test_inventory_detects_changed_missing_and_invalid_json(config,tmp_path):
    one=tmp_path/'first';inventory(config,one)
    p=config.source('files/original.txt');p.write_text('SYNTHETIC changed')
    broken=config.source('files/truncated.json');broken.write_text('{"samples":[')
    two=tmp_path/'second';inventory(config,two,one/'inventory.jsonl')
    gaps=read_json(two/'gaps.json')
    assert any(g.get('file_status')=='hash_mismatch' for g in gaps)
    assert any(g.get('file_status')=='invalid_json' for g in gaps)
    p.unlink();three=tmp_path/'third';inventory(config,three,two/'inventory.jsonl')
    assert any(g['file_status']=='source_missing' for g in read_json(three/'gaps.json'))
    assert broken.read_text()=='{"samples":['


def test_database_failure_is_nonzero_and_secret_is_redacted(config,tmp_path,monkeypatch):
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    p=tmp_path/'offline.json'
    write_json(p,{'source_root':'sources','allowed_sources':['files'],'output_root':'output','database':config.database|{'port':port}})
    monkeypatch.setenv('FOOD_KB_PASSWORD','SYNTHETIC_SECRET_MUST_NOT_APPEAR')
    r=subprocess.run([sys.executable,'-m','food_kb','--config',str(p),'doctor'],capture_output=True,text=True)
    assert r.returncode==1 and 'DATABASE_ERROR' in r.stderr
    assert 'SYNTHETIC_SECRET_MUST_NOT_APPEAR' not in r.stdout+r.stderr
