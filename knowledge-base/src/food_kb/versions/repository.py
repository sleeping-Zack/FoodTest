"""Append-only snapshots of registered version claims and their source members."""
from collections import Counter

from ..common import KBError, digest, identity
from ..db.session import insert
from ..knowledge.gaps import gap
from .sources import (PROCESSOR_VERSION, INDEXES, build_reference, has_null,
                      index_evidence, normalize_designation, registered_sources)


def _dependencies(conn, version_ids, references):
    result = []
    for row in conn.execute('SELECT record_id,record_type,version_id FROM knowledge_record WHERE version_id IS NOT NULL').fetchall():
        vid = str(row['version_id'])
        if vid not in version_ids:
            continue
        kind = ('classification' if row['record_type'] in {'category', 'taxonomy_metadata', 'classification_table', 'classification_text', 'definition_export'}
                else 'requirement' if row['record_type'] in {'requirement', 'inspection_item'}
                else 'method' if row['record_type'] == 'method' else 'definition' if row['record_type'] == 'definition'
                else 'scope' if row['record_type'] == 'standard_scope_catalog' else None)
        if kind:
            rid = str(row['record_id'])
            result.append({'dependent': {'kind': kind, 'id': rid}, 'depends_on': {'kind': 'version', 'id': vid},
                           'evidence': [{'source_ref': 'knowledge_record:' + rid, 'evidence_role': 'registered_version_binding'}],
                           'verification_state': 'unverified'})
    # Include concrete namespace/node IDs from all retained classification snapshots.
    for row in conn.execute('SELECT snapshot_id,payload FROM taxonomy_snapshot').fetchall():
        for ns in row['payload']['namespaces']:
            vid = ns['standard_version_id']
            if vid not in version_ids:
                continue
            evidence = [{'source_ref': 'taxonomy_snapshot:' + str(row['snapshot_id']), 'evidence_role': 'frozen_version_binding'}]
            for item in [{'kind': 'namespace', 'id': ns['namespace']},
                         *({'kind': 'classification', 'id': n['id']} for n in ns['nodes']),
                         *({'kind': 'requirement', 'id': r['id']} for r in ns['requirements'])]:
                result.append({'dependent': item, 'depends_on': {'kind': 'version', 'id': vid},
                               'evidence': evidence, 'verification_state': 'unverified'})
    for row in conn.execute('''SELECT b.binding_id,k.version_id FROM rule_binding b
        JOIN binding_knowledge bk USING(binding_id) JOIN knowledge_record k USING(record_id)
        WHERE k.version_id IS NOT NULL''').fetchall():
        vid = str(row['version_id'])
        if vid in version_ids:
            result.append({'dependent': {'kind': 'rule', 'id': str(row['binding_id'])},
                           'depends_on': {'kind': 'version', 'id': vid},
                           'evidence': [{'source_ref': 'binding_knowledge:' + str(row['binding_id']), 'evidence_role': 'registered_binding'}],
                           'verification_state': 'unverified'})
    for ref in references:
        # The direction is an explicit candidate-impact connection, not a normative dependency.
        for vid in ([ref['from_version_id']] if ref['from_version_id'] else []) + ref['target']['version_ids']:
            result.append({'dependent': {'kind': 'reference', 'id': ref['reference_id']},
                           'depends_on': {'kind': 'version', 'id': vid}, 'evidence': [ref['evidence'][0]],
                           'verification_state': 'unverified'})
    unique = {digest(item): item for item in result}
    return [unique[key] for key in sorted(unique)]


