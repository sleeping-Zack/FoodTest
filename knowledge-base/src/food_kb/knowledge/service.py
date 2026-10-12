"""Persist immutable field-bound candidates and preserve reviewed revisions."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ..common import KBError, canonical, digest, file_hash, identity, read_json
from ..chunking.service import read_run
from ..db.session import insert
from ..review.service import require_actor
from .extract import PROCESSOR_VERSION, StructuredProvider
from .templates import SCHEMA_VERSION
from .validate import validate_candidates


def _path(config, key):
    path = (config.output_root / key).resolve()
    if not path.is_relative_to(config.output_root.resolve()):
        raise KBError('PATH_BOUNDARY', 'Knowledge report is outside the configured output directory.')
    return path


def _store(config, report):
    content = (canonical(report) + '\n').encode('utf8')
    sha = hashlib.sha256(content).hexdigest()
    key = f'knowledge-runs/{report["extraction_id"]}/{sha}.json'
    target = _path(config, key); target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix='.knowledge-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_hash(target) != sha:
                raise KBError('KNOWLEDGE_REPORT_CHANGED', 'Existing knowledge artifact differs; no overwrite performed.')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return key, sha


def _anchors(value):
    if isinstance(value, dict):
        if {'block_id', 'quote', 'source'} <= value.keys():
            yield value
        else:
            for item in value.values():
                yield from _anchors(item)
    elif isinstance(value, list):
        for item in value:
            yield from _anchors(item)


def _document(conn, bundle, version_id=None):
    rows = conn.execute('''SELECT v.* FROM document_version v JOIN version_asset a USING(version_id)
        WHERE a.asset_id=%s ORDER BY v.version_id''', (bundle['source']['asset_id'],)).fetchall()
    rows = [r for r in rows if version_id is None or str(r['version_id']) == str(version_id)]
    if len(rows) != 1:
        raise KBError('VERSION_SELECTION_REQUIRED', 'Select exactly one registered version linked to this original source.')
    return {key: str(value) if value is not None else None for key, value in rows[0].items()}


def _checked(validation, candidate, document):
    result = {key: value for key, value in validation.items() if key != 'logical_key'}
    result['publication_blockers'] = list(result['publication_blockers'])
    if document['identity_status'] != 'verified':
        result['publication_blockers'].append({'code': 'DOCUMENT_IDENTITY_UNVERIFIED',
            'message': '标准版本身份尚未核验。'})
    # The manual package has page/line claims, not independently verified field locators.
    if any(a['source'].get('locator_quality') != 'verified' for a in _anchors(candidate)):
        result['publication_blockers'].append({'code': 'FIELD_LOCATOR_UNVERIFIED',
            'message': '字段定位仍为人工转录页码与行号声明，须核验原页后审核。'})
    return result


def _coverage(bundle, records):
    result = []
    for chunk in bundle['chunks']:
        linked = [r['record_id'] for r in records if chunk['chunk_id'] in r['candidate']['source_chunk_ids']]
        if linked:
            status = 'passed'; reason = '原文已关联候选知识，可展开来源；条款关联不等于全部语义已结构化。'
        elif not chunk['retrieval_enabled']:
            status = 'not_applicable'; reason = '导航或整表上下文保留在切分结果中，不作为独立知识。'
        else:
            status = 'not_covered'; reason = '当前确定性抽取器未识别此内容；原文仍保留，需人工标注或后续解析。'
        result.append({'chunk_id': chunk['chunk_id'], 'anchor': chunk['anchor'], 'status': status,
                       'record_ids': linked, 'reason': reason})
    return result


def _require_method_representation(candidates, processor_version):
    if processor_version in {'knowledge-facts-3', 'knowledge-facts-4'} and any(
            c.get('record_type') == 'method' and 'semantic_coverage' not in c
            for c in candidates if isinstance(c, dict)):
        raise KBError('KNOWLEDGE_VALIDATION_FAILED', 'This extraction version requires explicit partial method semantic coverage; a revision cannot remove it.')


def _persist_record(conn, item, extraction_id, version_id, bundle):
    candidate = item['candidate']; validation = item['validation']; rid = item['record_id']
    values = {'record_id': rid, 'logical_key': candidate['logical_key'], 'revision': item['revision'],
        'record_type': candidate['record_type'], 'version_id': version_id, 'payload': candidate,
        'extraction_run': str(extraction_id), 'review_state': 'review_pending', 'synthetic': False,
        'field_states': {name: value.get('state', 'candidate' if value['value'] is not None else 'unknown')
                         for name, value in candidate['fields'].items()}}
    if insert(conn, 'knowledge_record', values) is None:
        raise KBError('KNOWLEDGE_REVISION_CONFLICT', 'Knowledge revision already belongs to another extraction.')
    insert(conn, 'record_extraction', {'record_id': rid, 'extraction_id': extraction_id,
                                      'source_chunk_ids': candidate['source_chunk_ids']})
    insert(conn, 'knowledge_validation', {'record_id': rid, 'report': validation,
        'passed': validation['passed'], 'publication_ready': validation['passed'] and not validation['publication_blockers'],
        'schema_version': SCHEMA_VERSION})

    def register(anchor, role):
        source = anchor['source']; quote = anchor['quote']
        eid = identity('knowledge-field-evidence', bundle['parse_id'], anchor['block_id'], digest(anchor))
        locator = source | ({'cell': anchor['cell']} if 'cell' in anchor else {})
        insert(conn, 'evidence_anchor', {'evidence_id': eid, 'asset_id': source['asset_id'],
            'parse_id': bundle['parse_id'], 'block_id': anchor['block_id'], 'locator': locator,
            'quote': quote, 'quote_hash': hashlib.sha256(quote.encode('utf8')).hexdigest(),
            'locator_quality': 'verified' if source.get('locator_quality') == 'verified' else 'page_claim'})
        insert(conn, 'knowledge_evidence', {'record_id': rid, 'evidence_id': eid, 'asset_id': source['asset_id'],
                                           'version_id': version_id, 'role': role})
        return eid

    for name, value in candidate['fields'].items():
        for anchor in _anchors(value):
            eid = register(anchor, 'direct')
            insert(conn, 'knowledge_field_evidence', {'record_id': rid, 'field_name': name, 'evidence_id': eid})
    for section in ('condition', 'references', 'relations'):
        for anchor in _anchors(candidate.get(section)):
            register(anchor, 'supporting')


def extract_knowledge(conn, config, chunk_run_id, *, version_id=None, provider=None):
    bundle = read_run(conn, config, chunk_run_id)
    document = _document(conn, bundle, version_id)
    provider = provider or StructuredProvider()
    configuration = {'provider': provider.name, 'provider_version': provider.version,
                     'schema_version': SCHEMA_VERSION, 'document_metadata': document}
    config_hash = digest(configuration)
    extraction_id = identity('knowledge-extraction', chunk_run_id, PROCESSOR_VERSION, config_hash)
    candidates = provider.extract(bundle, document)
    if not isinstance(candidates, list):
        raise KBError('PROVIDER_OUTPUT_INVALID', 'Extraction provider must return a JSON candidate array.')
    # Provider outputs are data only. JSON serialization rejects objects and non-finite numbers.
    try:
        json.dumps(candidates, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise KBError('PROVIDER_OUTPUT_INVALID', 'Extraction provider must return finite JSON data only.') from exc
    _require_method_representation(candidates, PROCESSOR_VERSION)
    validation = validate_candidates(candidates, bundle)
    records = []
    for candidate, checked in zip(candidates, validation['candidates']):
        if not checked['passed']:
            continue
        revision = f'r1-{extraction_id[:8]}-{digest(candidate)[:12]}'
        records.append({'record_id': identity('knowledge-record', candidate['logical_key'], revision),
            'revision': revision, 'review_state': 'review_pending', 'candidate': candidate,
            'validation': _checked(checked, candidate, document)})
    coverage = _coverage(bundle, records)
    report = {'extraction_id': extraction_id, 'chunk_run_id': str(chunk_run_id), 'parse_id': bundle['parse_id'],
        'configuration': configuration, 'document_metadata': document, 'source': bundle['source'],
        'records': records, 'rejected_candidates': [c for c, v in zip(candidates, validation['candidates']) if not v['passed']],
        'validation': validation, 'coverage': coverage, 'blocks': bundle['blocks'], 'chunks': bundle['chunks'],
        'summary': {'candidates': len(candidates), 'registered': len(records), 'review_pending': len(records),
            'approved': 0, 'executable': 0, 'machine_passed': sum(v['passed'] for v in validation['candidates']),
            'publication_ready': sum(not r['validation']['publication_blockers'] for r in records),
            'coverage_state': 'partial', 'selected_document_only': True,
            'method_semantics': 'partial_source_linked', 'formula_recalculation': 'unsupported'}}
    output_hash = digest(report); report_key, report_sha = _store(config, report)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042007)')
        old = conn.execute('SELECT * FROM extraction_run WHERE extraction_id=%s', (extraction_id,)).fetchone()
        if old:
            if old['output_hash'] != output_hash:
                raise KBError('KNOWLEDGE_OUTPUT_CONFLICT', 'Provider output changed; increment provider version instead of replacing a run.')
            read_extraction(conn, config, extraction_id)
            return {'status': 'duplicate', 'extraction_id': extraction_id, 'records': len(records),
                    'report': str(_path(config, report_key))}
        insert(conn, 'extraction_run', {'extraction_id': extraction_id, 'chunk_run_id': chunk_run_id,
            'version_id': document['version_id'], 'processor_version': PROCESSOR_VERSION, 'config_hash': config_hash,
            'output_hash': output_hash, 'report_key': report_key, 'report_sha256': report_sha, 'synthetic': False})
        for item in records:
            _persist_record(conn, item, extraction_id, document['version_id'], bundle)
        by_key = {r['candidate']['logical_key']: r['record_id'] for r in records}
        for item in records:
            for rel in item['candidate'].get('relations', []):
                anchor = next(_anchors(rel), None)
                if anchor is None:
                    continue
                eid = identity('knowledge-field-evidence', bundle['parse_id'], anchor['block_id'], digest(anchor))
                insert(conn, 'relation', {'relation_id': identity('knowledge-relation', item['record_id'], digest(rel)),
                    'from_record': item['record_id'], 'to_record': by_key.get(rel['target_key']),
                    'target_claim': rel['target_key'], 'kind': rel['kind'], 'evidence_id': eid,
                    'scope': item['candidate']['condition'], 'version_binding': {'version_id': document['version_id']},
                    'review_state': 'extracted'})
        for item in coverage:
            insert(conn, 'coverage_ledger', {'scope_id': extraction_id, 'target_ref': item['chunk_id'],
                'domain': 'knowledge', 'stage': 'KB-07-extraction', 'status': item['status'], 'reason': item['reason']})
    return {'status': 'completed' if validation['passed'] and records else 'completed_with_issues',
            'extraction_id': extraction_id, 'records': len(records), 'report': str(_path(config, report_key))}


def read_extraction(conn, config, extraction_id):
    row = conn.execute('SELECT * FROM extraction_run WHERE extraction_id=%s', (extraction_id,)).fetchone()
    if not row:
        raise KBError('NOT_FOUND', 'Knowledge extraction does not exist.')
    read_run(conn, config, str(row['chunk_run_id']))
    path = _path(config, row['report_key'])
    if not path.is_file() or file_hash(path) != row['report_sha256']:
        raise KBError('KNOWLEDGE_REPORT_CHANGED', 'Stored knowledge report is missing or changed.')
    report = read_json(path)
    if digest(report) != row['output_hash']:
        raise KBError('KNOWLEDGE_REPORT_CHANGED', 'Knowledge report fingerprint differs from its registration.')
    return report


def review_queue(conn, extraction_id=None):
    return conn.execute('''SELECT k.record_id,k.logical_key,k.revision,k.record_type,k.version_id,
        k.effective_review_state AS review_state,k.payload,v.report AS validation,r.extraction_id,
        (SELECT coalesce(jsonb_agg(to_jsonb(n) ORDER BY event_sequence),'[]') FROM knowledge_review_note n
         WHERE n.record_id=k.record_id) AS notes
        FROM knowledge_status k JOIN knowledge_validation v USING(record_id)
        JOIN record_extraction r USING(record_id)
        WHERE (%s::uuid IS NULL OR r.extraction_id=%s::uuid) ORDER BY k.logical_key,k.revision''',
        (extraction_id, extraction_id)).fetchall()


def revise(conn, config, record_id, candidate, *, actor, reason):
    if not isinstance(candidate, dict):
        raise KBError('KNOWLEDGE_VALIDATION_FAILED', 'A revision candidate must be a JSON object.')
    if not isinstance(reason, str) or not reason.strip():
        raise KBError('REVIEW_REASON_REQUIRED', 'A revision must explain what changed and why.')
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042007)')
        old = conn.execute('''SELECT k.*,r.extraction_id,x.processor_version FROM knowledge_record k
            JOIN record_extraction r USING(record_id) JOIN extraction_run x USING(extraction_id)
            WHERE k.record_id=%s''', (record_id,)).fetchone()
        if old is None:
            raise KBError('NOT_FOUND', 'An extracted knowledge revision is required.')
        require_actor(conn, actor, 'knowledge_reviewer', old['synthetic'])
        if old['synthetic']:
            raise KBError('REVISION_DOMAIN', 'This source extraction service handles real source candidates only.')
        report = read_extraction(conn, config, str(old['extraction_id']))
        bundle = read_run(conn, config, report['chunk_run_id'])
        if candidate.get('logical_key') != old['logical_key'] or candidate.get('record_type') != old['record_type']:
            raise KBError('REVISION_IDENTITY', 'Revision must retain its logical identity and knowledge type.')
        peers = [row['payload'] for row in conn.execute('''SELECT k.payload FROM knowledge_record k
            JOIN record_extraction r USING(record_id) WHERE r.extraction_id=%s AND k.logical_key<>%s
            AND NOT EXISTS(SELECT 1 FROM knowledge_revision n WHERE n.previous_record_id=k.record_id)''',
            (old['extraction_id'], old['logical_key'])).fetchall()]
        _require_method_representation([candidate], old['processor_version'])
        validation = validate_candidates(peers + [candidate], bundle)['candidates'][-1]
        if not validation['passed']:
            raise KBError('KNOWLEDGE_VALIDATION_FAILED', canonical(validation['issues']))
        number = int(re.match(r'r(\d+)-', old['revision'])[1]) + 1
        revision = f'r{number}-{str(old["extraction_id"])[:8]}-{digest(candidate)[:12]}'
        rid = identity('knowledge-record', old['logical_key'], revision)
        previous = conn.execute('SELECT * FROM knowledge_revision WHERE record_id=%s', (rid,)).fetchone()
        if previous:
            if str(previous['previous_record_id']) != str(record_id) or previous['actor'] != actor or previous['reason'] != reason:
                raise KBError('REVISION_CONFLICT', 'Repeated revision differs from its original actor or reason.')
            return {'status': 'duplicate', 'record_id': rid, 'previous_record_id': str(record_id), 'revision': revision}
        if conn.execute('SELECT 1 FROM knowledge_revision WHERE previous_record_id=%s', (record_id,)).fetchone():
            raise KBError('REVISION_SUPERSEDED', 'Revise the latest revision instead of branching from an older one.')
        item = {'record_id': rid, 'revision': revision, 'candidate': candidate,
                'validation': _checked(validation, candidate, report['document_metadata'])}
        _persist_record(conn, item, str(old['extraction_id']), str(old['version_id']), bundle)
        insert(conn, 'knowledge_revision', {'record_id': rid, 'previous_record_id': record_id, 'actor': actor, 'reason': reason})
    return {'status': 'completed', 'record_id': rid, 'previous_record_id': str(record_id), 'revision': revision,
            'review_state': 'review_pending'}
