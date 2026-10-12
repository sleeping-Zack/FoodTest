"""Synthetic fixture tests; these decisions do not represent expert approval."""
import hashlib
import psycopg
from psycopg.types.json import Jsonb
import pytest

from food_kb.common import KBError
from food_kb.db.repository import register_parse
from food_kb.db.session import insert
from food_kb.review.service import append_review
from food_kb.releases.service import publish
from test_schema import base, release as raw_release, uid


def actor(conn, name='synthetic.fixture.reviewer', role='knowledge_reviewer', synthetic=True):
    insert(conn, 'reviewer', {'actor': name, 'identity_reference': 'SYNTHETIC fixture; not a real expert', 'synthetic': synthetic})
    if role:
        insert(conn, 'reviewer_role', {'actor': name, 'role': role})
    return name


def candidate(conn, synthetic=True, quality='synthetic', ready=True, predecessor=None):
    if predecessor:
        previous = conn.execute('SELECT * FROM knowledge_record WHERE record_id=%s', (predecessor,)).fetchone()
        version = previous['version_id']; logical = previous['logical_key']
        asset = conn.execute('SELECT asset_id FROM version_asset WHERE version_id=%s', (version,)).fetchone()['asset_id']
    else:
        asset, _, version = base(conn, uid()); logical = uid()
    rid, eid = uid(), uid()
    payload = {'schema_version': 'knowledge-1', 'record_type': 'definition', 'template': 'product',
        'fields': {'subject': {'value': 'SYNTHETIC'}, 'definition': {'value': 'SYNTHETIC revised definition' if predecessor else 'SYNTHETIC definition'}}}
    insert(conn, 'knowledge_record', {'record_id': rid, 'logical_key': logical, 'revision': '2' if predecessor else '1',
        'record_type': 'definition', 'version_id': version, 'payload': payload,
        'extraction_run': 'SYNTHETIC fixture', 'review_state': 'review_pending', 'synthetic': synthetic})
    values = {'evidence_id': eid, 'asset_id': asset, 'locator': {'pdf_page': 1}, 'locator_quality': quality}
    if quality in {'verified', 'page_claim'}:
        parse = register_parse(conn, asset, 'SYNTHETIC fixture', uid(), uid(), 'usable'); block = uid()
        quote = 'SYNTHETIC definition'
        insert(conn, 'content_block', {'block_id': block, 'parse_id': parse, 'structural_key': 'definition', 'kind': 'section',
            'ordinal': 0, 'text_raw': quote, 'locator': {'pdf_page': 1}})
        values |= {'parse_id': parse, 'block_id': block, 'quote': quote, 'quote_hash': hashlib.sha256(quote.encode()).hexdigest()}
    insert(conn, 'evidence_anchor', values)
    insert(conn, 'knowledge_evidence', {'record_id': rid, 'evidence_id': eid, 'asset_id': asset, 'version_id': version, 'role': 'direct'})
    for field in payload['fields']:
        insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': field, 'evidence_id': eid})
    insert(conn, 'knowledge_validation', {'record_id': rid, 'schema_version': 'knowledge-1', 'passed': True,
        'publication_ready': ready, 'report': {'issues': [], 'publication_blockers': [] if ready else ['unresolved_condition']}})
    if predecessor:
        who = actor(conn)
        insert(conn, 'knowledge_revision', {'record_id': rid, 'previous_record_id': predecessor, 'actor': who,
            'reason': 'SYNTHETIC fixture correction, preserving the old revision'})
    return rid, eid


def approved(conn):
    rid, eid = candidate(conn); who = actor(conn)
    review = append_review(conn, rid, 'approved', who, 'SYNTHETIC fixture source checked', eid)
    return rid, eid, who, review


def publisher(conn):
    return actor(conn, 'synthetic.fixture.publisher', 'knowledge_publisher')


def test_review_requires_preexisting_identity_role_and_reason(conn):
    rid, _ = candidate(conn)
    for who in ['unknown-person', actor(conn, 'synthetic.fixture.submitter', None)]:
        with pytest.raises(KBError): append_review(conn, rid, 'approved', who, 'SYNTHETIC')
    assert not conn.execute("SELECT 1 FROM reviewer WHERE actor='unknown-person'").fetchone()
    assert conn.execute('SELECT count(*) AS n FROM review_record').fetchone()['n'] == 0
    with pytest.raises(KBError, match='nonempty'): append_review(conn, rid, 'rejected', actor(conn), '  ')


