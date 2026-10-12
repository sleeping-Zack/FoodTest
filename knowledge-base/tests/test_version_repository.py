from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from food_kb.common import KBError, digest, identity, write_json
from food_kb.db.session import insert
from food_kb.storage.assets import preserve
from food_kb.versions.repository import build_snapshot, collect_payload, read_snapshot
from food_kb.versions.sources import INDEXES


def registered(conn, config, *, amended=False, draft=False, missing_body=False, nul=False):
    original = preserve(config, 'files/original.txt'); insert(conn, 'source_asset', original)
    insert(conn, 'source_registration', {'registration_id': str(uuid4()), 'asset_id': original['asset_id'],
                                         'source_key': 'files/original.txt'})
    catalog_key, references_key, announcements_key = INDEXES
    catalog = []
    versions = []
    for n in (1, 2):
        doc, vid = str(uuid4()), str(uuid4()); versions.append(vid)
        raw = {'id': 'synthetic-' + str(n), 'code': f'SYNTH {n}-2020', 'family': f'SYNTH {n}',
               'title': 'SYNTHETIC', 'publication_date': '2020-01-01', 'implementation_date': '2020-02-01',
               'official_status': 'synthetic', 'is_amendment': amended and n == 2,
               'is_draft': draft and n == 2, 'attachments': [] if missing_body and n == 2 else
               [{'path': 'files/original.txt', 'sha256': original['asset_id']} ]}
        catalog.append(raw)
        insert(conn, 'source_document', {'document_id': doc, 'source_key': doc, 'kind': 'standard',
                                        'standard_family': raw['family'], 'title': 'SYNTHETIC'})
        insert(conn, 'document_version', {'version_id': vid, 'document_id': doc, 'designation': raw['code'],
               'revision': '1', 'title': 'SYNTHETIC', 'identity_status': 'draft' if draft and n == 2 else 'unverified'})
        if raw['attachments']:
            insert(conn, 'version_asset', {'version_id': vid, 'asset_id': original['asset_id'],
                  'role': 'amendment' if amended and n == 2 else 'draft' if draft and n == 2 else 'original'})
    references = [{'source_id': 'synthetic-1', 'source_path': 'files/original.txt',
        'source_sha256': original['asset_id'], 'pdf_page': 1, 'target_code': 'SYNTH 2-2020',
        'target_family': 'SYNTH 2', 'relation': '正文标准号引用候选',
        'excerpt': 'SYNTHETIC\x00 evidence' if nul else 'SYNTHETIC evidence'}]
    announcements = [{'id': 'synthetic-ann', 'kind': '勘误', 'title': 'SYNTHETIC erratum',
                      'source_path': 'files/original.txt', 'related_standard_ids': ['synthetic-1']}]
    for key, data in [(catalog_key, catalog), (references_key, references), (announcements_key, announcements)]:
        write_json(config.source_root / key, data)
        asset = preserve(config, key); insert(conn, 'source_asset', asset)
        for i, raw in enumerate(data):
            lid = identity('test-version-legacy', asset['asset_id'], i)
            insert(conn, 'legacy_record', {'legacy_id': lid, 'index_asset_id': asset['asset_id'],
                   'source_key': key, 'json_pointer': '/' + str(i), 'legacy_key': raw.get('id', str(i)),
                   'adapter': INDEXES[key], 'importer_version': 'legacy-2', 'raw_payload': raw})
            if key == catalog_key:
                insert(conn, 'document_claim', {'legacy_id': lid, 'version_id': versions[i]})
    return versions


def counts(conn):
    return {t: conn.execute('SELECT count(*) n FROM ' + t).fetchone()['n'] for t in
            ('source_asset', 'document_version', 'knowledge_record', 'review_record', 'kb_release',
             'version_reference_snapshot', 'version_reference_snapshot_source')}


