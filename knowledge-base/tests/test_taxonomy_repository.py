from dataclasses import replace
from uuid import uuid4

import psycopg
import pytest

from food_kb.common import KBError, digest, identity, write_json
from food_kb.db.session import insert
from food_kb.storage.assets import preserve
from food_kb.taxonomy.repository import build_snapshot, read_snapshot


def registered(conn, config, chinese=False):
    original = preserve(config, 'files/original.txt')
    insert(conn, 'source_asset', original)
    doc, version = str(uuid4()), str(uuid4())
    insert(conn, 'source_document', {'document_id': doc, 'source_key': 'synthetic-snapshot', 'kind': 'guide', 'title': 'SYNTHETIC'})
    insert(conn, 'document_version', {'version_id': version, 'document_id': doc, 'designation': 'SYNTHETIC',
                                    'revision': '1', 'title': 'SYNTHETIC', 'identity_status': 'unverified'})
    source = {'source': 'files/original.txt', 'sha256': original['asset_id']}
    data = [
        ('taxonomy_metadata', 'references/taxonomy.json', source | {'namespace': 'SYNTHETIC-A', 'standard': 'SYNTHETIC',
            'coded_nodes': [{'id': 'source-apple', 'classification_code': '01', 'label': '苹果',
                            'label_status': '原文类别', 'pdf_page': 1}], 'tables': []}),
        ('guide_metadata', 'references/guide.json', source | {'sections': [], 'tables': [], 'inspection_rows': []}),
        ('definition_export_metadata', 'references/definitions.json', {'source_sha256': original['asset_id'], 'definitions': []}),
        ('guide_page_cache_metadata', 'references/pages.json', source | {'pages': []}),
        ('definition_bridge_metadata', 'references/bridges.json', {'bridges': []}),
    ]
    if chinese:
        kind, _, raw = data[0]
        data[0] = (kind, 'references/分类体系/taxonomy.json', raw)
        data.append((kind, 'references/标准原文/another-taxonomy.json', raw | {'namespace': 'SYNTHETIC-B'}))
    for kind, path, raw in data:
        write_json(config.source_root / path, raw)
        asset = preserve(config, path); insert(conn, 'source_asset', asset)
        lid, rid = identity('synthetic-legacy', path), identity('synthetic-knowledge', path)
        insert(conn, 'legacy_record', {'legacy_id': lid, 'index_asset_id': asset['asset_id'], 'source_key': path,
               'json_pointer': '', 'legacy_key': 'metadata', 'adapter': kind, 'importer_version': 'legacy-2', 'raw_payload': raw})
        insert(conn, 'knowledge_record', {'record_id': rid, 'logical_key': path, 'revision': digest(raw),
               'record_type': kind, 'version_id': version, 'payload': raw, 'extraction_run': 'SYNTHETIC', 'synthetic': True})
        insert(conn, 'legacy_knowledge', {'legacy_id': lid, 'record_id': rid})
    return data


def counts(conn):
    return {name: conn.execute('SELECT count(*) n FROM ' + name).fetchone()['n']
            for name in ('taxonomy_snapshot', 'taxonomy_snapshot_source', 'source_asset', 'source_registration',
                         'legacy_record', 'knowledge_record', 'review_record', 'kb_release')}


def test_frozen_snapshot_repeat_is_zero_increment_and_history_ignores_changed_current_file(conn, config):
    registered(conn, config)
    first = build_snapshot(conn, config); payload = read_snapshot(conn, first['snapshot_id']); before = counts(conn)
    repeated = build_snapshot(conn, config)
    assert repeated['status'] == 'duplicate' and repeated['snapshot_id'] == first['snapshot_id'] and counts(conn) == before
    assert digest(payload) == first['payload_hash'] and payload['executable'] is False
    assert payload['summary']['namespaces'] == 2 and payload['summary']['inspection_requirements'] == 0
    assert payload['namespaces'][0]['standard_version_id'] != ''
    write_json(config.source_root / 'references/taxonomy.json', {'changed': 'SYNTHETIC'})
    assert read_snapshot(conn, first['snapshot_id']) == payload
    with pytest.raises(KBError, match='not a unique registered'):
        build_snapshot(conn, config)
    assert counts(conn) == before