def test_approval_pins_named_revision_fields_and_real_review_time(conn):
    rid, eid, who, decision = approved(conn)
    assert str(decision['record_id']) == rid and str(decision['evidence_id']) == eid
    assert decision['actor'] == who and decision['reviewed_at'] and decision['action'] == 'approved'
    assert conn.execute('SELECT effective_review_state FROM knowledge_status WHERE record_id=%s', (rid,)).fetchone()['effective_review_state'] == 'approved'
    with pytest.raises(psycopg.Error): conn.execute('UPDATE knowledge_record SET payload=%s WHERE record_id=%s', (Jsonb({'modified': True}), rid))
    asset = conn.execute('SELECT asset_id FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone()['asset_id']
    extra = uid()
    insert(conn, 'evidence_anchor', {'evidence_id': extra, 'asset_id': asset, 'locator': {'pdf_page': 1}, 'locator_quality': 'synthetic'})
    with pytest.raises(psycopg.Error, match='REVIEWED_FIELDS_SEALED'):
        insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': 'definition', 'evidence_id': extra})


def test_human_cannot_create_machine_result_or_rewrite_terminal_decision(conn):
    rid, _, who, _ = approved(conn)
    for action in ('machine_checked', 'rejected', 'review_pending'):
        with pytest.raises(KBError): append_review(conn, rid, action, who, 'SYNTHETIC')


def test_rejection_is_a_terminal_append_only_decision(conn):
    rid, _ = candidate(conn); who = actor(conn)
    decision = append_review(conn, rid, 'reject', who, 'SYNTHETIC fixture source conflict')
    assert decision['action'] == 'rejected'
    with pytest.raises(KBError): append_review(conn, rid, 'approve', who, 'SYNTHETIC')
    with pytest.raises(KBError): publish(conn, [rid], publisher(conn), synthetic=True)


def test_page_claim_cannot_become_real_expert_approval(conn):
    rid, eid = candidate(conn, synthetic=False, quality='page_claim')
    who = actor(conn, 'real.fixture.reviewer', synthetic=False)
    with pytest.raises(KBError, match='verified'): append_review(conn, rid, 'approved', who, 'SYNTHETIC real-domain negative fixture', eid)
    assert conn.execute('SELECT count(*) AS n FROM review_record').fetchone()['n'] == 0


def test_synthetic_identity_must_be_explicit_fixture_and_match_domain(conn):
    rid, _ = candidate(conn)
    insert(conn, 'reviewer', {'actor': 'pretend-expert', 'identity_reference': 'Expert', 'synthetic': True})
    insert(conn, 'reviewer_role', {'actor': 'pretend-expert', 'role': 'knowledge_reviewer'})
    with pytest.raises(KBError, match='fixture'): append_review(conn, rid, 'approved', 'pretend-expert', 'SYNTHETIC')
    wrong = actor(conn, 'real.fixture.reviewer', synthetic=False)
    with pytest.raises(KBError, match='separate'): append_review(conn, rid, 'approved', wrong, 'SYNTHETIC')


def test_missing_field_anchor_and_unresolved_validation_block_approval(conn):
    rid, _ = candidate(conn, ready=False)
    with pytest.raises(KBError, match='validation'): append_review(conn, rid, 'approved', actor(conn), 'SYNTHETIC')
    asset, _, version = base(conn, uid()); other = uid()
    insert(conn, 'knowledge_record', {'record_id': other, 'logical_key': other, 'revision': '1', 'record_type': 'definition',
        'version_id': version, 'payload': {'fields': {'subject': {'value': 'SYNTHETIC'}, 'definition': {'value': 'missing evidence'}}},
        'extraction_run': 'SYNTHETIC', 'synthetic': True, 'review_state': 'review_pending'})
    insert(conn, 'knowledge_validation', {'record_id': other, 'schema_version': 'knowledge-1', 'passed': True, 'publication_ready': True, 'report': {}})
    with pytest.raises(KBError, match='own source'): append_review(conn, other, 'approved', actor(conn), 'SYNTHETIC')


def test_review_evidence_cannot_belong_to_another_revision(conn):
    rid, _ = candidate(conn); _, other = candidate(conn)
    with pytest.raises(KBError, match='this revision'): append_review(conn, rid, 'approved', actor(conn), 'SYNTHETIC', other)


def test_supplement_requires_new_revision_and_comment_cannot_clear_it(conn):
    rid, _ = candidate(conn); who = actor(conn)
    note = append_review(conn, rid, 'supplement_requested', who, 'SYNTHETIC fixture requires additional source')
    assert note['effective_review_state'] == 'review_pending'
    append_review(conn, rid, 'comment', who, 'SYNTHETIC comment does not supply a revised record')
    with pytest.raises(KBError, match='new knowledge revision'): append_review(conn, rid, 'approved', who, 'SYNTHETIC')
    revised, _ = candidate(conn, predecessor=rid)
    assert append_review(conn, revised, 'approved', who, 'SYNTHETIC revised source verified')['action'] == 'approved'


def test_publish_requires_explicit_approval_and_publisher_role(conn):
    rid, _ = candidate(conn)
    with pytest.raises(KBError, match='explicit latest approval'): publish(conn, [rid], publisher(conn), synthetic=True)
    rid2, _, reviewer, _ = approved(conn)
    with pytest.raises(KBError, match='role'): publish(conn, [rid2], reviewer, synthetic=True)
    with pytest.raises(KBError): publish(conn, [], publisher(conn), synthetic=True)
    with pytest.raises(KBError): publish(conn, [rid2, rid2], publisher(conn), synthetic=True)
    assert conn.execute('SELECT count(*) AS n FROM kb_release').fetchone()['n'] == 0


def test_release_snapshot_is_idempotent_explicit_and_nonexecutable(conn):
    rid, _, _, review = approved(conn); who = publisher(conn)
    release = publish(conn, [rid], who, synthetic=True)
    assert release['manifest']['knowledge'] == [rid] and release['manifest']['bindings'] == []
    assert release['manifest']['knowledge_snapshot'][0]['review_id'] == str(review['review_id'])
    assert release['manifest']['executable'] is False and release['index_readiness']['semantic'] is False
    again = publish(conn, [rid], who, synthetic=True)
    assert again['duplicate'] is True and again['release_id'] == release['release_id']
    assert conn.execute('SELECT actor FROM release_authorization WHERE release_id=%s', (release['release_id'],)).fetchone()['actor'] == who


def test_revision_does_not_change_old_release_or_old_value(conn):
    rid, _, who, old_review = approved(conn); pub = publisher(conn)
    old_release = publish(conn, [rid], pub, synthetic=True)
    old_payload = conn.execute('SELECT payload FROM knowledge_record WHERE record_id=%s', (rid,)).fetchone()['payload']
    new_rid, _ = candidate(conn, predecessor=rid)
    new_review = append_review(conn, new_rid, 'approved', who, 'SYNTHETIC revised source verified')
    new_release = publish(conn, [new_rid], pub, parent_release_id=old_release['release_id'], synthetic=True)
    assert old_release['release_id'] != new_release['release_id']
    old_member = conn.execute('SELECT * FROM release_knowledge WHERE release_id=%s', (old_release['release_id'],)).fetchone()
    assert str(old_member['record_id']) == rid and old_member['review_id'] == old_review['review_id']
    assert new_release['manifest']['knowledge_snapshot'][0]['review_id'] == str(new_review['review_id'])
    assert conn.execute('SELECT payload FROM knowledge_record WHERE record_id=%s', (rid,)).fetchone()['payload'] == old_payload


def test_governance_blocks_new_release_and_preserves_old_snapshot(conn):
    rid, _, who, review = approved(conn); pub = publisher(conn)
    old = publish(conn, [rid], pub, synthetic=True)
    append_review(conn, rid, 'disputed', who, 'SYNTHETIC fixture conflict discovered')
    with pytest.raises(KBError, match='governance'): publish(conn, [rid], pub, synthetic=True)
    assert conn.execute('SELECT review_id FROM release_knowledge WHERE release_id=%s', (old['release_id'],)).fetchone()['review_id'] == review['review_id']
    append_review(conn, rid, 'reinstated', who, 'SYNTHETIC conflict resolved using the unchanged original source')
    assert publish(conn, [rid], pub, synthetic=True)['release_id'] == old['release_id']
    append_review(conn, rid, 'withdrawn', who, 'SYNTHETIC fixture withdrawn')
    with pytest.raises(KBError): publish(conn, [rid], pub, synthetic=True)


def test_pending_cannot_be_skipped_and_events_follow_insertion_order(conn):
    rid, eid = candidate(conn)
    # A separate fixture begins at extracted, as legacy extraction adapters do.
    row = conn.execute('SELECT * FROM knowledge_record WHERE record_id=%s', (rid,)).fetchone()
    second = uid()
    insert(conn, 'knowledge_record', {'record_id': second, 'logical_key': second, 'revision': '1', 'record_type': 'definition',
        'version_id': row['version_id'], 'payload': row['payload'], 'extraction_run': 'SYNTHETIC', 'synthetic': True})
    who = actor(conn)
    with pytest.raises(KBError, match='skip pending'): append_review(conn, second, 'approved', who, 'SYNTHETIC', eid)
    with conn.transaction():
        pending = append_review(conn, second, 'review_pending', who, 'SYNTHETIC queued for review')
        rejected = append_review(conn, second, 'rejected', who, 'SYNTHETIC fixture rejection')
    assert pending['event_sequence'] < rejected['event_sequence']


def test_release_cannot_mix_real_and_synthetic_domains(conn):
    rid, _, _, _ = approved(conn)
    real_pub = actor(conn, 'real.fixture.publisher', 'knowledge_publisher', synthetic=False)
    with pytest.raises(KBError, match='mixed'): publish(conn, [rid], real_pub, synthetic=False)
    assert conn.execute('SELECT count(*) AS n FROM kb_release').fetchone()['n'] == 0


def test_sql_approval_cannot_bypass_role_or_validation(conn):
    rid, eid = candidate(conn); who = actor(conn, role=None)
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC fixture bypass attempt', 'evidence_id': eid})
    insert(conn, 'reviewer_role', {'actor': who, 'role': 'knowledge_reviewer'})
    blocked, evidence = candidate(conn, ready=False)
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': blocked, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC fixture bypass attempt', 'evidence_id': evidence})


