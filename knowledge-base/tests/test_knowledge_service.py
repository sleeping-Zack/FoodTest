"""PostgreSQL integration probes in the test schema, never business approvals."""
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from food_kb.common import KBError, write_json
from food_kb.chunking import service as chunks
from food_kb.db.session import insert
from food_kb.knowledge.extract import StructuredProvider
from food_kb.knowledge import service
from food_kb.review.service import append_review
from test_chunking_service import MANUAL_DIRECTORY, manual_package

pytestmark = pytest.mark.skipif(not (MANUAL_DIRECTORY / 'source-map.json').is_file(),
                               reason='Private manual source bundle is not distributed with public code.')


def counts(conn):
    return {table: conn.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n'] for table in (
        'extraction_run', 'knowledge_record', 'record_extraction', 'knowledge_validation',
        'knowledge_field_evidence', 'knowledge_evidence', 'relation', 'coverage_ledger',
        'knowledge_revision', 'review_record', 'knowledge_review_note', 'kb_release', 'reviewer')}


def register_version(conn, asset_id, label='one'):
    document_id = str(uuid4()); version_id = str(uuid4())
    insert(conn, 'source_document', {'document_id': document_id, 'source_key': 'SYNTHETIC-KB07-' + label,
        'kind': 'standard', 'standard_family': 'GB 1886.1', 'title': 'SYNTHETIC isolated test registration'})
    insert(conn, 'document_version', {'version_id': version_id, 'document_id': document_id,
        'designation': 'GB 1886.1-2015', 'revision': 'SYNTHETIC-' + label,
        'title': 'SYNTHETIC isolated test registration', 'identity_status': 'unverified'})
    insert(conn, 'version_asset', {'version_id': version_id, 'asset_id': asset_id, 'role': 'original'})
    return version_id


@pytest.fixture
def chunked(conn, config, manual_package):
    run = chunks.chunk_manual(conn, config, 'files/manual')
    bundle = chunks.read_run(conn, config, run['run_id'])
    version_id = register_version(conn, bundle['source']['asset_id'])
    return {'run': run, 'bundle': bundle, 'version_id': version_id}


@pytest.fixture
def extracted(conn, config, chunked):
    result = service.extract_knowledge(conn, config, chunked['run']['run_id'])
    return chunked | {'result': result, 'report': service.read_extraction(conn, config, result['extraction_id'])}


def reviewer(conn):
    actor = 'SYNTHETIC isolated test reviewer, real-domain rejection tests only'
    insert(conn, 'reviewer', {'actor': actor,
        'identity_reference': 'SYNTHETIC fixture identity restricted to isolated PostgreSQL test schema',
        'synthetic': False})
    insert(conn, 'reviewer_role', {'actor': actor, 'role': 'knowledge_reviewer'})
    return actor


def requirement(report):
    return next(item for item in report['records'] if item['candidate']['record_type'] == 'requirement')


def test_persistence_repeat_has_zero_delta_and_keeps_review_pending(conn, config, extracted):
    result = extracted['result']; report = extracted['report']
    assert result['status'] == 'completed'
    assert report['summary']['registered'] == len(report['records']) == 21
    assert report['summary']['approved'] == report['summary']['executable'] == report['summary']['publication_ready'] == 0
    assert {item['candidate']['record_type'] for item in report['records']} == {
        'standard_scope_catalog', 'requirement', 'definition', 'method'}
    assert all(item['review_state'] == 'review_pending' for item in report['records'])
    assert all(item['candidate']['executable'] is False for item in report['records'])
    before = counts(conn)
    second = service.extract_knowledge(conn, config, extracted['run']['run_id'])
    assert second['status'] == 'duplicate'
    assert second['extraction_id'] == result['extraction_id']
    assert counts(conn) == before
    assert service.read_extraction(conn, config, result['extraction_id']) == report
    assert conn.execute('SELECT count(*) AS n FROM standard_scope_catalog').fetchone()['n'] == 1


def test_every_populated_field_has_direct_same_parse_source_evidence(conn, extracted):
    rows = conn.execute('''SELECT k.record_id,k.payload,f.field_name,e.asset_id,e.parse_id,e.quote,b.text_raw,
        ke.role FROM knowledge_record k JOIN knowledge_field_evidence f USING(record_id)
        JOIN evidence_anchor e USING(evidence_id) JOIN content_block b USING(block_id)
        JOIN knowledge_evidence ke ON ke.record_id=k.record_id AND ke.evidence_id=e.evidence_id''').fetchall()
    assert rows
    for row in rows:
        assert row['role'] == 'direct'
        assert str(row['parse_id']) == extracted['bundle']['parse_id']
        assert row['asset_id'] == extracted['bundle']['source']['asset_id']
        assert row['quote'] in row['text_raw']
    for item in extracted['report']['records']:
        populated = {name for name, field in item['candidate']['fields'].items() if field['value'] is not None}
        evidenced = {row['field_name'] for row in rows if str(row['record_id']) == item['record_id']}
        assert populated <= evidenced