def test_preserved_source_corruption_refuses_build_without_overwrite(conn, config):
    registered(conn, config)
    asset = preserve(config, 'references/taxonomy.json')
    stored = config.output_root / asset['storage_key']; stored.write_text('SYNTHETIC damaged storage', encoding='utf8')
    before = counts(conn)
    with pytest.raises(KBError, match='fingerprint changed'):
        build_snapshot(conn, config)
    assert counts(conn) == before and stored.read_text('utf8') == 'SYNTHETIC damaged storage'


def test_changed_processor_output_is_not_silently_overwritten(conn, config, monkeypatch):
    registered(conn, config); first = build_snapshot(conn, config); before = counts(conn)
    import food_kb.taxonomy.repository as repository
    original = repository.build_standard
    def altered(*args):
        value = original(*args); value['nodes'][0]['name'] = 'SYNTHETIC altered'; return value
    monkeypatch.setattr(repository, 'build_standard', altered)
    with pytest.raises(KBError, match='different content'):
        build_snapshot(conn, config)
    assert counts(conn) == before and read_snapshot(conn, first['snapshot_id'])['namespaces'][0]['nodes'][0]['name'] == '苹果'


def test_snapshot_rows_and_members_are_immutable_and_late_members_fail(conn, config):
    registered(conn, config); first = build_snapshot(conn, config)
    with pytest.raises(psycopg.Error, match='IMMUTABLE_HISTORY'):
        conn.execute('DELETE FROM taxonomy_snapshot WHERE snapshot_id=%s', (first['snapshot_id'],))
    with pytest.raises(psycopg.Error, match='IMMUTABLE_HISTORY'):
        conn.execute('DELETE FROM taxonomy_snapshot_source WHERE snapshot_id=%s', (first['snapshot_id'],))
    asset = preserve(config, 'files/original.txt')['asset_id']
    with pytest.raises(psycopg.Error, match='TAXONOMY_SOURCE_MANIFEST_MISMATCH'):
        insert(conn, 'taxonomy_snapshot_source', {'snapshot_id': first['snapshot_id'], 'source_key': 'late', 'asset_id': asset})


def test_missing_registered_inputs_do_not_create_empty_success_snapshot(conn, config):
    with pytest.raises(KBError, match='must be registered first'):
        build_snapshot(conn, config)
    assert counts(conn)['taxonomy_snapshot'] == 0


def test_engineering_hints_are_source_literals_without_executing_the_old_module(conn, config):
    registered(conn, config)
    engine = config.source_root / 'food-classifier/engine.py'; engine.parent.mkdir()
    engine.write_text("raise RuntimeError('module must not execute')\nAMBIGUOUS = {'牛奶': ['巴氏杀菌乳', '灭菌乳'], '面粉': ['小麦粉', '玉米粉']}\n", encoding='utf8')
    selected = replace(config, allowed_sources=config.allowed_sources + (engine,))
    result = build_snapshot(conn, selected); snapshot = read_snapshot(conn, result['snapshot_id'])
    assert len(snapshot['normalization_hints']) == 2
    assert all(item['verification_state'] == 'unverified' and item['kind'] == 'engineering_ambiguity' for item in snapshot['normalization_hints'])
    assert snapshot['normalization_hints'][0]['evidence'][0]['evidence_role'] == 'engineering_hint'
    assert counts(conn)['source_registration'] == 1
    before = counts(conn); assert build_snapshot(conn, selected)['status'] == 'duplicate'; assert counts(conn) == before


