"""Append-only candidate snapshots over already registered source indexes."""
import ast
import re
from collections import Counter

from ..common import KBError, digest, file_hash, identity, read_json
from ..db.session import insert
from ..storage.assets import preserve
from .sources import PROCESSOR_VERSION, build_guide, build_standard, validate_snapshot

ADAPTERS = ('taxonomy_metadata', 'guide_metadata', 'definition_export_metadata',
            'guide_page_cache_metadata', 'definition_bridge_metadata')


def _engineering_hints(config):
    """Read one literal from the existing source; never execute its module."""
    key = 'food-classifier/engine.py'
    if not (config.source_root / key).is_file():
        return [], None
    path = config.source(key)
    text = path.read_text(encoding='utf-8-sig')
    parsed = ast.parse(text)
    assignments = [item for item in parsed.body if isinstance(item, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'AMBIGUOUS' for target in item.targets)]
    if len(assignments) != 1:
        raise KBError('TAXONOMY_HINT_INVALID', 'Existing ambiguity hints must have one literal assignment.')
    assignment = assignments[0]
    try:
        ambiguous = ast.literal_eval(assignment.value)
    except (ValueError, KeyError, TypeError, SyntaxError) as exc:
        raise KBError('TAXONOMY_HINT_INVALID', 'Ambiguity hints are not a source literal.') from exc
    if not isinstance(ambiguous, dict) or any(not isinstance(k, str) or not k or not isinstance(values, list)
            or not values or any(not isinstance(v, str) or not v for v in values) for k, values in ambiguous.items()):
        raise KBError('TAXONOMY_HINT_INVALID', 'Ambiguity targets must be explicit nonempty strings.')
    info = preserve(config, key)
    source = {'source_key': key, **{k: info[k] for k in ('asset_id', 'sha256', 'size', 'storage_key')},
              'role': 'engineering_hint', 'status': 'hash_verified', 'legacy_id': None}
    quote = ast.get_source_segment(text, assignment)
    hints = [{'value': name, 'targets': targets, 'kind': 'engineering_ambiguity',
              'verification_state': 'unverified', 'required_facts': ['process', 'ingredients'],
              'evidence': [{'source_key': key, 'source_asset_id': info['asset_id'],
                   'quote': quote, 'locator': {'line_start': assignment.lineno, 'line_end': assignment.end_lineno},
                   'locator_quality': 'source_pointer', 'evidence_role': 'engineering_hint'}]}
             for name, targets in sorted(ambiguous.items())]
    return hints, (info, source)


def _registered_sources(conn, config):
    rows = conn.execute("""SELECT legacy_id, source_key, index_asset_id, adapter, raw_payload
        FROM legacy_record WHERE adapter = ANY(%s) AND importer_version='legacy-2'
        ORDER BY source_key,index_asset_id""", (list(ADAPTERS),)).fetchall()
    selected = []
    by_key = {}
    for row in rows:
        by_key.setdefault(row['source_key'], []).append(row)
    for source_key, choices in by_key.items():
        current = config.source(source_key)
        if not current.is_file():
            raise KBError('TAXONOMY_SOURCE_MISSING', 'Registered classification index is missing.')
        sha = file_hash(current)
        matches = [row for row in choices if row['index_asset_id'] == sha]
        if len(matches) != 1:
            raise KBError('TAXONOMY_SOURCE_CHANGED', 'Current index is not a unique registered legacy-2 snapshot; import its revision first.')
        row = matches[0]
        if read_json(current) != row['raw_payload']:
            raise KBError('TAXONOMY_SOURCE_CONFLICT', 'Frozen legacy payload differs from its registered index bytes.')
        selected.append(row)
    kinds = Counter(row['adapter'] for row in selected)
    if not kinds['taxonomy_metadata'] or any(kinds[k] != 1 for k in ADAPTERS[1:]):
        raise KBError('TAXONOMY_SOURCE_MISSING', 'Taxonomies, guide, definitions, page cache and bridges must be registered first.')
    members = {}
    def source(relative, sha, role, legacy_id=None):
        asset = conn.execute('SELECT * FROM source_asset WHERE asset_id=%s', (sha,)).fetchone()
        if not asset:
            raise KBError('TAXONOMY_SOURCE_MISSING', 'Classification source bytes are not registered.')
        current = config.source(relative)
        saved = config.output_root / asset['storage_key']
        if not current.is_file() or not saved.is_file():
            raise KBError('TAXONOMY_SOURCE_MISSING', 'Current or preserved classification source is missing.')
        if file_hash(current) != sha or file_hash(saved) != sha:
            raise KBError('TAXONOMY_SOURCE_CHANGED', 'Classification source or preserved original fingerprint changed.')
        value = {'source_key': relative, 'asset_id': sha, 'sha256': sha, 'size': asset['size'],
                 'storage_key': asset['storage_key'], 'role': role, 'status': 'hash_verified',
                 'legacy_id': str(legacy_id) if legacy_id else None}
        old = members.get(relative)
        if old is not None and old != value:
            raise KBError('TAXONOMY_SOURCE_CONFLICT', 'Same source path has incompatible frozen identities.')
        members[relative] = value
    for row in selected:
        source(row['source_key'], row['index_asset_id'], 'index', row['legacy_id'])
        raw = row['raw_payload']
        if row['adapter'] in ('taxonomy_metadata', 'guide_metadata'):
            source(raw['source'], raw['sha256'], 'original')
    return selected, [members[key] for key in sorted(members)]