@pytest.mark.parametrize('target,error', [
    ('knowledge_report', 'KNOWLEDGE_REPORT_CHANGED'), ('chunk_report', 'CHUNK_REPORT_CHANGED'),
    ('preserved_source', 'CHUNK_SOURCE_CHANGED')])
def test_report_and_preserved_source_tampering_is_detected(conn, config, extracted, target, error):
    if target == 'knowledge_report':
        path = Path(extracted['result']['report'])
    elif target == 'chunk_report':
        path = Path(extracted['run']['report'])
    else:
        saved = extracted['bundle']['source']['preserved_files']['GB1886.1-2015.md']
        path = config.output_root / saved['storage_key']
    path.write_bytes(path.read_bytes() + b'\nSYNTHETIC tamper')
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.read_extraction(conn, config, extracted['result']['extraction_id'])
    assert caught.value.code == error
    assert counts(conn) == before


def test_unlinked_version_selection_is_rejected_before_writes(conn, config, chunked):
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.extract_knowledge(conn, config, chunked['run']['run_id'], version_id=str(uuid4()))
    assert caught.value.code == 'VERSION_SELECTION_REQUIRED'
    assert counts(conn) == before


def test_multiple_versions_require_explicit_selection(conn, config, chunked):
    other = register_version(conn, chunked['bundle']['source']['asset_id'], 'two')
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.extract_knowledge(conn, config, chunked['run']['run_id'])
    assert caught.value.code == 'VERSION_SELECTION_REQUIRED'
    assert counts(conn) == before
    first = service.extract_knowledge(conn, config, chunked['run']['run_id'], version_id=chunked['version_id'])
    second = service.extract_knowledge(conn, config, chunked['run']['run_id'], version_id=other)
    assert first['extraction_id'] != second['extraction_id']
    assert service.read_extraction(conn, config, first['extraction_id'])['document_metadata']['version_id'] == chunked['version_id']


def test_invalid_provider_keeps_rejected_candidate_and_unknown_coverage(conn, config, chunked):
    class InvalidProvider(StructuredProvider):
        name = 'SYNTHETIC-missing-source'; version = 'test-1'

        def extract(self, bundle, document):
            candidate = super().extract(bundle, document)[0]
            candidate['fields']['subject']['evidence'] = []
            return [candidate]

    result = service.extract_knowledge(conn, config, chunked['run']['run_id'], provider=InvalidProvider())
    report = service.read_extraction(conn, config, result['extraction_id'])
    assert result['status'] == 'completed_with_issues'
    assert len(report['rejected_candidates']) == 1
    assert report['records'] == []
    assert counts(conn)['knowledge_record'] == counts(conn)['knowledge_field_evidence'] == 0
    assert any(item['status'] == 'not_covered' for item in report['coverage'])
    assert all(item['status'] != 'passed' for item in report['coverage'])
    assert report['summary']['coverage_state'] != 'complete'


def test_same_provider_version_cannot_replace_output(conn, config, extracted):
    class ChangedProvider(StructuredProvider):
        def extract(self, bundle, document):
            candidates = super().extract(bundle, document)
            candidates[0]['title'] += ' SYNTHETIC changed output'
            return candidates

    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.extract_knowledge(conn, config, extracted['run']['run_id'], provider=ChangedProvider())
    assert caught.value.code == 'KNOWLEDGE_OUTPUT_CONFLICT'
    assert counts(conn) == before
    assert service.read_extraction(conn, config, extracted['result']['extraction_id']) == extracted['report']


def test_new_provider_version_preserves_previous_generation(conn, config, extracted):
    class RevisedProvider(StructuredProvider):
        version = 'SYNTHETIC-next-provider-version'

    before = counts(conn)
    second = service.extract_knowledge(conn, config, extracted['run']['run_id'], provider=RevisedProvider())
    assert second['extraction_id'] != extracted['result']['extraction_id']
    assert counts(conn)['knowledge_record'] == 2 * before['knowledge_record']
    assert service.read_extraction(conn, config, extracted['result']['extraction_id']) == extracted['report']


