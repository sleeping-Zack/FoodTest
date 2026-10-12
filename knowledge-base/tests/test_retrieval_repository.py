"""Synthetic source snapshots in isolated PostgreSQL schemas, never approvals."""
from copy import deepcopy
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb
import pytest

from food_kb.common import KBError, digest, file_hash, identity, write_json
from food_kb.db.repository import register_parse
from food_kb.db.session import insert
from food_kb.retrieval import repository, sources
from food_kb.retrieval.service import search
from food_kb.storage.assets import preserve


def uid():
    return str(uuid4())


@pytest.fixture
def selected_sources(conn, config):
    raw = 'SYNTHETIC 苹果 苹果汁 色泽 碳酸钠 ♞，仅隔离工程夹具，非食品标准。'
    path = config.source_root / 'files/original.txt'; path.write_text(raw, encoding='utf8')
    asset = preserve(config, 'files/original.txt'); insert(conn, 'source_asset', asset)
    doc, version, chunk_id, extraction_id, taxid, vsid = [uid() for _ in range(6)]
    insert(conn, 'source_document', {'document_id': doc, 'source_key': doc, 'kind': 'standard',
                                   'title': 'SYNTHETIC', 'standard_family': 'GB 1886.1'})
    insert(conn, 'document_version', {'version_id': version, 'document_id': doc, 'designation': 'GB 1886.1-2015',
                                    'revision': 'SYNTHETIC', 'title': 'SYNTHETIC'})
    parse = register_parse(conn, asset['asset_id'], 'SYNTHETIC', 'SYNTHETIC', 'SYNTHETIC', 'usable')
    ev = {'asset_id': asset['asset_id'], 'pdf_pages': [1], 'line_start': 1, 'line_end': 1,
          'locator_quality': 'manual_page_and_line'}
    block_id, cid = uid(), uid()
    bundle = {'run_id': chunk_id, 'source': {'preserved_files': {'original.txt': asset}}, 'blocks': [],
        'chunks': [{'chunk_id': cid, 'title': 'SYNTHETIC 原文', 'kind': 'section', 'anchor': 'A.1',
                    'raw_markdown': raw, 'source': ev, 'retrieval_enabled': True},
                   {'chunk_id': uid(), 'title': 'SYNTHETIC 上下文', 'kind': 'section', 'anchor': 'document',
                    'raw_markdown': raw, 'source': ev, 'retrieval_enabled': False}]}
    report_path = config.output_root / 'synthetic-chunks.json'; write_json(report_path, bundle)
    insert(conn, 'chunk_run', {'run_id': chunk_id, 'parse_id': parse, 'strategy_version': 'SYNTHETIC',
        'config_hash': digest('SYNTHETIC'), 'configuration': {}, 'output_hash': digest(bundle),
        'report_key': report_path.name, 'report_sha256': file_hash(report_path), 'source_manifest': {'original.txt': asset}})
    field = lambda v: {'value': v, 'raw': v, 'evidence': [{'quote': raw, 'source': ev, 'block_id': block_id}]}
    knowledge = {'extraction_id': extraction_id, 'chunk_run_id': chunk_id,
        'document_metadata': {'version_id': version, 'designation': 'GB 1886.1-2015'}, 'records': [{
            'record_id': uid(), 'review_state': 'rejected', 'candidate': {'record_type': 'requirement',
                'logical_key': version + ':requirement:A.1', 'condition': {'state': 'unresolved', 'raw': 'SYNTHETIC'},
                'fields': {'subject': field('碳酸钠'), 'project': field('色泽'), 'value': field('SYNTHETIC'),
                    'method_reference': field('NY/T 123-2020'), 'basis': field('GB 2762-2022'),
                    'requirement_type': field('qualitative')}}}]}
    kp = config.output_root / 'synthetic-knowledge.json'; write_json(kp, knowledge)
    insert(conn, 'extraction_run', {'extraction_id': extraction_id, 'chunk_run_id': chunk_id, 'version_id': version,
        'processor_version': 'SYNTHETIC', 'config_hash': digest('SYNTHETIC'), 'output_hash': digest(knowledge),
        'report_key': kp.name, 'report_sha256': file_hash(kp), 'synthetic': True})
    source = {'source_key': 'files/original.txt', **asset, 'legacy_id': None}
    source.pop('media_type')
    rowev = [{'source_key': source['source_key'], 'source_asset_id': asset['asset_id'],
              'quote': raw, 'locator': {'pdf_page': 1}, 'locator_quality': 'page_claim'}]
    nodes = [{'id': uid(), 'name': name, 'code': name, 'names': [{'value': name, 'kind': 'canonical'},
                 {'value': 'SYNTHETIC未审别名', 'kind': 'normalization_hint'}], 'definition': {'raw': name},
              'condition': {'state': 'unresolved', 'raw': 'SYNTHETIC'}, 'exclusions': [], 'evidence': rowev,
              'review_state': 'review_pending'} for name in ['苹果', '苹果汁']]
    req = {'id': uid(), 'node_id': nodes[0]['id'], 'table_id': 'SYNTHETIC-table',
        'semantic': {'project': '色泽', 'method_raw': 'NY/T 123-2020', 'basis_raw': 'GB 2762-2022'},
        'condition': {'state': 'unresolved', 'raw': 'SYNTHETIC'}, 'exclusions': [],
        'review_state': 'review_pending', 'evidence': rowev}
    ns = {'namespace': 'SYNTHETIC', 'standard_version_id': version, 'nodes': nodes,
          'relations': [], 'requirements': [req], 'gaps': []}
    tax = {'schema_version': 'taxonomy-1', 'processor_version': 'SYNTHETIC', 'snapshot_id': taxid,
           'input_hash': digest('SYNTHETIC'), 'sources': [source], 'namespaces': [ns],
           'review_state': 'review_pending', 'scope_complete': False, 'executable': False}
    v = {'version_id': version, 'document_id': doc, 'family': 'GB 1886.1', 'designation': 'GB 1886.1-2015',
         'title': 'SYNTHETIC 元数据', 'review_state': 'review_pending', 'evidence': rowev}
    versions = {'schema_version': 'version-reference-1', 'processor_version': 'SYNTHETIC', 'snapshot_id': vsid,
        'input_hash': digest('SYNTHETIC'), 'sources': [source], 'versions': [v], 'references': [],
        'events': [], 'gaps': [], 'dependencies': [], 'review_state': 'review_pending',
        'scope_complete': False, 'executable': False}
    with conn.transaction():
        for table, member, payload in [('taxonomy_snapshot', 'taxonomy_snapshot_source', tax),
                                       ('version_reference_snapshot', 'version_reference_snapshot_source', versions)]:
            insert(conn, table, {'snapshot_id': payload['snapshot_id'], 'processor_version': 'SYNTHETIC',
                'input_hash': payload['input_hash'], 'payload_hash': digest(payload), 'payload': payload})
            insert(conn, member, {'snapshot_id': payload['snapshot_id'], 'source_key': source['source_key'],
                                  'asset_id': source['asset_id'], 'legacy_id': None})
    ids = {'chunk_run_id': chunk_id, 'knowledge_run_id': extraction_id,
           'taxonomy_snapshot_id': taxid, 'version_snapshot_id': vsid}
    return {'ids': ids, 'bundle': bundle, 'knowledge': knowledge, 'taxonomy': tax, 'versions': versions,
            'asset': asset, 'report_path': report_path}