def collect_payload(conn, config):
    records, sources, coverage = registered_sources(conn, config)
    source_map = {s['source_key']: s for s in sources}
    groups = {kind: [r for r in records if r['adapter'] == kind] for kind in INDEXES.values()}
    rows = conn.execute('''SELECT c.legacy_id,v.*,d.standard_family FROM document_claim c
        JOIN document_version v USING(version_id) JOIN source_document d USING(document_id)''').fetchall()
    claims = {str(r['legacy_id']): r for r in rows}
    links = {(str(r['version_id']), r['asset_id']): r['role'] for r in conn.execute('SELECT * FROM version_asset').fetchall()}
    catalog_map, versions_by_id, gaps = {}, {}, []
    for row in groups['catalog']:
        raw, lid = row['raw_payload'], str(row['legacy_id'])
        evidence = [index_evidence(row)]
        if has_null(raw):
            gaps.append(gap('unsupported_text', 'catalog_claim', lid, '目录含不可表示字符；原始登记保留，未转为可执行信息。', evidence))
            continue
        v = claims.get(lid)
        if v is None:
            gaps.append(gap('source_conflict', 'catalog_claim', lid, '目录记录没有唯一已登记版本绑定。', evidence))
            continue
        vid = str(v['version_id']); catalog_map[raw['id']] = vid
        item = versions_by_id.get(vid)
        if item is None:
            item = {'version_id': vid, 'document_id': str(v['document_id']), 'designation': v['designation'],
                    'family': v['standard_family'] or raw.get('family'), 'title': v['title'],
                    'is_amendment': bool(raw.get('is_amendment')), 'is_draft': bool(raw.get('is_draft')) or str(v['identity_status']) == 'draft',
                    'identity_state': str(v['identity_status']),
                    'review_state': 'review_pending', 'body_assets': [], 'other_assets': [], 'date_claims': [],
                    'status_claims': [], 'policies': [], 'evidence': [], 'source_legacy_ids': [], 'role_claims': [], 'quality_claims': [],
                    'transition_claim': v['transition_text'], 'executable': False}
            versions_by_id[vid] = item
        item['source_legacy_ids'].append(lid); item['evidence'].extend(evidence)
        item['role_claims'].append({'is_amendment': bool(raw.get('is_amendment')), 'is_draft': bool(raw.get('is_draft')),
                                   'evidence': evidence})
        for role, field in [('publication', 'publication_date'), ('implementation', 'implementation_date')]:
            item['date_claims'].append({'role': role, 'value': raw.get(field), 'raw_value': raw.get(field),
                  'verification_state': 'unverified', 'source_ref': row['index_asset_id'] + '#' + row['json_pointer'] + '/' + field,
                  'evidence': [index_evidence(row, field)]})
        item['status_claims'].append({'value': raw.get('official_status'), 'verification_state': 'unverified',
                                    'evidence': [index_evidence(row, 'official_status')]})
        for ai, a in enumerate(raw.get('attachments', [])):
            source = source_map.get(a.get('path'))
            if not source or (vid, source['asset_id']) not in links:
                continue
            role = links[(vid, source['asset_id'])]
            value = {'asset_id': source['asset_id'], 'source_key': source['source_key'], 'role': role,
                     'verification_state': 'unverified'}
            # An amendment can never supply a principal standard's body, even if an old relation mislabeled its carrier.
            target = item['body_assets'] if role == 'original' and not item['is_amendment'] and item['identity_state'] != 'draft' else item['other_assets']
            if value not in target:
                target.append(value)
            quality_evidence = [index_evidence(row, 'attachments/' + str(ai))]
            item['quality_claims'].append({'asset_id': source['asset_id'], 'source_key': source['source_key'],
                  'extraction_status': a.get('extraction_status'), 'page_count': a.get('page_count'),
                  'empty_text_pages': a.get('empty_text_pages'), 'unreliable_text_pages': a.get('unreliable_text_pages'),
                  'verification_state': 'unverified', 'evidence': quality_evidence})
            if a.get('empty_text_pages'):
                gaps.append(gap('missing_pages', 'version', vid, '旧文字提取为空的页需补核；不是声称PDF物理缺页。', quality_evidence,
                                [{'kind': 'version', 'id': vid}]))
            if a.get('unreliable_text_pages'):
                gaps.append(gap('unreadable_text', 'version', vid, '旧文字提取标记乱码或不可靠页，不能确认完整语义。', quality_evidence))
        roles = {(r['is_amendment'], r['is_draft']) for r in item['role_claims']}
        if len(roles) > 1:
            item['identity_state'] = 'conflicting'
            item['is_amendment'] = any(r[0] for r in roles); item['is_draft'] = any(r[1] for r in roles)
            item['other_assets'].extend(item['body_assets']); item['body_assets'] = []
            gaps.append(gap('source_conflict', 'version', vid, '同一版本的主体/修改单/草案角色声明冲突，主体资格冻结。', item['evidence']))
    for row in (r for r in records if r['adapter'] == 'guide_metadata'):
        matches = conn.execute('''SELECT DISTINCT v.*,d.standard_family FROM legacy_knowledge m
            JOIN knowledge_record k USING(record_id) JOIN document_version v USING(version_id)
            JOIN source_document d USING(document_id) WHERE m.legacy_id=%s''', (row['legacy_id'],)).fetchall()
        if len(matches) != 1:
            gaps.append(gap('source_conflict', 'guide_claim', str(row['legacy_id']), '细则登记缺唯一业务版本绑定。', [index_evidence(row)]))
            continue
        v, raw = matches[0], row['raw_payload']; vid = str(v['version_id'])
        s = source_map.get(raw['source']); evidence = [index_evidence(row)]
        body = [{'asset_id': s['asset_id'], 'source_key': s['source_key'], 'role': 'original', 'verification_state': 'unverified'}] if s and links.get((vid, s['asset_id'])) == 'original' else []
        versions_by_id[vid] = {'version_id': vid, 'document_id': str(v['document_id']), 'designation': v['designation'],
            'family': 'inspection-guide', 'title': v['title'], 'document_kind': 'guide',
            'is_amendment': False, 'is_draft': False, 'identity_state': str(v['identity_status']),
            'review_state': 'review_pending', 'body_assets': body, 'other_assets': [], 'date_claims': [],
            'status_claims': [], 'policies': [], 'evidence': evidence, 'source_legacy_ids': [str(row['legacy_id'])],
            'role_claims': [{'document_kind': 'guide', 'evidence': evidence}], 'quality_claims': [],
            'transition_claim': None, 'executable': False}
    versions = sorted(versions_by_id.values(), key=lambda v: (normalize_designation(v['designation']), v['version_id']))
    designation_map = {}
    for v in versions:
        if not v['is_amendment']:
            designation_map.setdefault(normalize_designation(v['designation']), []).append(v['version_id'])
    references = []
    for row in groups['reference_candidate']:
        lid = str(row['legacy_id'])
        if has_null(row['raw_payload']):
            source_version = catalog_map.get(row['raw_payload'].get('source_id'))
            affected = [{'kind': 'reference', 'id': lid}]
            if source_version:
                affected.append({'kind': 'version', 'id': source_version})
            gaps.append(gap('unsupported_text', 'reference', lid, '引用原始文字含不可表示字符，保留登记并隔离。', [index_evidence(row)], affected))
            continue
        ref = build_reference(row, catalog_map.get(row['raw_payload'].get('source_id')), catalog_map, designation_map, source_map)
        references.append(ref)
        if not ref['from_version_id'] or not ref['target']['version_ids']:
            kind = 'historical_version_missing' if ref['binding']['datedness'] == 'dated' and not ref['target']['version_ids'] else 'reference_unresolved'
            gaps.append(gap(kind, 'reference', ref['reference_id'], '引用来源或目标缺少已登记的唯一版本候选；不绑定最新版。', [ref['evidence'][0]]))
    events = []
    for row in groups['announcement']:
        raw = row['raw_payload']
        if has_null(raw):
            gaps.append(gap('unsupported_text', 'event', str(row['legacy_id']), '关联事件含不可表示字符，保留原登记。', [index_evidence(row)]))
            continue
        kind = {'公告': 'announcement', '解读': 'interpretation', '勘误': 'erratum'}.get(raw.get('kind'), 'unknown')
        evidence = [index_evidence(row)]
        s = source_map.get(raw.get('source_path'))
        if s:
            evidence.append({'source_key': s['source_key'], 'source_asset_id': s['asset_id'],
                             'locator': {'location_status': 'file_only'}, 'locator_quality': 'source_pointer',
                             'evidence_role': 'event_carrier'})
        events.append({'event_id': identity('version-event', str(row['legacy_id'])), 'kind': kind,
                       'title': raw.get('title'), 'date_claim': raw.get('date'),
                       'version_ids': sorted({catalog_map[c] for c in raw.get('related_standard_ids', []) if c in catalog_map}),
                       'evidence': evidence, 'verification_state': 'unverified', 'review_state': 'review_pending'})
    for v in versions:
        vid, evidence = v['version_id'], v['evidence']
        if not v['body_assets'] and not v['is_amendment']:
            gaps.append(gap('missing_body', 'version', vid, '无已登记主体全文；修改单、草案及元数据不能代替主体。', evidence))
        if v['identity_state'] == 'conflicting':
            gaps.append(gap('source_conflict', 'version', vid, '登记的版本身份冲突，不能判为正式主体或现行依据。', evidence))
        else:
            gaps.append(gap('identity_unverified', 'version', vid, '标准身份未核实，目录现行/废止声明不作确定性结论。', evidence))
        gaps.append(gap('version_policy_missing', 'version', vid, '缺有出处且已核实的业务日期角色/过渡适用规则。', evidence))
        gaps.append(gap('review_pending', 'version', vid, '真实版本与引用仍待专家审核。', evidence))
        if v['is_amendment']:
            targets = designation_map.get(normalize_designation(v['designation']), [])
            events.append({'event_id': identity('version-amendment', vid), 'kind': 'amendment', 'title': v['title'],
                           'version_id': vid, 'target_version_ids': targets, 'version_ids': [vid, *targets],
                           'evidence': evidence, 'verification_state': 'unverified', 'review_state': 'review_pending'})
            if not any(versions_by_id[t]['body_assets'] for t in targets):
                gaps.append(gap('missing_body', 'version', vid, '已登记修改单，但其指定主体全文缺失；不能用修改单判定主体要求。', evidence,
                                [{'kind': 'version', 'id': x} for x in [vid, *targets]]))
    for c in coverage:
        if not c['complete']:
            gaps.append(gap('unregistered_index_rows', 'source', c['asset_id'], '当前索引有未登记记录，必须单独导入后形成新快照。',
                            [{'source_key': c['source_key'], 'source_asset_id': c['asset_id'], 'locator': {'missing_pointers': c['missing_pointers']}, 'locator_quality': 'source_pointer'}]))
    dependencies = _dependencies(conn, set(versions_by_id), references)
    affected = {}
    for dep in dependencies:
        affected.setdefault((dep['depends_on']['kind'], dep['depends_on']['id']), []).append(dep['dependent'])
    for g in gaps:
        for t in list(g['affected_refs']):
            g['affected_refs'].extend(affected.get((t['kind'], t['id']), []))
        g['affected_refs'] = list({(t['kind'], t['id']): t for t in g['affected_refs']}.values())
    gaps = sorted({g['gap_id']: g for g in gaps}.values(), key=lambda g: g['gap_id'])
    references.sort(key=lambda r: r['reference_id']); events.sort(key=lambda e: e['event_id'])
    input_hash = digest({'sources': sources, 'registered_coverage': coverage, 'versions': versions,
                         'dependencies': dependencies, 'events': events})
    sid = identity('version-reference-snapshot', PROCESSOR_VERSION, input_hash)
    summary = {'versions': len(versions), 'principal_versions': sum(not v['is_amendment'] and v.get('document_kind') != 'guide' for v in versions),
               'amendment_versions': sum(v['is_amendment'] for v in versions),
               'guide_versions': sum(v.get('document_kind') == 'guide' for v in versions), 'references': len(references),
               'reference_natures': dict(Counter(r['nature']['value'] for r in references)),
               'reference_bindings': dict(Counter(r['binding']['datedness'] for r in references)),
               'events': len(events), 'event_kinds': dict(Counter(e['kind'] for e in events)),
               'gaps': len(gaps), 'gap_kinds': dict(Counter(g['kind'] for g in gaps)), 'sources': len(sources),
               'dependencies': len(dependencies), 'approved_versions': 0, 'verified_policies': 0}
    return {'schema_version': 'version-reference-1', 'processor_version': PROCESSOR_VERSION,
            'snapshot_id': sid, 'input_hash': input_hash, 'sources': sources, 'registered_coverage': coverage,
            'versions': versions, 'references': references, 'events': events, 'gaps': gaps,
            'dependencies': dependencies, 'summary': summary, 'review_state': 'review_pending',
            'scope_complete': False, 'executable': False,
            'limitations': ['日期与版本为登记声明，业务日期角色和过渡条款未核，不默认今天或最新版。',
                            '规范性、范围和定年绑定是独立维度；原文提及不能升级为规范依赖。',
                            '修改单与主体分开；候选影响分析不修改旧知识、审核或发布。']}