def test_provider_failure_and_nonfinite_output_write_nothing(conn, config, chunked):
    class FailedProvider(StructuredProvider):
        def extract(self, bundle, document):
            raise RuntimeError('SYNTHETIC provider failure')

    class NonfiniteProvider(StructuredProvider):
        def extract(self, bundle, document):
            candidates = super().extract(bundle, document)
            candidates[0]['fields']['subject']['value'] = float('nan')
            return candidates

    before = counts(conn)
    with pytest.raises(RuntimeError, match='SYNTHETIC provider'):
        service.extract_knowledge(conn, config, chunked['run']['run_id'], provider=FailedProvider())
    assert counts(conn) == before
    with pytest.raises(KBError):
        service.extract_knowledge(conn, config, chunked['run']['run_id'], provider=NonfiniteProvider())
    assert counts(conn) == before


def test_late_transaction_failure_rolls_back_all_knowledge_objects(conn, config, chunked, monkeypatch):
    original = service.insert

    def fail_late(connection, table, values):
        if table == 'knowledge_field_evidence':
            raise RuntimeError('SYNTHETIC late persist failure')
        return original(connection, table, values)

    monkeypatch.setattr(service, 'insert', fail_late)
    before = counts(conn)
    with pytest.raises(RuntimeError, match='SYNTHETIC late persist'):
        service.extract_knowledge(conn, config, chunked['run']['run_id'])
    assert counts(conn) == before


def test_revision_preserves_old_payload_and_is_idempotent(conn, config, extracted):
    actor = reviewer(conn); old = requirement(extracted['report'])
    payload = deepcopy(old['candidate']); payload['title'] += '（SYNTHETIC 审阅标题澄清）'
    reason = 'SYNTHETIC 隔离测试仅修改展示标题，保留原文值和证据，不授予审批'
    new = service.revise(conn, config, old['record_id'], payload, actor=actor, reason=reason)
    assert new['status'] == 'completed' and new['revision'].startswith('r2-')
    before = counts(conn)
    duplicate = service.revise(conn, config, old['record_id'], payload, actor=actor, reason=reason)
    assert duplicate['status'] == 'duplicate' and duplicate['record_id'] == new['record_id']
    assert counts(conn) == before
    stored_old = conn.execute('SELECT payload FROM knowledge_record WHERE record_id=%s', (old['record_id'],)).fetchone()
    stored_new = conn.execute('SELECT payload,review_state FROM knowledge_record WHERE record_id=%s', (new['record_id'],)).fetchone()
    assert stored_old['payload'] == old['candidate']
    assert stored_new['payload'] == payload and stored_new['review_state'] == 'review_pending'
    assert service.read_extraction(conn, config, extracted['result']['extraction_id']) == extracted['report']
    assert {row['revision'] for row in service.review_queue(conn, extracted['result']['extraction_id'])
            if row['logical_key'] == payload['logical_key']} == {old['revision'], new['revision']}


def test_revision_rejects_branching_and_changed_retry_reason(conn, config, extracted):
    actor = reviewer(conn); old = requirement(extracted['report'])
    payload = deepcopy(old['candidate']); payload['title'] += '（SYNTHETIC r2）'
    service.revise(conn, config, old['record_id'], payload, actor=actor, reason='SYNTHETIC first reason')
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.revise(conn, config, old['record_id'], payload, actor=actor, reason='SYNTHETIC changed reason')
    assert caught.value.code == 'REVISION_CONFLICT'
    payload['title'] += ' fork'
    with pytest.raises(KBError) as caught:
        service.revise(conn, config, old['record_id'], payload, actor=actor, reason='SYNTHETIC fork reason')
    assert caught.value.code == 'REVISION_SUPERSEDED'
    assert counts(conn) == before


@pytest.mark.parametrize('change,error', [('unknown_actor', 'UNKNOWN_REVIEWER'),
    ('no_reason', 'REVIEW_REASON_REQUIRED'), ('changed_limit', 'KNOWLEDGE_VALIDATION_FAILED'),
    ('identity', 'REVISION_IDENTITY')])
def test_revision_rejects_fake_identity_and_unsupported_changes(conn, config, extracted, change, error):
    actor = reviewer(conn); old = requirement(extracted['report']); payload = deepcopy(old['candidate'])
    reason = 'SYNTHETIC reject probe'
    if change == 'unknown_actor': actor = 'nonexistent expert'
    elif change == 'no_reason': reason = ' '
    elif change == 'changed_limit':
        old = next(item for item in extracted['report']['records']
                   if item['candidate']['record_type'] == 'requirement' and
                   item['candidate']['fields']['requirement_type']['value'] in {'upper_limit', 'lower_limit'})
        payload = deepcopy(old['candidate'])
        payload['fields']['value'].update(value='999', normalization_reason='擅改限值')
    elif change == 'identity': payload['logical_key'] += ':fake'
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.revise(conn, config, old['record_id'], payload, actor=actor, reason=reason)
    assert caught.value.code == error
    assert counts(conn) == before