@pytest.fixture
def built(conn, config, selected_sources):
    result = repository.build_generation(conn, config, selected_sources['ids'])
    return selected_sources | {'result': result, 'corpus': repository.read_generation(conn, result['generation_id'])}


def counts(conn):
    return {t: conn.execute('SELECT count(*) n FROM ' + t).fetchone()['n'] for t in
            ('retrieval_generation', 'retrieval_generation_source', 'retrieval_record', 'knowledge_record',
             'review_record', 'source_asset', 'kb_release')}


def test_fixed_collection_readonly_and_repeat_zero_delta(conn, config, selected_sources):
    before = counts(conn)
    corpus = sources.collect_payload(conn, config, selected_sources['ids'])
    assert counts(conn) == before and corpus['summary']['records'] == 6
    first = repository.build_generation(conn, config, selected_sources['ids']); after = counts(conn)
    assert first['status'] == 'success' and repository.read_generation(conn, first['generation_id']) == corpus
    assert repository.build_generation(conn, config, selected_sources['ids'])['status'] == 'duplicate'
    assert counts(conn) == after and first['payload_hash'] == digest(corpus)
    assert corpus['summary']['accepted_body_chunks'] == 1 and corpus['summary']['context_chunks_retained'] == 1
    assert all(not r['executable'] for r in corpus['records'])