def test_sql_release_requires_separate_publication_authorization(conn):
    rid, _, _, review = approved(conn)
    with pytest.raises(psycopg.Error): raw_release(conn, rid, review['review_id'])
    assert conn.execute('SELECT count(*) AS n FROM kb_release').fetchone()['n'] == 0


def bind_extraction(conn, rid, parse_id, chunk_parse=None, synthetic=True):
    version = conn.execute('SELECT version_id FROM knowledge_record WHERE record_id=%s', (rid,)).fetchone()['version_id']
    run, chunk, extraction = uid(), uid(), uid()
    insert(conn, 'chunk_run', {'run_id': run, 'parse_id': parse_id, 'strategy_version': 'SYNTHETIC', 'config_hash': uid(),
        'configuration': {}, 'output_hash': uid(), 'report_key': 'fixtures/chunks.json', 'report_sha256': '0' * 64, 'source_manifest': {}})
    insert(conn, 'chunk', {'chunk_id': chunk, 'parse_id': chunk_parse or parse_id, 'strategy_version': 'SYNTHETIC',
        'content_hash': '0' * 64, 'retrieval_text': 'SYNTHETIC', 'chunk_run_id': run if chunk_parse is None else None})
    insert(conn, 'extraction_run', {'extraction_id': extraction, 'chunk_run_id': run, 'version_id': version,
        'processor_version': 'SYNTHETIC', 'config_hash': uid(), 'output_hash': uid(),
        'report_key': 'fixtures/extraction.json', 'report_sha256': '0' * 64, 'synthetic': synthetic})
    insert(conn, 'record_extraction', {'record_id': rid, 'extraction_id': extraction, 'source_chunk_ids': [chunk]})


