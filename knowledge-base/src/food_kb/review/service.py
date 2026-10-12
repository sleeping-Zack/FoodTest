"""Append decisions to a named revision; never create identities or roles."""
from uuid import uuid4

from ..common import KBError
from ..db.session import insert
from ..knowledge.templates import RECORD_FIELDS


def require_actor(conn, actor, role, synthetic):
    reviewer = conn.execute('SELECT * FROM reviewer WHERE actor=%s', (actor,)).fetchone()
    if not reviewer:
        raise KBError('UNKNOWN_REVIEWER', 'The actor must be registered before reviewing or publishing.')
    if reviewer['synthetic'] is not synthetic:
        raise KBError('REVIEW_DOMAIN_MISMATCH', 'Synthetic fixtures and real knowledge require separate identities.')
    if synthetic and not all(word in reviewer['identity_reference'].lower() for word in ('synthetic', 'fixture')):
        raise KBError('SYNTHETIC_IDENTITY_REQUIRED', 'Synthetic decisions require an explicitly identified test fixture.')
    if not conn.execute('SELECT 1 FROM reviewer_role WHERE actor=%s AND role=%s', (actor, role)).fetchone():
        raise KBError('REVIEW_ROLE_REQUIRED', 'The actor does not have the required review or publication role.')
    return reviewer


def publication_check(conn, record):
    """Check field evidence and unresolved dispositions, independently of approval."""
    rid = record['record_id']
    if record['record_type'] not in RECORD_FIELDS:
        raise KBError('UNKNOWN_RECORD_TYPE', 'Unknown knowledge types cannot be approved or published.')
    validation = conn.execute('SELECT * FROM knowledge_validation WHERE record_id=%s', (rid,)).fetchone()
    if not validation or not validation['passed'] or not validation['publication_ready']:
        raise KBError('KNOWLEDGE_VALIDATION_REQUIRED', 'Schema, conditions, units and sources must pass publication validation.')
    if conn.execute("SELECT 1 FROM knowledge_review_note WHERE record_id=%s AND action='supplement_requested'", (rid,)).fetchone():
        raise KBError('SUPPLEMENT_REQUIRED', 'The requested supplement must be supplied as a new knowledge revision.')
    event = conn.execute('SELECT action FROM governance_event WHERE record_id=%s ORDER BY event_sequence DESC LIMIT 1', (rid,)).fetchone()
    if event and event['action'] in ('withdrawn', 'disputed', 'superseded'):
        raise KBError('GOVERNANCE_BLOCKED', 'The current governance decision prevents approval or publication.')
    version = conn.execute('SELECT identity_status FROM document_version WHERE version_id=%s', (record['version_id'],)).fetchone()
    if not version or version['identity_status'] != 'verified':
        raise KBError('VERSION_IDENTITY_REQUIRED', 'The source version must have a verified identity.')
    evidence = conn.execute('''SELECT f.field_name,e.*,p.asset_id AS parsed_asset,ke.role
        FROM knowledge_field_evidence f JOIN evidence_anchor e USING(evidence_id)
        LEFT JOIN parse_run p USING(parse_id)
        LEFT JOIN knowledge_evidence ke ON ke.record_id=f.record_id AND ke.evidence_id=f.evidence_id
        WHERE f.record_id=%s''', (rid,)).fetchall()
    extraction = conn.execute('''SELECT cr.parse_id FROM record_extraction re
        JOIN extraction_run er USING(extraction_id) JOIN chunk_run cr ON cr.run_id=er.chunk_run_id
        WHERE re.record_id=%s''', (rid,)).fetchone()
    fields = record['payload'].get('fields', {})
    required = {key for key, value in fields.items() if isinstance(value, dict) and value.get('value') is not None}
    if not required or not required.issubset({row['field_name'] for row in evidence}):
        raise KBError('FIELD_EVIDENCE_REQUIRED', 'Every populated knowledge field requires its own source evidence.')
    allowed = {'verified', 'synthetic'} if record['synthetic'] else {'verified'}
    for row in evidence:
        if row['locator_quality'] not in allowed or row['role'] != 'direct':
            raise KBError('VERIFIED_FIELD_EVIDENCE_REQUIRED', 'Every field anchor must be verified direct evidence in the same source version.')
        if not conn.execute('SELECT 1 FROM version_asset WHERE version_id=%s AND asset_id=%s', (record['version_id'], row['asset_id'])).fetchone():
            raise KBError('SOURCE_VERSION_MISMATCH', 'Field evidence does not belong to the knowledge source version.')
        if extraction and row['parse_id'] != extraction['parse_id']:
            raise KBError('SOURCE_PARSE_MISMATCH', 'Field evidence must belong to the extraction source parse.')
        if row['locator_quality'] == 'verified':
            if not row['parse_id'] or not row['block_id'] or not row['quote'] or row['parsed_asset'] != row['asset_id']:
                raise KBError('VERIFIED_FIELD_EVIDENCE_REQUIRED', 'Verified field anchors must identify a parsed block and exact quotation.')
            block = conn.execute('SELECT text_raw,locator FROM content_block WHERE block_id=%s AND parse_id=%s', (row['block_id'], row['parse_id'])).fetchone()
            if not block or row['quote'] not in block['text_raw']:
                raise KBError('QUOTE_NOT_IN_BLOCK', 'The field quotation must occur in its original parsed block.')
            if any(key not in row['locator'] or row['locator'][key] != value for key, value in block['locator'].items()):
                raise KBError('SOURCE_LOCATION_MISMATCH', 'Field pages and lines must match the original parsed block location.')
    if not extraction and not record['synthetic']:
        raise KBError('SOURCE_EXTRACTION_REQUIRED', 'Real knowledge must retain the original extraction and chunk run.')
    return validation, evidence