def test_source_review_and_method_judgment_roles_preserved(conn, config, built):
    gid = built['result']['generation_id']
    rejected = search(conn, gid, mode='exact', filters={'review_state': 'rejected'})
    assert rejected['total_count'] == 1 and rejected['hits'][0]['review_state'] == 'rejected'
    method = search(conn, gid, mode='exact', filters={'method_standard': 'ＮＹ／Ｔ １２３－２０２０'})
    assert method['total_count'] == 2
    assert search(conn, gid, mode='exact', filters={'judgment_standard': 'NY/T123-2020'})['total_count'] == 0
    assert search(conn, gid, mode='exact', filters={'method_standard': 'GB2762-2022'})['total_count'] == 0


@pytest.mark.parametrize('missing', ['chunk_run_id', 'knowledge_run_id', 'taxonomy_snapshot_id', 'version_snapshot_id'])
def test_no_implicit_latest_or_missing_source_selection(conn, config, selected_sources, missing):
    ids = dict(selected_sources['ids']); ids.pop(missing)
    with pytest.raises(KBError, match='four fixed'):
        sources.collect_payload(conn, config, ids)
    assert counts(conn)['retrieval_generation'] == 0


def test_changed_fixed_report_or_preserved_bytes_refuses_duplicate(conn, config, built):
    gid = built['result']['generation_id']; before = counts(conn)
    built['report_path'].write_text('SYNTHETIC changed', encoding='utf8')
    with pytest.raises(KBError, match='missing or changed'):
        repository.build_generation(conn, config, built['ids'])
    assert counts(conn) == before and repository.read_generation(conn, gid) == built['corpus']


def test_preserved_source_changed_even_with_frozen_snapshots(conn, config, built):
    saved = config.output_root / built['asset']['storage_key']; saved.write_text('SYNTHETIC changed', encoding='utf8')
    with pytest.raises(KBError, match='preserved source'):
        repository.build_generation(conn, config, built['ids'])


@pytest.mark.parametrize('table', ['retrieval_generation', 'retrieval_generation_source', 'retrieval_record'])
def test_search_history_append_only(conn, built, table):
    with pytest.raises(psycopg.Error):
        conn.execute('DELETE FROM ' + table)
    column = {'retrieval_generation': 'processor_version', 'retrieval_generation_source': 'source_key',
              'retrieval_record': 'record_type'}[table]
    with pytest.raises(psycopg.Error):
        conn.execute('UPDATE ' + table + ' SET ' + column + '=' + column)