def test_unverified_real_source_cannot_be_approved_by_test_role(conn, extracted):
    actor = reviewer(conn); item = requirement(extracted['report']); before = counts(conn)
    with pytest.raises(KBError) as caught:
        append_review(conn, item['record_id'], 'approved', actor, 'SYNTHETIC rejection probe, no actual approval')
    assert caught.value.code == 'KNOWLEDGE_VALIDATION_REQUIRED'
    assert counts(conn) == before


def test_new_processor_cannot_submit_a_method_without_coverage(conn, config, chunked):
    class MissingCoverage(StructuredProvider):
        def extract(self, bundle, metadata):
            candidates = super().extract(bundle, metadata)
            for candidate in candidates:
                if candidate['record_type'] == 'method':
                    candidate.pop('semantic_coverage')
            return candidates
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.extract_knowledge(conn, config, chunked['run']['run_id'], provider=MissingCoverage())
    assert caught.value.code == 'KNOWLEDGE_VALIDATION_FAILED'
    assert counts(conn) == before


def test_method_revision_cannot_hide_limits_by_reverting_to_legacy_shape(conn, config, extracted):
    actor = reviewer(conn)
    old = next(r for r in extracted['report']['records'] if r['candidate']['record_type'] == 'method')
    payload = deepcopy(old['candidate'])
    payload.pop('semantic_coverage')
    for key in ('method_anchor', 'method_checks', 'calculation_dependencies'):
        payload['fields'].pop(key)
    payload['fields']['sections']['value'] = [{'anchor': r['anchor'], 'title': r['title'],
        'text': 'SYNTHETIC attempt to remove the partial semantics gate'} for r in payload['fields']['sections']['value']]
    before = counts(conn)
    with pytest.raises(KBError) as caught:
        service.revise(conn, config, old['record_id'], payload, actor=actor, reason='SYNTHETIC downgrade rejection')
    assert caught.value.code == 'KNOWLEDGE_VALIDATION_FAILED'
    assert counts(conn) == before


def test_field_evidence_cannot_be_attached_to_fake_field(conn, extracted):
    row = conn.execute('SELECT * FROM knowledge_field_evidence LIMIT 1').fetchone()
    with pytest.raises(psycopg.Error, match='UNKNOWN_KNOWLEDGE_FIELD'):
        with conn.transaction():
            conn.execute('INSERT INTO knowledge_field_evidence(record_id,field_name,evidence_id) VALUES(%s,%s,%s)',
                         (row['record_id'], 'SYNTHETIC invented field', row['evidence_id']))


def test_cli_extract_report_queue_and_unregistered_review(conn, config, extracted, monkeypatch, capsys):
    from food_kb import cli
    monkeypatch.setattr(cli.Config, 'load', staticmethod(lambda _: config))
    monkeypatch.setattr(cli, 'connect', lambda _: nullcontext(conn))
    before = counts(conn)
    assert cli.main(['extract-knowledge', extracted['run']['run_id']]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'duplicate'
    assert cli.main(['knowledge-report', extracted['result']['extraction_id']]) == 0
    assert json.loads(capsys.readouterr().out)['summary']['registered'] == 21
    assert cli.main(['knowledge-review-queue', '--extraction-id', extracted['result']['extraction_id']]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 21
    item = requirement(extracted['report'])
    assert cli.main(['review-knowledge', item['record_id'], '--action', 'approved', '--actor', 'unknown',
                     '--reason', 'SYNTHETIC rejection probe']) == 1
    assert json.loads(capsys.readouterr().err)['error_code'] == 'UNKNOWN_REVIEWER'
    assert counts(conn) == before


def test_cli_revision_rejects_unknown_actor_without_registering_identity(conn, config, extracted, monkeypatch, capsys, tmp_path):
    from food_kb import cli
    monkeypatch.setattr(cli.Config, 'load', staticmethod(lambda _: config))
    monkeypatch.setattr(cli, 'connect', lambda _: nullcontext(conn))
    item = requirement(extracted['report']); path = tmp_path / 'SYNTHETIC-candidate.json'
    write_json(path, item['candidate'])
    before = counts(conn)
    assert cli.main(['revise-knowledge', item['record_id'], str(path), '--actor', 'unknown', '--reason', 'SYNTHETIC probe']) == 1
    assert json.loads(capsys.readouterr().err)['error_code'] == 'UNKNOWN_REVIEWER'
    assert counts(conn) == before