def _version(conn, record):
    rows = conn.execute("""SELECT DISTINCT v.version_id,v.identity_status
        FROM legacy_knowledge m JOIN knowledge_record k USING(record_id)
        JOIN document_version v USING(version_id) WHERE m.legacy_id=%s""", (record['legacy_id'],)).fetchall()
    binding = 'legacy_knowledge'
    if not rows and record['adapter'] == 'taxonomy_metadata':
        raw = record['raw_payload']
        rows = conn.execute('''SELECT DISTINCT v.version_id,v.identity_status
            FROM document_version v JOIN version_asset a USING(version_id)
            WHERE a.asset_id=%s AND regexp_replace(v.designation,'\\s','','g')=%s''',
            (raw['sha256'], re.sub(r'\s+', '', raw['standard']))).fetchall()
        binding = 'original_asset_claim'
        if not rows:
            rows = conn.execute("SELECT version_id,identity_status FROM document_version WHERE regexp_replace(designation,'\\s','','g')=%s",
                                (re.sub(r'\s+', '', raw['standard']),)).fetchall()
            binding = 'designation_claim'
    if len(rows) != 1:
        raise KBError('TAXONOMY_VERSION_CONFLICT', 'Classification index lacks a unique registered document version.')
    record['version_binding'] = {'basis': binding, 'state': 'unverified',
                                'note': 'Unique registered identity claim is not expert validation or current applicability.'}
    return str(rows[0]['version_id']), rows[0]['identity_status']