def test_late_extra_source_and_record_manifest_rejected(conn, built):
    gid = built['result']['generation_id']; before = counts(conn)
    with pytest.raises(psycopg.Error, match='MANIFEST'):
        insert(conn, 'retrieval_generation_source', {'generation_id': gid, 'source_key': 'z/SYNTHETIC-extra',
                                                    'asset_id': built['asset']['asset_id']})
    old = conn.execute('SELECT * FROM retrieval_record WHERE generation_id=%s LIMIT 1', (gid,)).fetchone()
    item = deepcopy(old['payload']); item['record_id'] = uid()
    with pytest.raises(psycopg.Error, match='MANIFEST'):
        conn.execute('''INSERT INTO retrieval_record(generation_id,record_id,record_type,payload,payload_hash,index_hash,
          exact_terms,raw_text,normalized_text,tokens) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
          (gid, item['record_id'], old['record_type'], Jsonb(item), digest(item), old['index_hash'],
           Jsonb(old['exact_terms']), old['raw_text'], old['normalized_text'], old['tokens']))
    assert counts(conn) == before


@pytest.mark.parametrize('mode', ['exact', 'literal_raw', 'normalized_phrase', 'zh_ngram_phrase'])
def test_database_queries_are_readonly_and_keep_unknown_conditions(conn, built, mode):
    before = counts(conn); gid = built['result']['generation_id']
    result = search(conn, gid, '苹果', mode=mode, filters={'record_type': 'classification'})
    assert result['status'] == 'success' and result['total_count'] == 2
    assert result['unknown_conditions'] == 2 and all(h['applicability'] == 'unknown' for h in result['hits'])
    assert counts(conn) == before and not result['trace']['postgres_candidates']['whole_corpus_python_scan']
    if mode != 'exact':
        assert result['hits'][0]['display_fields']['name'] == '苹果'
        assert any(m['match_kind'] == 'related_name' for m in result['hits'][1]['matches'])


@pytest.mark.parametrize('query', ['不存在的SYNTHETIC', '苹果'])
def test_missing_gin_is_degraded_even_with_empty_results(conn, built, query):
    conn.execute('DROP INDEX retrieval_record_tokens_gin')
    result = search(conn, built['result']['generation_id'], query)
    assert result['status'] == 'degraded' and result['effective_mode'] == 'normalized_phrase'
    assert result['missing_capabilities'] == ['physical_lexical_gin']
    assert result['faults'][0]['code'] == 'RETRIEVAL_INDEX_UNAVAILABLE'
    if query.startswith('不存在'):
        assert not result['hits'] and result['empty_reason'] != 'no_records'


def test_nonindexed_symbol_uses_honest_literal_fallback(conn, built):
    result = search(conn, built['result']['generation_id'], '♞')
    assert result['status'] == 'degraded' and result['total_count'] == 3
    assert result['missing_capabilities'] == ['indexed_tokens_for_query']
    assert {h['source_channel'] for h in result['hits']} == {'accepted_manual_body', 'knowledge_fact', 'inspection_row_candidate'}
    assert all(any(m['matched_text'] == '♞' for m in h['matches']) for h in result['hits'])


def test_unreviewed_name_hint_not_indexed_as_exact_entity(conn, built):
    assert search(conn, built['result']['generation_id'], 'SYNTHETIC未审别名')['total_count'] == 0


@pytest.mark.parametrize('column', ['exact_terms', 'raw_text', 'normalized_text', 'tokens'])
def test_derived_index_corruption_never_normal_empty(conn, built, column):
    # Maintenance tampering is simulated only in this newly created test schema.
    conn.execute('ALTER TABLE retrieval_record DISABLE TRIGGER immutable')
    values = {'exact_terms': "'{}'::jsonb", 'raw_text': "'SYNTHETIC altered'", 'normalized_text': "''", 'tokens': "''::tsvector"}
    conn.execute('UPDATE retrieval_record SET ' + column + '=' + values[column])
    result = search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'failed' and result['total_count'] is None
    assert result['faults'][0]['code'] == 'RETRIEVAL_GENERATION_CORRUPT'


def test_database_failure_not_normal_empty(conn, built):
    conn.execute('ALTER TABLE retrieval_record RENAME TO synthetic_unavailable_records')
    result = search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'failed' and result['total_count'] is None
    assert result['faults'][0]['code'] == 'RETRIEVAL_DATABASE_FAILED'


def test_missing_generation_has_explicit_failure(conn):
    result = search(conn, uid(), '苹果')
    assert result['status'] == 'failed' and result['faults'][0]['code'] == 'RETRIEVAL_GENERATION_MISSING'


@pytest.mark.parametrize('variant', ['missing_source', 'wrong_source_identity', 'missing_record', 'wrong_record_identity'])
def test_partial_or_equal_count_wrong_manifest_rolls_back(conn, built, variant):
    oldid = built['result']['generation_id']; before = counts(conn)
    old = conn.execute('SELECT * FROM retrieval_generation WHERE generation_id=%s', (oldid,)).fetchone()
    header = deepcopy(old['payload']); newid = uid(); header['generation_id'] = newid
    header['input_hash'] = digest(['SYNTHETIC bad manifest', variant, newid])
    values = {k: old[k] for k in ('processor_version', 'corpus_hash', 'chunk_run_id', 'knowledge_run_id',
        'taxonomy_snapshot_id', 'version_snapshot_id', 'source_count', 'record_count')}
    values |= {'generation_id': newid, 'input_hash': header['input_hash'], 'payload_hash': digest(header), 'payload': header}
    if variant == 'wrong_source_identity':
        header['sources'][0]['source_key'] = 'SYNTHETIC wrong equal-count key'
        values['payload_hash'] = digest(header)
    with pytest.raises(psycopg.Error, match='MANIFEST'):
        with conn.transaction():
            insert(conn, 'retrieval_generation', values)
            members = conn.execute('SELECT source_key,asset_id FROM retrieval_generation_source WHERE generation_id=%s ORDER BY source_key', (oldid,)).fetchall()
            for s in members[:-1] if variant == 'missing_source' else members:
                insert(conn, 'retrieval_generation_source', {'generation_id': newid, **s})
            rows = conn.execute('SELECT * FROM retrieval_record WHERE generation_id=%s ORDER BY record_id', (oldid,)).fetchall()
            for i, r in enumerate(rows[:-1] if variant == 'missing_record' else rows):
                if variant == 'wrong_record_identity' and i == 0:
                    r['payload_hash'] = digest('SYNTHETIC wrong equal-count hash')
                conn.execute('''INSERT INTO retrieval_record(generation_id,record_id,record_type,payload,payload_hash,index_hash,
                    exact_terms,raw_text,normalized_text,tokens) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (newid, r['record_id'], r['record_type'], Jsonb(r['payload']), r['payload_hash'], r['index_hash'],
                     Jsonb(r['exact_terms']), r['raw_text'], r['normalized_text'], r['tokens']))
    assert counts(conn) == before