def test_repeat_frozen_read_and_readonly_collection_preserve_history(conn, config):
    registered(conn, config); baseline = counts(conn)
    payload = collect_payload(conn, config)
    assert counts(conn) == baseline and payload['summary']['versions'] == 2
    first = build_snapshot(conn, config); before = counts(conn)
    assert build_snapshot(conn, config)['status'] == 'duplicate' and counts(conn) == before
    assert read_snapshot(conn, first['snapshot_id']) == payload
    assert digest(payload) == first['payload_hash']
    assert all(c['complete'] for c in payload['registered_coverage'])
    assert all(v['policies'] == [] and not v['executable'] for v in payload['versions'])
    key = next(iter(INDEXES)); write_json(config.source_root / key, [{'changed': True}])
    assert read_snapshot(conn, first['snapshot_id']) == payload
    with pytest.raises(KBError, match='not registered'):
        build_snapshot(conn, config)
    assert counts(conn) == before


@pytest.mark.parametrize('where', ['current', 'preserved'])
def test_registered_source_corruption_never_replaces_history(conn, config, where):
    registered(conn, config); first = build_snapshot(conn, config); before = counts(conn)
    original = preserve(config, 'files/original.txt')
    path = config.source_root / 'files/original.txt' if where == 'current' else config.output_root / original['storage_key']
    path.write_text('SYNTHETIC corrupt', encoding='utf8')
    with pytest.raises(KBError, match='fingerprint changed'):
        build_snapshot(conn, config)
    assert counts(conn) == before and read_snapshot(conn, first['snapshot_id'])['snapshot_id'] == first['snapshot_id']


@pytest.mark.parametrize('variant', ['amended', 'draft', 'missing_body'])
def test_amendment_draft_and_metadata_never_supply_principal_body(conn, config, variant):
    versions = registered(conn, config, **{variant: True}); first = build_snapshot(conn, config)
    payload = read_snapshot(conn, first['snapshot_id']); second = next(v for v in payload['versions'] if v['version_id'] == versions[1])
    assert second['body_assets'] == []
    assert any(g['kind'] == 'missing_body' and g['target']['id'] == versions[1] for g in payload['gaps'])
    if variant == 'amended':
        assert second['other_assets'][0]['role'] == 'amendment'
        assert any(e['kind'] == 'amendment' for e in payload['events'])


def test_unrepresentable_text_remains_raw_and_explicitly_quarantined(conn, config):
    versions = registered(conn, config, nul=True); payload = collect_payload(conn, config)
    assert payload['references'] == [] and payload['summary']['gap_kinds']['unsupported_text'] == 1
    assert '\x00' in conn.execute("SELECT raw_payload FROM legacy_record WHERE adapter='reference_candidate'").fetchone()['raw_payload']['excerpt']
    assert {'kind': 'version', 'id': versions[0]} in next(g for g in payload['gaps'] if g['kind'] == 'unsupported_text')['affected_refs']
    from food_kb.versions.references import expand_references
    expanded = expand_references(payload, [versions[0]])
    assert any(g['kind'] == 'unsupported_text' for g in expanded['gaps']) and not expanded['complete']
    build_snapshot(conn, config)


def test_nature_scope_and_datedness_are_independent_claims(conn, config):
    registered(conn, config); payload = collect_payload(conn, config); ref = payload['references'][0]
    assert ref['nature']['value'] == 'mention' and ref['scope']['value'] == 'unknown'
    assert ref['binding']['datedness'] == 'dated' and len(ref['binding']['version_ids']) == 1
    assert all(ref[k]['verification_state'] == 'unverified' for k in ('nature', 'scope', 'binding'))
    assert ref['condition']['state'] == 'unresolved' and ref['review_state'] == 'review_pending'


def test_source_manifest_and_snapshot_rows_are_append_only(conn, config):
    registered(conn, config); first = build_snapshot(conn, config)
    for table in ('version_reference_snapshot', 'version_reference_snapshot_source'):
        with pytest.raises(psycopg.Error, match='IMMUTABLE_HISTORY'):
            conn.execute('DELETE FROM ' + table)
    with pytest.raises(psycopg.Error, match='VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'):
        with conn.transaction():
            original = preserve(config, 'files/original.txt')
            insert(conn, 'version_reference_snapshot_source', {'snapshot_id': first['snapshot_id'],
                   'source_key': 'references/late.txt', 'asset_id': original['asset_id']})