def build_snapshot(conn, config):
    records, sources = _registered_sources(conn, config)
    hints, engineering_source = _engineering_hints(config)
    if engineering_source:
        sources.append(engineering_source[1]); sources.sort(key=lambda s: s['source_key'])
    groups = {kind: [r for r in records if r['adapter'] == kind] for kind in ADAPTERS}
    namespaces = []
    for record in groups['taxonomy_metadata']:
        version, state = _version(conn, record)
        item = build_standard(record, version, state)
        item['version_binding'] = record['version_binding']; namespaces.append(item)
    guide = groups['guide_metadata'][0]
    version, state = _version(conn, guide)
    item = build_guide(guide, groups['definition_export_metadata'][0],
                      groups['guide_page_cache_metadata'][0], groups['definition_bridge_metadata'][0], version, state)
    item['version_binding'] = guide['version_binding']; namespaces.append(item)
    namespaces.sort(key=lambda ns: ns['namespace'])
    input_hash = digest({'sources': sources, 'version_bindings': [(ns['namespace'], ns['standard_version_id']) for ns in namespaces]})
    snapshot_id = identity('taxonomy-snapshot', PROCESSOR_VERSION, input_hash)
    summary = {'namespaces': len(namespaces), 'standard_namespaces': len(namespaces) - 1,
               'source_files': len(sources), 'nodes': sum(len(n['nodes']) for n in namespaces),
               'relations': sum(len(n['relations']) for n in namespaces),
               'inspection_requirements': sum(len(n['requirements']) for n in namespaces),
               'gaps': sum(len(n['gaps']) for n in namespaces), 'approved_records': 0}
    payload = {'schema_version': 'taxonomy-1', 'processor_version': PROCESSOR_VERSION,
               'snapshot_id': snapshot_id, 'input_hash': input_hash, 'sources': sources,
               'namespaces': namespaces, 'normalization_hints': hints, 'summary': summary, 'scope_complete': False,
               'review_state': 'review_pending', 'executable': False,
               'limitations': ['分类快照为有来源候选，原文定位仍是页码声明，并非专家核实。',
                   '各标准版本独立；版本时效、日期角色与样品条件仍须核对。',
                   '分类表和定义仅部分结构化；未命中、未关联不能解释为没有要求。',
                   '抽检表行是资料候选，关联上级表不等于本次任务全部必检。']}
    validate_snapshot(payload)
    content_hash = digest(payload)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042008)')
        existing = conn.execute('SELECT payload_hash FROM taxonomy_snapshot WHERE snapshot_id=%s', (snapshot_id,)).fetchone()
        if existing:
            if existing['payload_hash'] != content_hash:
                raise KBError('TAXONOMY_SNAPSHOT_CONFLICT', 'Same frozen source/processor identity produced different content; append a new processor version.')
            read_snapshot(conn, snapshot_id)
            return {'snapshot_id': snapshot_id, 'status': 'duplicate', 'summary': summary, 'payload_hash': content_hash}
        if engineering_source:
            info, source = engineering_source
            insert(conn, 'source_asset', info)
            insert(conn, 'source_registration', {'registration_id': identity('registration', source['source_key'], info['asset_id'], None, 'AMBIGUOUS'),
                'asset_id': info['asset_id'], 'source_key': source['source_key'], 'json_pointer': 'AMBIGUOUS',
                'identity_status': 'unverified', 'quality': 'not_extracted',
                'metadata': {'role': 'engineering_hint', 'locator': hints[0]['evidence'][0]['locator'],
                             'note': 'Only expands retrieval candidates; never verifies a standard alias or sample fact.'}})
        insert(conn, 'taxonomy_snapshot', {'snapshot_id': snapshot_id, 'processor_version': PROCESSOR_VERSION,
               'input_hash': input_hash, 'payload_hash': content_hash, 'payload': payload})
        for source in sources:
            insert(conn, 'taxonomy_snapshot_source', {'snapshot_id': snapshot_id, 'source_key': source['source_key'],
                   'asset_id': source['asset_id'], 'legacy_id': source['legacy_id']})
    return {'snapshot_id': snapshot_id, 'status': 'success', 'summary': summary, 'payload_hash': content_hash}


def read_snapshot(conn, snapshot_id):
    row = conn.execute('SELECT payload,payload_hash FROM taxonomy_snapshot WHERE snapshot_id=%s', (snapshot_id,)).fetchone()
    if not row:
        raise KBError('TAXONOMY_SNAPSHOT_MISSING', 'Candidate taxonomy snapshot was not found.')
    payload = row['payload']
    if digest(payload) != row['payload_hash'] or payload.get('snapshot_id') != str(snapshot_id):
        raise KBError('TAXONOMY_SNAPSHOT_CORRUPT', 'Frozen taxonomy snapshot content fingerprint differs.')
    actual = conn.execute('SELECT source_key,asset_id,legacy_id FROM taxonomy_snapshot_source WHERE snapshot_id=%s ORDER BY source_key', (snapshot_id,)).fetchall()
    expected = [{k: source[k] for k in ('source_key', 'asset_id', 'legacy_id')} for source in payload['sources']]
    actual = [{k: str(v) if k == 'legacy_id' and v is not None else v for k, v in item.items()} for item in actual]
    # PostgreSQL's locale collation and Python's Unicode order can differ for
    # Chinese directories. Membership is identity-based, never order-based.
    if sorted(actual, key=lambda s: s['source_key']) != sorted(expected, key=lambda s: s['source_key']):
        raise KBError('TAXONOMY_SNAPSHOT_CORRUPT', 'Frozen taxonomy source membership differs.')
    return validate_snapshot(payload)
