"""A bounded retrieval corpus from explicitly selected, immutable source runs.

Only the accepted manual Markdown supplies standard body text. Catalog titles,
classification definitions and inspection rows retain their separate roles.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import re

from ..common import KBError, digest, file_hash, identity
from ..chunking.service import read_run
from ..knowledge.service import read_extraction, _anchors
from ..taxonomy.repository import read_snapshot as read_taxonomy
from ..versions.repository import read_snapshot as read_versions


PROCESSOR_VERSION = 'retrieval-sources-1'
SOURCE_KEYS = {'chunk_run_id', 'knowledge_run_id', 'taxonomy_snapshot_id', 'version_snapshot_id'}


def _strings(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    # IDs, references and executable conditions are not paraphrased into text.
    return []


def _standards(value):
    if not isinstance(value, str):
        return []
    return list(dict.fromkeys(re.findall(r'(?:GB|NY|SN|SC|QB|LS|SB|WS|HG|DB\d*|ISO|CAC|JJF|JJG)\s*(?:/\s*[TZ]\s*)?\d+(?:\.\d+)*(?:\s*[-—–]\s*\d{4})?', value)))


def _exact(version, **values):
    designation = version.get('designation') or ''
    year = re.search(r'[-—–]\s*(\d{4})$', designation)
    return {k: v for k, v in {'designation': designation, 'family': version.get('family'),
        'year': year[1] if year else None, 'version_id': version.get('version_id'),
        'document_id': version.get('document_id'), **values}.items() if v is not None and v != '' and v != []}


def _record(kind, logical_id, fields, exact, sources, conditions=(), *, source_channel, gap_ids=(), review_state='review_pending', exclusions=()):
    if review_state not in {'extracted', 'machine_checked', 'review_pending', 'approved', 'rejected'}:
        raise KBError('RETRIEVAL_SOURCE_CONFLICT', 'Frozen source review state is absent or unsupported.')
    return {'record_id': identity('retrieval-record', source_channel, kind, logical_id),
        'logical_id': logical_id, 'record_type': kind, 'fields': fields,
        'exact': exact | {'record_type': kind, 'review_state': review_state},
        'sources': deepcopy(sources), 'conditions': deepcopy(list(conditions)),
        'exclusions': deepcopy(list(exclusions)),
        'gap_ids': sorted(set(gap_ids)), 'source_channel': source_channel,
        'review_state': review_state, 'applicability': 'unknown', 'executable': False}


def _manual_evidence(anchor):
    source = anchor['source']
    return {'asset_id': source['asset_id'], 'markdown_asset_id': source.get('markdown_asset_id'),
        'block_id': anchor.get('block_id'), 'locator': deepcopy(source), 'quote': anchor['quote'],
        'locator_quality': source.get('locator_quality', 'page_claim'), 'evidence_role': 'accepted_manual_body'}


def corpus_from_snapshots(bundle, knowledge, taxonomy, versions, source_snapshots):
    """Pure composition; all persisted inputs must already have passed their readers."""
    if knowledge['chunk_run_id'] != bundle['run_id']:
        raise KBError('RETRIEVAL_SOURCE_CONFLICT', 'Knowledge and manual chunks belong to different source runs.')
    version_map = {v['version_id']: v for v in versions['versions']}
    document = knowledge['document_metadata']
    selected = version_map.get(document['version_id'])
    if selected is None or selected['designation'] != document['designation']:
        raise KBError('RETRIEVAL_SOURCE_CONFLICT', 'Manual knowledge lacks the selected registered version identity.')
    sources = {}
    for s in [*taxonomy['sources'], *versions['sources']]:
        item = {k: s.get(k) for k in ('source_key', 'asset_id', 'sha256', 'size', 'storage_key')}
        if s['source_key'] in sources and sources[s['source_key']] != item:
            raise KBError('RETRIEVAL_SOURCE_CONFLICT', 'Selected snapshots disagree on the same source bytes.')
        sources[s['source_key']] = item
    for name, s in bundle['source']['preserved_files'].items():
        key = 'manual/' + bundle['run_id'] + '/' + name
        sources[key] = {'source_key': key, **{k: s[k] for k in ('asset_id', 'sha256', 'size', 'storage_key')}}
    gap_lookup, affected = {}, defaultdict(set)
    for g in versions['gaps']:
        gap_lookup[g['gap_id']] = {k: deepcopy(g.get(k)) for k in ('gap_id', 'kind', 'reason', 'target', 'evidence')}
        for t in [g['target'], *g.get('affected_refs', [])]:
            affected[t['id']].add(g['gap_id'])
    records = []
    manual_gaps = affected[selected['version_id']]
    for c in bundle['chunks']:
        if not c['retrieval_enabled']:
            continue
        fields = {'title': c['title'], 'body': c['raw_markdown']}
        if c['kind'] == 'table_row':
            fields['table'] = c['raw_markdown']
        if re.match(r'^1(?:\b|\.)', c['anchor']):
            fields['scope'] = c['raw_markdown']
        ev = [_manual_evidence({'quote': c['raw_markdown'], 'source': c['source']})]
        records.append(_record('chunk', c['chunk_id'], fields,
            _exact(selected, clause=c['anchor'], chunk_id=c['chunk_id']), ev,
            [{'state': 'unresolved', 'raw': '版本时效和完整范围仍待核。', 'required_facts': []}],
            source_channel='accepted_manual_body', gap_ids=manual_gaps))
    for r in knowledge['records']:
        c = r['candidate']; values = {k: v.get('value') for k, v in c['fields'].items()}
        fields = {}
        for src, dest in [('subject', 'name'), ('project', 'project'), ('definition', 'definition'),
                          ('scope_text', 'scope'), ('method_name', 'title'), ('value', 'table')]:
            texts = _strings(c['fields'][src].get('raw') if src == 'value' and src in c['fields'] else values.get(src))
            if texts:
                fields[dest] = texts if len(texts) > 1 else texts[0]
        anchors = {digest(a): a for a in _anchors(c)}
        # Direct quotes are source text, not generated summaries of methods.
        fields['body'] = list(dict.fromkeys(a['quote'] for a in anchors.values()))
        clause = c['logical_key'].split(':', 2)[-1]
        refs = values.get('method_reference')
        if isinstance(refs, list):
            refs = ' '.join(b.get('title', '') for b in refs if isinstance(b, dict))
        methods = _standards(refs) if isinstance(refs, str) else []
        records.append(_record(c['record_type'], r['record_id'], fields,
            _exact(selected, project_id=r['record_id'] if c['record_type'] == 'requirement' else None,
                project=values.get('project'), clause=clause,
                requirement_type=values.get('requirement_type'), method_standard=methods,
                judgment_standard=_standards(values.get('basis'))),
            [_manual_evidence(a) for a in anchors.values()], [c['condition']],
            source_channel='knowledge_fact', gap_ids=manual_gaps | affected[r['record_id']], review_state=r['review_state']))
    for ns in taxonomy['namespaces']:
        v = version_map.get(ns['standard_version_id'])
        if v is None:
            raise KBError('RETRIEVAL_SOURCE_CONFLICT', 'Classification version is absent from the fixed version snapshot.')
        ns_gap_ids = set()
        for g in ns['gaps']:
            gid = identity('retrieval-source-gap', ns['namespace'], digest(g))
            gap_lookup[gid] = {'gap_id': gid, **deepcopy(g), 'reason': '分类源快照记录的未闭合来源/关联缺口。'}
            ns_gap_ids.add(gid)
        # One namespace coverage pointer avoids repeating hundreds of unrelated
        # variety gaps on every table row. Individual gaps remain discoverable.
        if ns_gap_ids:
            gid = identity('retrieval-source-gap', ns['namespace'], 'coverage')
            gap_lookup[gid] = {'gap_id': gid, 'kind': 'namespace_coverage_partial',
                'target': {'kind': 'namespace', 'id': ns['namespace']},
                'reason': '该分类命名空间的来源或关联覆盖未闭合。', 'gap_ids': sorted(ns_gap_ids)}
            ns_gap_ids = {gid}
        for n in ns['nodes']:
            fields = {'name': n['name'], 'definition': n['definition']['raw']}
            # Normalization hints and examples stay in the source snapshot. They
            # must not turn into exact-name matches or an unreviewed dictionary.
            records.append(_record('classification', n['id'], fields,
                _exact(v, node_id=n['id'], namespace=ns['namespace'], category_code=n.get('code')),
                n['evidence'], [n['condition']], source_channel='classification_candidate', exclusions=n['exclusions'],
                gap_ids=affected[v['version_id']] | affected[n['id']] | ns_gap_ids, review_state=n['review_state']))
        for r in ns['requirements']:
            sem = r['semantic']
            records.append(_record('inspection_requirement', r['id'],
                {'project': sem['project'], 'table': [e['quote'] for e in r['evidence']]},
                _exact(v, node_id=r['node_id'], project_id=r['id'], project=sem['project'],
                    namespace=ns['namespace'], clause=r['table_id'],
                    method_standard=_standards(sem['method_raw']), judgment_standard=_standards(sem['basis_raw'])),
                r['evidence'], [r['condition']], source_channel='inspection_row_candidate', exclusions=r['exclusions'],
                gap_ids=affected[v['version_id']] | affected[r['id']] | ns_gap_ids, review_state=r['review_state']))
    for v in versions['versions']:
        records.append(_record('standard_metadata', v['version_id'], {'title': v['title'], 'designation': v['designation']},
            _exact(v), v['evidence'], [{'state': 'unresolved', 'raw': '日期声明不是业务适用政策。', 'required_facts': []}],
            source_channel='catalog_metadata', gap_ids=affected[v['version_id']], review_state=v['review_state']))
        if v['version_id'] != selected['version_id']:
            gid = identity('retrieval-gap', 'accepted_body_not_indexed', v['version_id'])
            gap_lookup[gid] = {'gap_id': gid, 'kind': 'accepted_body_not_indexed',
                'target': {'kind': 'version', 'id': v['version_id']},
                'reason': '本代次未收录该标准经接受解析的主体全文；登记PDF或分类字段不等于全文可检索。', 'evidence': v['evidence'][:1]}
            records[-1]['gap_ids'].append(gid)
    records.sort(key=lambda r: r['record_id'])
    manifest = [sources[k] for k in sorted(sources)]
    from .exact import ALGORITHM_VERSION
    from .lexical import TOKENIZER_VERSION
    from .normalize import NORMALIZER_VERSION
    retrieval_config = {'algorithm_version': ALGORITHM_VERSION, 'normalizer_version': NORMALIZER_VERSION,
                        'tokenizer_version': TOKENIZER_VERSION, 'alias_expansion': 'disabled'}
    input_hash = digest({'source_snapshots': source_snapshots, 'sources': manifest, 'retrieval_config': retrieval_config,
        'input_payload_hashes': {'chunk': digest(bundle), 'knowledge': digest(knowledge),
                                'taxonomy': digest(taxonomy), 'versions': digest(versions)}})
    gid = identity('retrieval-generation', PROCESSOR_VERSION, input_hash)
    return {'schema_version': 'retrieval-corpus-1', 'processor_version': PROCESSOR_VERSION,
        'algorithm_version': 'retrieval-engine-1', 'generation_id': gid, 'input_hash': input_hash,
        'retrieval_config': retrieval_config,
        'source_snapshots': dict(source_snapshots), 'sources': manifest, 'records': records,
        'dictionary': [], 'gap_lookup': gap_lookup, 'review_state': 'review_pending',
        'scope_complete': False, 'executable': False,
        'summary': {'records': len(records), 'record_types': dict(Counter(r['record_type'] for r in records)),
            'source_files': len(manifest), 'gaps': len(gap_lookup), 'accepted_body_versions': 1,
            'accepted_body_chunks': sum(r['record_type'] == 'chunk' for r in records),
            'context_chunks_retained': len(bundle['chunks']) - sum(c['retrieval_enabled'] for c in bundle['chunks']),
            'standard_bodies_not_indexed': len(versions['versions']) - 1,
            'alias_dictionary_state': 'empty_no_approved_aliases',
            'review_state_basis': 'frozen_source_report_no_live_review_override',
            'approved_records': sum(r['review_state'] == 'approved' for r in records)},
        'limitations': ['全文仅覆盖用户接受的单份手工MD；不采用已否定的KB05自动OCR正文。',
            '分类、检验表和目录字段为来源固定候选，不能代替其他标准全文。',
            '未审别名不自动扩张；命中、得分和标准身份不证明业务适用或完整应检。']}


def collect_payload(conn, config, source_snapshots):
    if not isinstance(source_snapshots, dict) or set(source_snapshots) != SOURCE_KEYS or any(
            not isinstance(v, str) or not v.strip() for v in source_snapshots.values()):
        raise KBError('RETRIEVAL_SOURCE_SELECTION', 'Select all four fixed source run/snapshot IDs explicitly.')
    bundle = read_run(conn, config, source_snapshots['chunk_run_id'])
    knowledge = read_extraction(conn, config, source_snapshots['knowledge_run_id'])
    taxonomy = read_taxonomy(conn, source_snapshots['taxonomy_snapshot_id'])
    versions = read_versions(conn, source_snapshots['version_snapshot_id'])
    result = corpus_from_snapshots(bundle, knowledge, taxonomy, versions, source_snapshots)
    for s in result['sources']:
        saved = (config.output_root / s['storage_key']).resolve()
        if not saved.is_relative_to(config.output_root.resolve()) or not saved.is_file() or file_hash(saved) != s['asset_id']:
            raise KBError('RETRIEVAL_SOURCE_CHANGED', 'A fixed retrieval source is missing or its preserved bytes changed.')
    return result