def test_nonliteral_engineering_hint_is_rejected(conn, config):
    registered(conn, config)
    engine = config.source_root / 'food-classifier/engine.py'; engine.parent.mkdir()
    engine.write_text("AMBIGUOUS = dict(牛奶=['巴氏杀菌乳'])\n", encoding='utf8')
    selected = replace(config, allowed_sources=config.allowed_sources + (engine,))
    with pytest.raises(KBError, match='not a source literal'):
        build_snapshot(conn, selected)
    assert counts(conn)['taxonomy_snapshot'] == counts(conn)['source_registration'] == 0


def test_chinese_source_membership_is_independent_of_database_locale_collation(conn, config):
    registered(conn, config, chinese=True)
    first = build_snapshot(conn, config)
    stored = read_snapshot(conn, first['snapshot_id'])
    assert any('标准原文' in source['source_key'] for source in stored['sources'])
    assert any('分类体系' in source['source_key'] for source in stored['sources'])
    assert build_snapshot(conn, config)['status'] == 'duplicate'
    assert digest(read_snapshot(conn, first['snapshot_id'])) == first['payload_hash']


def test_processor_upgrade_appends_a_snapshot_and_keeps_previous_content_readable(conn, config, monkeypatch):
    registered(conn, config)
    import food_kb.taxonomy.repository as repository
    monkeypatch.setattr(repository, 'PROCESSOR_VERSION', 'taxonomy-sources-1')
    original = build_snapshot(conn, config); frozen = read_snapshot(conn, original['snapshot_id'])
    old_rows = conn.execute('SELECT source_key,asset_id,legacy_id FROM taxonomy_snapshot_source WHERE snapshot_id=%s ORDER BY source_key',
                            (original['snapshot_id'],)).fetchall()
    monkeypatch.setattr(repository, 'PROCESSOR_VERSION', 'taxonomy-sources-2')
    upgraded = build_snapshot(conn, config)
    assert upgraded['snapshot_id'] != original['snapshot_id'] and counts(conn)['taxonomy_snapshot'] == 2
    assert read_snapshot(conn, original['snapshot_id']) == frozen and digest(frozen) == original['payload_hash']
    assert conn.execute('SELECT source_key,asset_id,legacy_id FROM taxonomy_snapshot_source WHERE snapshot_id=%s ORDER BY source_key',
                        (original['snapshot_id'],)).fetchall() == old_rows


@pytest.mark.parametrize('field', ['schema_version', 'snapshot_id', 'processor_version', 'input_hash',
                                 'review_state', 'executable', 'scope_complete', 'sources', 'namespaces'])
def test_database_does_not_let_missing_candidate_contract_fields_pass_as_null(conn, config, field):
    registered(conn, config); first = build_snapshot(conn, config)
    payload = read_snapshot(conn, first['snapshot_id']); payload = payload | {'snapshot_id': str(uuid4()), 'processor_version': 'SYNTHETIC-missing-field'}
    missing_id = payload['snapshot_id']; del payload[field]
    with pytest.raises(psycopg.Error):
        insert(conn, 'taxonomy_snapshot', {'snapshot_id': missing_id, 'processor_version': 'SYNTHETIC-missing-field',
               'input_hash': payload.get('input_hash', 'a' * 64), 'payload_hash': digest(payload), 'payload': payload})


def test_database_rejects_legacy_member_identity_borrowed_from_another_source(conn, config):
    registered(conn, config); result = build_snapshot(conn, config)
    members = conn.execute('SELECT * FROM taxonomy_snapshot_source WHERE snapshot_id=%s AND legacy_id IS NOT NULL ORDER BY source_key',
                           (result['snapshot_id'],)).fetchall()
    with pytest.raises(psycopg.Error, match='TAXONOMY_SOURCE_IDENTITY_MISMATCH'):
        insert(conn, 'taxonomy_snapshot_source', {'snapshot_id': result['snapshot_id'], 'source_key': members[0]['source_key'],
               'asset_id': members[0]['asset_id'], 'legacy_id': members[1]['legacy_id']})
