from contextlib import nullcontext
import json
from pathlib import Path
import shutil
from uuid import uuid4

import psycopg
import pytest

from food_kb.common import KBError, file_hash, read_json, write_json
from food_kb.chunking import service

MANUAL_DIRECTORY = Path(__file__).resolve().parents[2] / 'docs/knowledge-base/manual/GB1886.1-2015'
pytestmark = pytest.mark.skipif(not (MANUAL_DIRECTORY / 'source-map.json').is_file(),
                               reason='Private manual source bundle is not distributed with public code.')


@pytest.fixture
def manual_package(config):
    source = MANUAL_DIRECTORY
    destination = config.source_root / 'files/manual'
    shutil.copytree(source, destination)
    return destination


def counts(conn):
    return {table: conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in
            ('source_asset', 'parse_run', 'content_block', 'chunk_run', 'chunk', 'evidence_anchor',
             'chunk_evidence', 'chunk_context_link')}


def revise_source_metadata(directory):
    path = directory / 'source.json'
    source = read_json(path)
    source['synthetic_test_revision'] = 2
    write_json(path, source)
    checksum_path = directory / 'checksums.json'
    if checksum_path.is_file():
        checksums = read_json(checksum_path)
        checksums['source.json'] = file_hash(path)
        write_json(checksum_path, checksums)


def test_repeat_chunking_keeps_all_object_counts_and_saved_report(conn, config, manual_package):
    first = service.chunk_manual(conn, config, 'files/manual')
    before = counts(conn)
    report = service.read_run(conn, config, first['run_id'])
    second = service.chunk_manual(conn, config, 'files/manual')
    assert first['status'] == 'completed' and second['status'] == 'duplicate'
    assert first['run_id'] == second['run_id']
    assert counts(conn) == before
    assert service.read_run(conn, config, first['run_id']) == report
    assert len([chunk for chunk in report['chunks'] if chunk['kind'] == 'table_row']) == 9
    difference = service.compare_runs(conn, config, first['run_id'], second['run_id'])
    assert all(difference[key] == [] for key in ('added', 'removed', 'content_changed', 'identity_changed'))


def test_new_strategy_preserves_old_parse_chunks_and_report(conn, config, manual_package, monkeypatch, capsys):
    first = service.chunk_manual(conn, config, 'files/manual')
    old = service.read_run(conn, config, first['run_id'])
    before = counts(conn)
    second = service.chunk_manual(conn, config, 'files/manual', strategy_version='SYNTHETIC-strategy-2')
    after = counts(conn)
    assert first['parse_id'] == second['parse_id'] and first['run_id'] != second['run_id']
    assert after['parse_run'] == before['parse_run']
    assert after['content_block'] == before['content_block']
    assert after['chunk'] == before['chunk'] * 2
    assert after['chunk_run'] == 2
    assert service.read_run(conn, config, first['run_id']) == old
    difference = service.compare_runs(conn, config, first['run_id'], second['run_id'])
    assert difference['same_parse']
    assert len(difference['identity_changed']) == first['chunks'] == 120
    assert difference['content_changed'] == difference['added'] == difference['removed'] == []
    from food_kb import cli
    monkeypatch.setattr(cli.Config, 'load', staticmethod(lambda _: config))
    monkeypatch.setattr(cli, 'connect', lambda _: nullcontext(conn))
    before_cli = counts(conn)
    assert cli.main(['chunk-diff', first['run_id'], second['run_id']]) == 0
    assert json.loads(capsys.readouterr().out) == difference
    assert counts(conn) == before_cli


def test_changed_source_package_creates_new_parse_without_replacing_old(conn, config, manual_package):
    first = service.chunk_manual(conn, config, 'files/manual')
    old = service.read_run(conn, config, first['run_id'])
    revise_source_metadata(manual_package)
    # Explicit synthetic revision of the copied Markdown, never the real source.
    markdown = manual_package / 'GB1886.1-2015.md'
    text = markdown.read_text(encoding='utf8')
    assert '| 99.2 |' in text
    markdown.write_text(text.replace('| 99.2 |', '| 99.3 |', 1), encoding='utf8')
    map_path = manual_package / 'source-map.json'
    mapping = read_json(map_path)
    mapping['markdown_sha256'] = file_hash(markdown)
    write_json(map_path, mapping)
    checksum_path = manual_package / 'checksums.json'
    checksums = read_json(checksum_path)
    checksums['GB1886.1-2015.md'] = file_hash(markdown)
    checksums['source-map.json'] = file_hash(map_path)
    write_json(checksum_path, checksums)
    second = service.chunk_manual(conn, config, 'files/manual')
    assert first['parse_id'] != second['parse_id']
    assert counts(conn)['parse_run'] == 2
    assert service.read_run(conn, config, first['run_id']) == old
    difference = service.compare_runs(conn, config, first['run_id'], second['run_id'])
    assert not difference['same_parse']
    assert 0 < len(difference['content_changed']) < first['chunks']
    assert 'table-2:row:1' in difference['content_changed']
    assert len(difference['identity_changed']) == first['chunks']
    assert difference['added'] == difference['removed'] == []


