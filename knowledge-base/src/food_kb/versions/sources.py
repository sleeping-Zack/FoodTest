"""Read current registered indexes without upgrading their semantic claims."""
import re
import unicodedata
from collections import Counter

from ..common import KBError, file_hash, identity, read_json

PROCESSOR_VERSION = 'version-reference-sources-2'
INDEXES = {
    'references/食品标准资料库/索引/标准总目录.json': 'catalog',
    'references/食品标准资料库/索引/标准逐层引用关系.json': 'reference_candidate',
    'references/食品标准资料库/索引/公告解读勘误关联.json': 'announcement',
}


def normalize_designation(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).replace('—', '-').replace('–', '-')


def has_null(value):
    if isinstance(value, str):
        return '\x00' in value
    if isinstance(value, dict):
        return any(has_null(k) or has_null(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_null(v) for v in value)
    return False


def index_evidence(row, field=None):
    pointer = row['json_pointer'] + ('/' + field if field else '')
    return {'source_key': row['source_key'], 'source_asset_id': row['index_asset_id'],
            'locator': {'json_pointer': pointer}, 'locator_quality': 'source_pointer',
            'evidence_role': 'index_claim'}


def registered_sources(conn, config):
    records, members, coverage = [], {}, []
    assets = {r['asset_id']: r for r in conn.execute('SELECT * FROM source_asset').fetchall()}
    registrations = {}
    for row in conn.execute('SELECT source_key,asset_id FROM source_registration').fetchall():
        registrations.setdefault(row['source_key'], set()).add(row['asset_id'])

    def member(key, sha, role, legacy_id=None):
        asset = assets.get(sha)
        if not asset:
            return None
        current, saved = config.source(key), config.output_root / asset['storage_key']
        if not current.is_file() or not saved.is_file():
            raise KBError('VERSION_SOURCE_MISSING', 'Current or preserved registered version source is missing.')
        old = members.get(key)
        if old:
            if old['asset_id'] != sha:
                raise KBError('VERSION_SOURCE_CONFLICT', 'A source path has incompatible registered identities.')
            return old
        if file_hash(current) != sha or file_hash(saved) != sha:
            raise KBError('VERSION_SOURCE_CHANGED', 'Current or preserved registered version source fingerprint changed.')
        item = {'source_key': key, 'asset_id': sha, 'sha256': sha, 'size': asset['size'],
                'storage_key': asset['storage_key'], 'role': role, 'status': 'hash_verified',
                'legacy_id': str(legacy_id) if legacy_id else None}
        members[key] = item
        return item

    for key, adapter in INDEXES.items():
        current = config.source(key)
        if not current.is_file():
            raise KBError('VERSION_SOURCE_MISSING', 'Required version index is missing.')
        sha = file_hash(current)
        data = read_json(current)
        if not isinstance(data, list):
            raise KBError('VERSION_SOURCE_INVALID', 'Version indexes must contain a list.')
        rows = conn.execute("""SELECT legacy_id,index_asset_id,source_key,json_pointer,legacy_key,adapter,raw_payload
            FROM legacy_record WHERE source_key=%s AND index_asset_id=%s AND adapter=%s
            AND importer_version='legacy-2' ORDER BY json_pointer""", (key, sha, adapter)).fetchall()
        if not rows:
            raise KBError('VERSION_SOURCE_UNREGISTERED', 'Current version index is not registered; import its revision first.')
        pointers = set()
        for row in rows:
            pointer = row['json_pointer']
            try:
                position = int(pointer.removeprefix('/'))
                raw = data[position]
                if position < 0 or pointer != '/' + str(position) or raw != row['raw_payload']:
                    raise ValueError()
            except (IndexError, TypeError, ValueError):
                raise KBError('VERSION_SOURCE_CONFLICT', 'Registered index row does not match its frozen source pointer.')
            pointers.add(pointer)
            records.append(row)
        if member(key, sha, 'index', rows[0]['legacy_id']) is None:
            raise KBError('VERSION_SOURCE_UNREGISTERED', 'Version index bytes are not registered.')
        coverage.append({'source_key': key, 'asset_id': sha, 'adapter': adapter,
                         'index_records': len(data), 'registered_records': len(rows),
                         'missing_pointers': ['/' + str(i) for i in range(len(data)) if '/' + str(i) not in pointers],
                         'complete': len(pointers) == len(data)})
    for row in records:
        raw = row['raw_payload']
        if row['adapter'] == 'catalog':
            refs = [(a.get('path'), a.get('sha256'), 'amendment' if raw.get('is_amendment') else 'draft' if raw.get('is_draft') else 'original')
                    for a in raw.get('attachments', [])]
        elif row['adapter'] == 'reference_candidate':
            refs = [(raw.get('source_path'), raw.get('source_sha256'), 'original')]
        else:
            refs = [(raw.get('source_path'), None, 'announcement')]
        for key, expected, role in refs:
            if not key:
                continue
            candidates = registrations.get(key, set())
            sha = expected if expected in candidates else None
            if expected is None and len(candidates) == 1:
                sha = next(iter(candidates))
            if sha:
                member(key, sha, role)
    guide_key = 'references/食品标准资料库/索引/食品项目标准方法关联.json'
    if config.source(guide_key).is_file():
        sha = file_hash(config.source(guide_key))
        rows = conn.execute("""SELECT legacy_id,index_asset_id,source_key,json_pointer,legacy_key,adapter,raw_payload
            FROM legacy_record WHERE source_key=%s AND index_asset_id=%s AND adapter='guide_metadata'
            AND importer_version='legacy-2'""", (guide_key, sha)).fetchall()
        if rows:
            if len(rows) != 1 or read_json(config.source(guide_key)) != rows[0]['raw_payload']:
                raise KBError('VERSION_SOURCE_CONFLICT', 'Registered guide metadata conflicts with its source bytes.')
            row = rows[0]; records.append(row)
            member(guide_key, sha, 'index', row['legacy_id'])
            raw = row['raw_payload']; member(raw['source'], raw['sha256'], 'guide_original')
            coverage.append({'source_key': guide_key, 'asset_id': sha, 'adapter': 'guide_metadata',
                             'index_records': 1, 'registered_records': 1, 'missing_pointers': [], 'complete': True})
    return records, [members[key] for key in sorted(members)], coverage


def build_reference(row, source_version, catalog_map, designation_map, sources):
    raw = row['raw_payload']
    evidence = [index_evidence(row)]
    source = sources.get(raw.get('source_path'))
    if source:
        evidence.append({'source_key': source['source_key'], 'source_asset_id': source['asset_id'],
                         'locator': {'pdf_page': raw['pdf_page']} if isinstance(raw.get('pdf_page'), int) and raw['pdf_page'] > 0 else {'location_status': 'file_only'},
                         'quote': raw.get('excerpt'), 'locator_quality': 'page_claim', 'evidence_role': 'original_claim'})
    target = raw.get('target_code') or ''
    normalized = normalize_designation(target)
    dated = bool(re.search(r'-\d{4}$', normalized))
    candidates = sorted(set(designation_map.get(normalized, [])) if dated else
                        {v for k, vals in designation_map.items() if re.sub(r'-\d{4}$', '', k) == normalized for v in vals})
    stated_ids = raw.get('resolution', {}).get('candidate_ids', [])
    declared = sorted({catalog_map[x] for x in stated_ids if x in catalog_map})
    nature = 'replaces' if raw.get('relation') == '替代关系文字候选' else 'mention'
    return {'reference_id': identity('version-reference', str(row['legacy_id'])),
            'source_legacy_id': str(row['legacy_id']), 'from_version_id': source_version,
            'target': {'designation': target, 'family': raw.get('target_family'), 'version_ids': candidates,
                       'legacy_candidate_version_ids': declared},
            'nature': {'value': nature, 'verification_state': 'unverified', 'evidence': [index_evidence(row, 'relation')]},
            'scope': {'value': 'unknown', 'refs': [], 'verification_state': 'unverified', 'evidence': [index_evidence(row)]},
            'binding': {'datedness': 'dated' if dated else 'undated' if target else 'unknown',
                        'designation': target, 'version_ids': candidates, 'verification_state': 'unverified', 'evidence': [index_evidence(row, 'target_code')]},
            'condition': {'state': 'unresolved', 'evidence': [index_evidence(row, 'excerpt')]},
            'evidence': evidence, 'review_state': 'review_pending', 'executable': False}