def test_missing_manifest_members_rollback_entire_snapshot(conn, config):
    registered(conn, config); payload = collect_payload(conn, config); before = counts(conn)
    with pytest.raises(psycopg.Error, match='VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'):
        with conn.transaction():
            insert(conn, 'version_reference_snapshot', {'snapshot_id': payload['snapshot_id'],
                'processor_version': payload['processor_version'], 'input_hash': payload['input_hash'],
                'payload_hash': digest(payload), 'payload': payload})
    assert counts(conn) == before


def test_missing_candidate_gate_key_is_not_null_bypass(conn, config):
    registered(conn, config); payload = collect_payload(conn, config); payload.pop('executable')
    with pytest.raises(psycopg.Error):
        insert(conn, 'version_reference_snapshot', {'snapshot_id': payload['snapshot_id'],
            'processor_version': payload['processor_version'], 'input_hash': payload['input_hash'],
            'payload_hash': digest(payload), 'payload': payload})


def test_source_manifest_cannot_borrow_other_index_identity(conn, config):
    registered(conn, config); first = build_snapshot(conn, config)
    rows = conn.execute('SELECT source_key,asset_id,legacy_id FROM version_reference_snapshot_source WHERE legacy_id IS NOT NULL').fetchall()
    with pytest.raises(psycopg.Error, match='TAXONOMY_SOURCE_IDENTITY_MISMATCH'):
        insert(conn, 'version_reference_snapshot_source', {'snapshot_id': first['snapshot_id'],
               'source_key': 'references/other.json', 'asset_id': rows[0]['asset_id'], 'legacy_id': rows[0]['legacy_id']})


def test_new_processor_retains_old_snapshot(conn, config, monkeypatch):
    registered(conn, config); first = build_snapshot(conn, config); old = read_snapshot(conn, first['snapshot_id'])
    import food_kb.versions.repository as repository
    monkeypatch.setattr(repository, 'PROCESSOR_VERSION', 'version-reference-sources-SYNTHETIC-2')
    second = build_snapshot(conn, config)
    assert first['snapshot_id'] != second['snapshot_id']
    assert read_snapshot(conn, first['snapshot_id']) == old
    assert counts(conn)['version_reference_snapshot'] == 2


def test_quality_claims_retain_page_arrays_without_asserting_physical_missing_pages(conn, config):
    registered(conn, config)
    # Append a new index revision with an explicit legacy extraction-quality claim.
    key = next(iter(INDEXES)); data = __import__('json').loads(config.source(key).read_text('utf8'))
    data[0]['attachments'][0].update({'extraction_status': 'extracted', 'page_count': 4,
                                     'empty_text_pages': [2], 'unreliable_text_pages': [3]})
    write_json(config.source_root / key, data); asset = preserve(config, key); insert(conn, 'source_asset', asset)
    old_claims = conn.execute("SELECT l.json_pointer,c.version_id FROM legacy_record l JOIN document_claim c USING(legacy_id) WHERE l.adapter='catalog'").fetchall()
    for i, raw in enumerate(data):
        lid = identity('quality-legacy', asset['asset_id'], i)
        insert(conn, 'legacy_record', {'legacy_id': lid, 'index_asset_id': asset['asset_id'], 'source_key': key,
               'json_pointer': '/' + str(i), 'legacy_key': raw['id'], 'adapter': 'catalog', 'importer_version': 'legacy-2', 'raw_payload': raw})
        vid = next(r['version_id'] for r in old_claims if r['json_pointer'] == '/' + str(i))
        insert(conn, 'document_claim', {'legacy_id': lid, 'version_id': vid})
    payload = collect_payload(conn, config)
    quality = next(q for v in payload['versions'] for q in v['quality_claims'] if q['empty_text_pages'])
    assert quality['empty_text_pages'] == [2] and quality['unreliable_text_pages'] == [3]
    assert quality['verification_state'] == 'unverified'
    assert any('不是声称PDF物理缺页' in g['reason'] for g in payload['gaps'] if g['kind'] == 'missing_pages')


