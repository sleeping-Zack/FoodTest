"""Bounded tracing keeps mentions, candidate exploration and dependencies separate."""
from collections import defaultdict
import re

from ..common import KBError
from ..taxonomy.conditions import evaluate_condition
from .sources import normalize_designation


def _evidenced(value):
    return isinstance(value, list) and bool(value) and all(isinstance(e, dict) and e for e in value)


def _condition(ref, facts):
    condition = ref.get('condition')
    if not isinstance(condition, dict) or 'state' not in condition or not _evidenced(condition.get('evidence')):
        return {'state': 'unknown', 'reason_codes': ['REFERENCE_CONDITION_MISSING'], 'missing_facts': []}
    result = evaluate_condition(condition, facts or {})
    return {'state': result['status'], **result}


def expand_references(snapshot, start_version_ids, *, mode='dependencies', max_depth=8,
                      max_nodes=100, max_edges=200, facts=None):
    if mode not in {'dependencies', 'candidates'}:
        raise KBError('REFERENCE_MODE_INVALID', 'Use dependencies or explicitly labeled candidates mode.')
    if not isinstance(start_version_ids, list) or not start_version_ids or any(not isinstance(v, str) or not v for v in start_version_ids):
        raise KBError('REFERENCE_START_INVALID', 'Explicit nonempty version IDs are required.')
    limits = ((max_depth, 0, 64), (max_nodes, 1, 10000), (max_edges, 1, 50000))
    if any(isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high for v, low, high in limits):
        raise KBError('REFERENCE_BUDGET_INVALID', 'Reference traversal needs bounded integer budgets.')
    versions = {v['version_id']: v for v in snapshot.get('versions', [])}
    outgoing = defaultdict(list)
    for ref in snapshot.get('references', []):
        if ref.get('from_version_id'):
            outgoing[ref['from_version_id']].append(ref)
    for values in outgoing.values():
        values.sort(key=lambda r: r['reference_id'])
    visited, nodes, trace, cycles, unexpanded, gaps, selected = set(), [], [], [], [], [], []
    stack = [(vid, [vid], 0) for vid in reversed(list(dict.fromkeys(start_version_ids)))]
    edges = 0
    def omitted(vid, reason, path, reference_id=None):
        value = {'version_id': vid, 'reason': reason, 'path': path}
        if reference_id:
            value['reference_id'] = reference_id
        unexpanded.append(value)
    while stack:
        vid, path, depth = stack.pop()
        if vid in visited:
            continue
        if vid not in versions:
            omitted(vid, 'VERSION_MISSING', path); continue
        if len(visited) >= max_nodes:
            omitted(vid, 'NODE_BUDGET_EXHAUSTED', path); continue
        visited.add(vid); nodes.append(vid)
        for ref in outgoing.get(vid, []):
            rid = ref['reference_id']
            if edges >= max_edges:
                omitted(vid, 'EDGE_BUDGET_EXHAUSTED', path, rid); continue
            edges += 1
            nature, scope, binding = ref.get('nature', {}), ref.get('scope', {}), ref.get('binding', {})
            condition = _condition(ref, facts)
            entry = {'reference_id': rid, 'from_version_id': vid, 'path': path,
                     'nature': nature, 'scope': scope, 'binding': binding,
                     'condition_result': condition, 'evidence': ref.get('evidence', []),
                     'candidate_only': mode == 'candidates'}
            trace.append(entry)
            if nature.get('value') != 'normative' and mode == 'dependencies':
                entry['state'] = 'not_dependency' if nature.get('value') in {'mention', 'informative', 'replaces', 'amends'} else 'unknown'
                entry['reason'] = 'MENTION_OR_EVENT_NOT_NORMATIVE' if entry['state'] == 'not_dependency' else 'REFERENCE_NATURE_UNKNOWN'
                if nature.get('verification_state') != 'verified':
                    gaps.append({'reference_id': rid, 'kind': 'reference_unresolved', 'reason': 'REFERENCE_NATURE_UNVERIFIED', 'evidence': ref.get('evidence', [])})
                continue
            reasons = []
            for name, dimension in [('NATURE', nature), ('SCOPE', scope), ('BINDING', binding)]:
                if dimension.get('verification_state') != 'verified' or not _evidenced(dimension.get('evidence')):
                    reasons.append('REFERENCE_' + name + '_UNVERIFIED')
            if scope.get('value') not in {'whole', 'clause', 'item'}:
                reasons.append('REFERENCE_SCOPE_UNKNOWN')
            elif scope['value'] in {'clause', 'item'} and (not isinstance(scope.get('refs'), list) or
                    not scope['refs'] or any(not isinstance(v, str) or not v for v in scope['refs'])):
                reasons.append('REFERENCE_SCOPE_TARGET_MISSING')
            if condition['state'] in {'unknown', 'conflicting'}:
                reasons.append('REFERENCE_CONDITION_' + condition['state'].upper())
            if ref.get('review_state') != 'approved':
                reasons.append('REFERENCE_REVIEW_PENDING')
            if not _evidenced(ref.get('evidence')):
                reasons.append('REFERENCE_EVIDENCE_MISSING')
            targets = sorted(set(binding.get('version_ids', ref.get('target', {}).get('version_ids', []))))
            if not targets:
                reasons.append('REFERENCE_TARGET_MISSING')
            if any(t not in versions for t in targets):
                reasons.append('REFERENCE_TARGET_VERSION_MISSING')
            datedness = binding.get('datedness')
            if datedness == 'dated':
                requested = normalize_designation(binding.get('designation'))
                if not re.search(r'-\d{4}$', requested):
                    reasons.append('DATED_REFERENCE_DESIGNATION_MISSING')
                elif any(normalize_designation(versions[t].get('designation')) != requested for t in targets if t in versions):
                    reasons.append('DATED_REFERENCE_VERSION_MISMATCH')
            elif datedness == 'undated':
                requested = normalize_designation(binding.get('designation'))
                if not requested or re.search(r'-\d{4}$', requested) or any(
                        normalize_designation(versions[t].get('family') or re.sub(r'-\d{4}$', '', versions[t].get('designation', ''))) != requested
                        for t in targets if t in versions):
                    reasons.append('UNDATED_REFERENCE_FAMILY_MISMATCH')
                if len(targets) == 1 and targets[0] in versions and isinstance(binding.get('time_context'), dict):
                    from .resolve import resolve_versions
                    target_version = versions[targets[0]]
                    resolved = resolve_versions(snapshot, binding['time_context'],
                                                document_id=target_version.get('document_id'), facts=facts)
                    if resolved.get('selected_version_id') != targets[0]:
                        reasons.append('UNDATED_REFERENCE_CONTEXT_UNRESOLVED')
                else:
                    reasons.append('UNDATED_REFERENCE_CONTEXT_MISSING')
            else:
                reasons.append('REFERENCE_DATEDNESS_UNKNOWN')
            if mode == 'dependencies' and len(targets) != 1:
                reasons.append('REFERENCE_TARGET_BINDING_UNRESOLVED')
            if condition['state'] == 'not_applicable':
                entry['state'] = 'not_applicable'; entry['reason'] = 'REFERENCE_CONDITION_FALSE'; continue
            entry['state'] = 'conflicting' if condition['state'] == 'conflicting' else 'unknown' if reasons else 'applicable'
            entry['reason_codes'] = reasons
            if reasons:
                gaps.append({'reference_id': rid, 'kind': 'reference_unresolved', 'reason_codes': reasons, 'evidence': ref.get('evidence', [])})
                if mode == 'dependencies':
                    for target in targets:
                        omitted(target, 'REFERENCE_UNRESOLVED', [*path, target], rid)
                    continue
            selected.append(rid)
            for target in reversed(targets):
                target_path = [*path, target]
                if target in path:
                    cycles.append({'reference_id': rid, 'path': target_path, 'reason': 'REFERENCE_CYCLE'})
                    continue
                if depth >= max_depth:
                    omitted(target, 'DEPTH_BUDGET_EXHAUSTED', target_path, rid)
                    continue
                if target not in visited:
                    stack.append((target, target_path, depth + 1))
    selected_targets = {('version', v) for v in visited} | {('reference', t['reference_id']) for t in trace}
    for g in snapshot.get('gaps', []):
        if any((t['kind'], t['id']) in selected_targets for t in [g['target'], *g.get('affected_refs', [])]):
            gaps.append(g)
    complete = not (unexpanded or cycles or gaps)
    return {'schema_version': 'reference-expansion-1', 'snapshot_id': snapshot.get('snapshot_id'),
            'mode': mode, 'start_version_ids': start_version_ids, 'visited_version_ids': nodes,
            'selected_reference_ids': selected, 'trace': trace, 'cycles': cycles,
            'unexpanded': unexpanded, 'gaps': gaps, 'complete': complete,
            'state': 'complete' if complete else 'incomplete',
            'budgets': {'max_depth': max_depth, 'max_nodes': max_nodes, 'max_edges': max_edges},
            'used': {'nodes': len(nodes), 'edges': edges}, 'scope_complete': False,
            'candidate_only': mode == 'candidates', 'executable': False}