def validate_snapshot(payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != 'version-reference-1':
        raise KBError('VERSION_SNAPSHOT_INVALID', 'Unsupported version snapshot contract.')
    if payload.get('review_state') != 'review_pending' or payload.get('scope_complete') is not False or payload.get('executable') is not False:
        raise KBError('VERSION_SNAPSHOT_INVALID', 'Candidate snapshot cannot imply approval, completeness or execution.')
    for field in ('sources', 'versions', 'references', 'events', 'gaps', 'dependencies'):
        if not isinstance(payload.get(field), list):
            raise KBError('VERSION_SNAPSHOT_INVALID', 'Version snapshot lists are required.')
    if not payload['sources'] or len({s['source_key'] for s in payload['sources']}) != len(payload['sources']):
        raise KBError('VERSION_SNAPSHOT_INVALID', 'Source manifest must be nonempty and unique.')
    if has_null(payload):
        raise KBError('VERSION_SNAPSHOT_INVALID', 'Unrepresentable source text must remain quarantined.')


def build_snapshot(conn, config):
    payload = collect_payload(conn, config)
    validate_snapshot(payload)
    sid, content_hash = payload['snapshot_id'], digest(payload)
    with conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(608042009)')
        existing = conn.execute('SELECT payload_hash FROM version_reference_snapshot WHERE snapshot_id=%s', (sid,)).fetchone()
        if existing:
            if existing['payload_hash'] != content_hash:
                raise KBError('VERSION_SNAPSHOT_CONFLICT', 'Same frozen input/processor produced different content; append a processor version.')
            read_snapshot(conn, sid)
            status = 'duplicate'
        else:
            insert(conn, 'version_reference_snapshot', {'snapshot_id': sid, 'processor_version': PROCESSOR_VERSION,
                   'input_hash': payload['input_hash'], 'payload_hash': content_hash, 'payload': payload})
            for s in payload['sources']:
                insert(conn, 'version_reference_snapshot_source', {'snapshot_id': sid, 'source_key': s['source_key'],
                       'asset_id': s['asset_id'], 'legacy_id': s['legacy_id']})
            status = 'success'
    return {'snapshot_id': sid, 'status': status, 'payload_hash': content_hash, 'summary': payload['summary']}


def read_snapshot(conn, snapshot_id):
    row = conn.execute('SELECT payload,payload_hash FROM version_reference_snapshot WHERE snapshot_id=%s', (snapshot_id,)).fetchone()
    if not row:
        raise KBError('VERSION_SNAPSHOT_MISSING', 'Requested version snapshot does not exist.')
    payload = row['payload']; validate_snapshot(payload)
    if payload['snapshot_id'] != str(snapshot_id) or digest(payload) != row['payload_hash']:
        raise KBError('VERSION_SNAPSHOT_CORRUPT', 'Frozen version snapshot fingerprint does not match.')
    actual = sorted([(r['source_key'], r['asset_id'], str(r['legacy_id']) if r['legacy_id'] else None)
                     for r in conn.execute('SELECT source_key,asset_id,legacy_id FROM version_reference_snapshot_source WHERE snapshot_id=%s', (snapshot_id,)).fetchall()])
    expected = sorted((s['source_key'], s['asset_id'], s['legacy_id']) for s in payload['sources'])
    if actual != expected:
        raise KBError('VERSION_SNAPSHOT_CORRUPT', 'Frozen version source members do not match the manifest.')
    return payload