def test_same_generation_new_content_rejected_not_replaced(conn, built):
    before = counts(conn); changed = deepcopy(built['corpus'])
    changed['records'][0]['fields']['title'] = 'SYNTHETIC changed output under same source processor'
    with pytest.raises(KBError, match='append a new processor'):
        repository.persist_generation(conn, changed)
    assert counts(conn) == before


def test_partial_gin_is_not_reported_as_healthy(conn, built):
    conn.execute('DROP INDEX retrieval_record_tokens_gin')
    conn.execute("CREATE INDEX retrieval_record_tokens_gin ON retrieval_record USING gin(tokens) WHERE record_type='chunk'")
    result = search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'degraded' and result['missing_capabilities'] == ['physical_lexical_gin']


def test_missing_exact_index_is_explicit_degraded(conn, built):
    conn.execute('DROP INDEX retrieval_record_exact_gin')
    result = search(conn, built['result']['generation_id'], mode='exact', filters={'project': '不存在的SYNTHETIC'})
    assert result['status'] == 'degraded' and result['total_count'] == 0
    assert result['missing_capabilities'] == ['physical_exact_gin']


def test_outdated_configuration_not_silently_reinterpreted(conn, built, monkeypatch):
    from food_kb.retrieval import service
    monkeypatch.setattr(service, 'TOKENIZER_VERSION', 'SYNTHETIC-new-tokenizer')
    result = service.search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'failed' and result['faults'][0]['code'] == 'UNSUPPORTED_RETRIEVAL_CONFIG'


def test_index_query_exception_rolls_back_and_fallback_is_degraded(conn, built, monkeypatch):
    from food_kb.retrieval import service
    original = service._query_rows; calls = []
    def faulty(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise psycopg.errors.InternalError('SYNTHETIC injected index fault, not a real source error')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, '_query_rows', faulty)
    result = service.search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'degraded' and result['total_count'] == 5
    assert {h['source_channel'] for h in result['hits']} == {'accepted_manual_body', 'knowledge_fact',
                                                            'inspection_row_candidate', 'classification_candidate'}
    assert result['faults'][0]['code'] == 'RETRIEVAL_INDEX_QUERY_FAILED' and len(calls) == 2
    assert conn.execute('SELECT 1 AS alive').fetchone()['alive'] == 1


def test_fallback_exception_returns_failed_without_secret_error_text(conn, built, monkeypatch):
    from food_kb.retrieval import service
    def faulty(*args, **kwargs):
        raise psycopg.errors.InternalError('SYNTHETIC sensitive error must not leak')
    monkeypatch.setattr(service, '_query_rows', faulty)
    result = service.search(conn, built['result']['generation_id'], '苹果')
    assert result['status'] == 'failed' and result['total_count'] is None
    assert 'sensitive' not in str(result)