def test_conflicting_catalog_roles_cannot_use_first_row_body_role(conn, config):
    versions = registered(conn, config)
    key = next(iter(INDEXES)); data = __import__('json').loads(config.source(key).read_text('utf8'))
    changed = dict(data[0], id='synthetic-amended-claim', is_amendment=True); data.append(changed)
    write_json(config.source_root / key, data); asset = preserve(config, key); insert(conn, 'source_asset', asset)
    for i, raw in enumerate(data):
        lid = identity('conflict-legacy', asset['asset_id'], i)
        insert(conn, 'legacy_record', {'legacy_id': lid, 'index_asset_id': asset['asset_id'], 'source_key': key,
               'json_pointer': '/' + str(i), 'legacy_key': raw['id'], 'adapter': 'catalog', 'importer_version': 'legacy-2', 'raw_payload': raw})
        insert(conn, 'document_claim', {'legacy_id': lid, 'version_id': versions[0 if i == 2 else i]})
    payload = collect_payload(conn, config); conflicted = next(v for v in payload['versions'] if v['version_id'] == versions[0])
    assert conflicted['body_assets'] == [] and conflicted['identity_state'] == 'conflicting'
    assert any(g['kind'] == 'source_conflict' and g['target']['id'] == versions[0] for g in payload['gaps'])


@pytest.mark.parametrize('late_key', ['000-first.txt', 'zzz-last.txt'])
def test_optimized_manifest_guard_rejects_late_first_or_last_member(conn, config, late_key):
    registered(conn, config); first = build_snapshot(conn, config); before = counts(conn)
    original = preserve(config, 'files/original.txt')
    with pytest.raises(psycopg.Error, match='VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'):
        with conn.transaction():
            insert(conn, 'version_reference_snapshot_source', {'snapshot_id': first['snapshot_id'],
                   'source_key': late_key, 'asset_id': original['asset_id']})
    assert counts(conn) == before


def test_optimized_count_gate_does_not_accept_same_count_wrong_asset(conn, config):
    registered(conn, config); payload = collect_payload(conn, config); before = counts(conn)
    wrong = preserve(config, next(iter(INDEXES)))
    with pytest.raises(psycopg.Error, match='VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'):
        with conn.transaction():
            insert(conn, 'version_reference_snapshot', {'snapshot_id': payload['snapshot_id'],
                'processor_version': payload['processor_version'], 'input_hash': payload['input_hash'],
                'payload_hash': digest(payload), 'payload': payload})
            for s in payload['sources']:
                insert(conn, 'version_reference_snapshot_source', {'snapshot_id': payload['snapshot_id'],
                   'source_key': s['source_key'], 'asset_id': wrong['asset_id'] if s['legacy_id'] is None else s['asset_id'],
                   'legacy_id': s['legacy_id']})
    assert counts(conn) == before


def test_optimized_manifest_count_is_derived_and_immediate_constraint_still_validates(conn, config):
    registered(conn, config); first = build_snapshot(conn, config)
    row = conn.execute('SELECT source_count,payload FROM version_reference_snapshot WHERE snapshot_id=%s', (first['snapshot_id'],)).fetchone()
    assert row['source_count'] == len(row['payload']['sources'])
    original = preserve(config, 'files/original.txt')
    with pytest.raises(psycopg.Error, match='VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'):
        with conn.transaction():
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            insert(conn, 'version_reference_snapshot_source', {'snapshot_id': first['snapshot_id'],
                   'source_key': '000-late-after-immediate.txt', 'asset_id': original['asset_id']})