@pytest.mark.parametrize('target', ['report', 'source'])
def test_saved_report_and_preserved_source_tampering_are_rejected(conn, config, manual_package, target):
    first = service.chunk_manual(conn, config, 'files/manual')
    report = service.read_run(conn, config, first['run_id'])
    if target == 'report':
        path = Path(first['report'])
    else:
        item = report['source']['preserved_files']['source-map.json']
        path = config.output_root / item['storage_key']
    path.write_bytes(path.read_bytes() + b'\nSYNTHETIC tamper')
    with pytest.raises(KBError) as caught:
        service.read_run(conn, config, first['run_id'])
    assert caught.value.code == ('CHUNK_REPORT_CHANGED' if target == 'report' else 'CHUNK_SOURCE_CHANGED')


def test_invalid_source_map_writes_no_database_objects(conn, config, manual_package):
    path = manual_package / 'source-map.json'
    mapping = read_json(path)
    mapping['pages'][2]['markdown_start_line'] += 1
    write_json(path, mapping)
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.chunk_manual(conn, config, 'files/manual')
    assert caught.value.code == 'MANUAL_PAGE_MISMATCH'
    assert counts(conn) == before


def test_cross_parse_context_and_evidence_links_are_rejected(conn, config, manual_package):
    first = service.chunk_manual(conn, config, 'files/manual')
    revise_source_metadata(manual_package)
    second = service.chunk_manual(conn, config, 'files/manual')
    chunk = conn.execute('SELECT chunk_id FROM chunk WHERE parse_id=%s LIMIT 1', (first['parse_id'],)).fetchone()
    block = conn.execute('SELECT block_id FROM content_block WHERE parse_id=%s LIMIT 1', (second['parse_id'],)).fetchone()
    evidence = conn.execute('SELECT evidence_id FROM evidence_anchor WHERE parse_id=%s LIMIT 1', (second['parse_id'],)).fetchone()
    with pytest.raises(psycopg.Error, match='same parse'):
        with conn.transaction():
            conn.execute('INSERT INTO chunk_context_link(chunk_id,target_block_id,role) VALUES(%s,%s,%s)',
                         (chunk['chunk_id'], block['block_id'], 'SYNTHETIC-cross-parse'))
    with pytest.raises(psycopg.Error, match='same parse'):
        with conn.transaction():
            conn.execute('INSERT INTO chunk_evidence(chunk_id,evidence_id) VALUES(%s,%s)',
                         (chunk['chunk_id'], evidence['evidence_id']))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute('''INSERT INTO chunk(chunk_id,parse_id,strategy_version,content_hash,retrieval_text,chunk_run_id)
                            VALUES(%s,%s,%s,%s,%s,%s)''',
                         (uuid4(), first['parse_id'], 'SYNTHETIC', 'SYNTHETIC', 'cross-parse run', second['run_id']))


def test_chunk_run_and_chunks_are_immutable(conn, config, manual_package):
    first = service.chunk_manual(conn, config, 'files/manual')
    for query in ('UPDATE chunk_run SET strategy_version=%s WHERE run_id=%s',
                  'UPDATE chunk SET strategy_version=%s WHERE chunk_run_id=%s'):
        with pytest.raises(psycopg.Error):
            with conn.transaction():
                conn.execute(query, ('SYNTHETIC-overwrite', first['run_id']))
    assert service.read_run(conn, config, first['run_id'])['strategy_version'] != 'SYNTHETIC-overwrite'


def test_mid_transaction_failure_rolls_back_all_chunk_objects(conn, config, manual_package, monkeypatch):
    original = service.insert
    def fail_on_links(connection, table, values):
        if table == 'chunk_context_link':
            raise RuntimeError('SYNTHETIC late transaction failure')
        return original(connection, table, values)
    monkeypatch.setattr(service, 'insert', fail_on_links)
    before = counts(conn)
    with pytest.raises(RuntimeError, match='SYNTHETIC late'):
        service.chunk_manual(conn, config, 'files/manual')
    assert counts(conn) == before


def test_source_map_change_after_loading_cannot_be_registered_as_old_content(conn, config, manual_package, monkeypatch):
    original = service.load_manual
    def changed_after_loading(directory):
        manual = original(directory)
        path = directory / 'source-map.json'
        mapping = read_json(path)
        mapping['synthetic_test_race'] = 'changed after the parser read it'
        write_json(path, mapping)
        return manual
    monkeypatch.setattr(service, 'load_manual', changed_after_loading)
    before = counts(conn)
    with pytest.raises(KBError):
        service.chunk_manual(conn, config, 'files/manual')
    assert counts(conn) == before


def test_strategy_change_cannot_silently_retype_an_existing_parse_block(conn, config, manual_package, monkeypatch):
    service.chunk_manual(conn, config, 'files/manual')
    original = service.build_bundle
    def changed_block(*args, **kwargs):
        bundle = original(*args, **kwargs)
        bundle['blocks'][0]['kind'] = 'SYNTHETIC-different-parse-block-kind'
        return bundle
    monkeypatch.setattr(service, 'build_bundle', changed_block)
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.chunk_manual(conn, config, 'files/manual', strategy_version='SYNTHETIC-changed-block')
    assert caught.value.code == 'MANUAL_BLOCK_CONFLICT'
    assert counts(conn) == before