def append_review(conn, record_id, action, actor, reason, evidence_id=None):
    """Record a human decision on precisely one immutable revision."""
    action = {'approve': 'approved', 'reject': 'rejected'}.get(action, action)
    if action not in {'review_pending', 'approved', 'rejected', 'supplement_requested', 'comment',
                      'disputed', 'withdrawn', 'reinstated', 'superseded'}:
        raise KBError('INVALID_REVIEW_ACTION', 'Human review cannot create a machine validation decision.')
    if not isinstance(reason, str) or not reason.strip():
        raise KBError('REVIEW_REASON_REQUIRED', 'A review decision requires a nonempty reason.')
    with conn.transaction():
        record = conn.execute('SELECT * FROM knowledge_record WHERE record_id=%s FOR UPDATE', (record_id,)).fetchone()
        if not record:
            raise KBError('KNOWLEDGE_NOT_FOUND', 'The requested knowledge revision does not exist.')
        require_actor(conn, actor, 'knowledge_reviewer', record['synthetic'])
        latest = conn.execute('SELECT action FROM review_record WHERE record_id=%s ORDER BY event_sequence DESC LIMIT 1', (record_id,)).fetchone()
        previous = latest['action'] if latest else record['review_state']
        if action in {'supplement_requested', 'comment', 'disputed'}:
            if action == 'supplement_requested' and previous not in {'extracted', 'machine_checked', 'review_pending'}:
                raise KBError('ILLEGAL_REVIEW_TRANSITION', 'Terminal decisions require a new revision before requesting a supplement.')
            event = insert(conn, 'knowledge_review_note', {'note_id': str(uuid4()), 'record_id': record_id,
                'actor': actor, 'action': action, 'reason': reason.strip()})
            return dict(event) | {'effective_review_state': previous}
        if action in {'withdrawn', 'reinstated', 'superseded'}:
            governance = conn.execute('SELECT action FROM governance_event WHERE record_id=%s ORDER BY event_sequence DESC LIMIT 1', (record_id,)).fetchone()
            if action == 'reinstated' and (not governance or governance['action'] not in {'withdrawn', 'disputed', 'superseded'}):
                raise KBError('ILLEGAL_GOVERNANCE_TRANSITION', 'Reinstatement requires an existing blocking governance decision.')
            if action != 'reinstated' and previous != 'approved':
                raise KBError('ILLEGAL_GOVERNANCE_TRANSITION', 'Withdrawal or supersession applies to an approved revision.')
            return dict(insert(conn, 'governance_event', {'event_id': str(uuid4()), 'record_id': record_id,
                'actor': actor, 'action': action, 'reason': reason.strip()})) | {'effective_review_state': previous}
        transitions = {'extracted': {'review_pending', 'rejected'}, 'machine_checked': {'review_pending', 'rejected'},
                       'review_pending': {'approved', 'rejected'}}
        if action not in transitions.get(previous, set()):
            raise KBError('ILLEGAL_REVIEW_TRANSITION', 'The review decision cannot skip pending review or overwrite a terminal decision.')
        if action == 'approved':
            _, evidence = publication_check(conn, record)
            available = {str(row['evidence_id']) for row in evidence}
            if evidence_id is None:
                evidence_id = sorted(available)[0]
            elif str(evidence_id) not in available:
                raise KBError('REVIEW_EVIDENCE_MISMATCH', 'The approval evidence must belong to this revision and its field evidence.')
        elif evidence_id is not None and not conn.execute('SELECT 1 FROM knowledge_evidence WHERE record_id=%s AND evidence_id=%s', (record_id, evidence_id)).fetchone():
            raise KBError('REVIEW_EVIDENCE_MISMATCH', 'The review evidence must belong to this revision.')
        return dict(insert(conn, 'review_record', {'review_id': str(uuid4()), 'record_id': record_id,
            'action': action, 'actor': actor, 'reason': reason.strip(), 'evidence_id': evidence_id}))