def test_sql_page_claim_cannot_bypass_real_field_approval(conn):
    rid, eid = candidate(conn, synthetic=False, quality='page_claim')
    source = conn.execute('SELECT parse_id FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone()
    bind_extraction(conn, rid, source['parse_id'], synthetic=False)
    who = actor(conn, 'real.fixture.reviewer', synthetic=False)
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC fixture bypass attempt', 'evidence_id': eid})


def test_sql_field_parse_is_rechecked_when_attaching_extraction_after_fields(conn):
    rid, eid = candidate(conn, quality='verified')
    evidence = conn.execute('SELECT * FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone()
    wrong = register_parse(conn, evidence['asset_id'], 'SYNTHETIC other parse', uid(), uid(), 'usable')
    with pytest.raises(psycopg.Error): bind_extraction(conn, rid, wrong)


def test_sql_extraction_chunks_must_belong_to_declared_chunk_run(conn):
    rid, eid = candidate(conn, quality='verified')
    evidence = conn.execute('SELECT * FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone()
    other = register_parse(conn, evidence['asset_id'], 'SYNTHETIC other parse', uid(), uid(), 'usable')
    with pytest.raises(psycopg.Error): bind_extraction(conn, rid, evidence['parse_id'], chunk_parse=other)


def test_service_rejects_real_verified_record_without_extraction_chain(conn):
    rid, eid = candidate(conn, synthetic=False, quality='verified')
    with pytest.raises(KBError, match='original extraction'):
        append_review(conn, rid, 'approved', actor(conn, 'real.fixture.reviewer', synthetic=False), 'SYNTHETIC', eid)


def test_sql_dispute_note_generates_governance_and_blocks_approval(conn):
    rid, eid = candidate(conn); who = actor(conn)
    note = uid()
    insert(conn, 'knowledge_review_note', {'note_id': note, 'record_id': rid, 'actor': who,
        'action': 'disputed', 'reason': 'SYNTHETIC direct SQL source conflict'})
    assert conn.execute('SELECT action FROM governance_event WHERE event_id=%s', (note,)).fetchone()['action'] == 'disputed'
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC bypass attempt', 'evidence_id': eid})


def test_sql_supplement_cannot_be_added_after_terminal_decision(conn):
    rid, _, who, _ = approved(conn)
    with pytest.raises(psycopg.Error):
        insert(conn, 'knowledge_review_note', {'note_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'supplement_requested', 'reason': 'SYNTHETIC request requires a new revision'})


def test_verified_quote_with_fabricated_page_is_rejected_at_approval(conn):
    rid, eid = candidate(conn, quality='verified'); who = actor(conn)
    source = conn.execute('SELECT * FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone(); fake = uid()
    insert(conn, 'evidence_anchor', {'evidence_id': fake, 'asset_id': source['asset_id'], 'parse_id': source['parse_id'],
        'block_id': source['block_id'], 'quote': source['quote'], 'quote_hash': source['quote_hash'],
        'locator_quality': 'verified', 'locator': {'pdf_page': 999}})
    version = conn.execute('SELECT version_id FROM knowledge_record WHERE record_id=%s', (rid,)).fetchone()['version_id']
    insert(conn, 'knowledge_evidence', {'record_id': rid, 'evidence_id': fake, 'asset_id': source['asset_id'],
        'version_id': version, 'role': 'direct'})
    insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': 'definition', 'evidence_id': fake})
    with pytest.raises(KBError, match='pages and lines'): append_review(conn, rid, 'approved', who, 'SYNTHETIC', fake)
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC bypass attempt', 'evidence_id': fake})


def test_sql_field_anchor_without_direct_membership_cannot_approve(conn):
    rid, eid = candidate(conn); who = actor(conn)
    original = conn.execute('SELECT * FROM evidence_anchor WHERE evidence_id=%s', (eid,)).fetchone(); other = uid()
    insert(conn, 'evidence_anchor', {'evidence_id': other, 'asset_id': original['asset_id'],
        'locator': {'pdf_page': 1}, 'locator_quality': 'synthetic'})
    insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': 'definition', 'evidence_id': other})
    with pytest.raises(KBError, match='verified direct'): append_review(conn, rid, 'approved', who, 'SYNTHETIC')
    with pytest.raises(psycopg.Error):
        insert(conn, 'review_record', {'review_id': uid(), 'record_id': rid, 'actor': who,
            'action': 'approved', 'reason': 'SYNTHETIC bypass attempt', 'evidence_id': eid})


def test_sealed_field_membership_allows_identical_retry(conn):
    rid, eid, _, _ = approved(conn)
    assert insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': 'definition', 'evidence_id': eid}) is None
