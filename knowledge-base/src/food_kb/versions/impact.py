"""Compare fixed snapshots and trace explicit dependencies without mutating history."""
from copy import deepcopy
import hashlib
import json

ALGORITHM_VERSION = 'version-impact-1'
COLLECTIONS = {'version': ('versions', 'version_id'), 'classification': ('classifications', 'id'),
               'requirement': ('requirements', 'id'), 'reference': ('references', 'reference_id'),
               'event': ('events', 'event_id'), 'gap': ('gaps', 'gap_id')}
_DERIVED = {'snapshot_id', 'input_hash', 'payload_hash', 'created_at'}


def _signature(item):
    content = {key: value for key, value in item.items() if key not in _DERIVED}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _members(snapshot, collection, id_key):
    members = {}
    for item in snapshot.get(collection, []):
        identifier = item.get(id_key) if isinstance(item, dict) else None
        if not isinstance(identifier, str) or not identifier or identifier in members:
            raise ValueError(f'{collection} requires unique non-empty {id_key} values.')
        members[identifier] = item
    return members


def _ref(value):
    if not isinstance(value, dict) or not isinstance(value.get('kind'), str) or not isinstance(value.get('id'), str):
        return None
    return (value['kind'], value['id']) if value['kind'] and value['id'] else None


def impact_snapshots(old, new, max_nodes=10000, max_edges=50000):
    """Changed identities seed a bounded trace over evidence-bearing dependencies.

    A dependency is ``{dependent:{kind,id}, depends_on:{kind,id}, evidence:[...]}``.
    Old and new links are both retained so removing an object cannot erase its
    historical consumers. Changed references/events also mark their explicitly
    identified consuming versions. Impact means recheck, never automatic approval.
    """
    if type(max_nodes) is not int or not 1 <= max_nodes <= 100000 or type(max_edges) is not int or not 1 <= max_edges <= 200000:
        raise ValueError('Impact budgets must be positive bounded integers.')
    result = {'schema_version': 'version-impact-1', 'algorithm_version': ALGORITHM_VERSION,
              'old_snapshot_id': old.get('snapshot_id'), 'new_snapshot_id': new.get('snapshot_id'),
              'changes': [], 'affected': [], 'unresolved': [], 'trace': [],
              'scope_complete': False, 'executable': False, 'requires_recheck': True,
              'history_unchanged': True, 'budget': {'max_nodes': max_nodes, 'max_edges': max_edges}}
    seeds = set(); reasons = {}; links = {}; inspected = 0
    for kind, (collection, id_key) in COLLECTIONS.items():
        before = _members(old, collection, id_key); after = _members(new, collection, id_key)
        for identifier in sorted(before.keys() | after.keys()):
            prior = before.get(identifier); current = after.get(identifier)
            if prior is not None and current is not None and _signature(prior) == _signature(current):
                continue
            action = 'added' if prior is None else 'removed' if current is None else 'changed'
            key = (kind, identifier); seeds.add(key); reasons[key] = [{'kind': kind, 'id': identifier}]
            result['changes'].append({'kind': kind, 'id': identifier, 'action': action,
                                      'before_hash': _signature(prior) if prior is not None else None,
                                      'after_hash': _signature(current) if current is not None else None})
            if kind == 'gap':
                for item in (prior, current):
                    if item:
                        for affected in [item.get('target', {})] + item.get('affected_refs', []):
                            consumer = _ref(affected)
                            if consumer:
                                seeds.add(consumer)
                                reasons.setdefault(consumer, []).append({'kind': kind, 'id': identifier})
            if kind in {'reference', 'event'}:
                for item in (prior, current):
                    if not item:
                        continue
                    consumers = ([item.get('from_version_id')] if kind == 'reference' else
                                 [item.get('version_id'), item.get('target_version_id')] +
                                 item.get('target_version_ids', []) + item.get('version_ids', []))
                    for version_id in consumers:
                        if isinstance(version_id, str) and version_id:
                            consumer = ('version', version_id); seeds.add(consumer)
                            reasons.setdefault(consumer, []).append({'kind': kind, 'id': identifier})
    for snapshot in (old, new):
        for dependency in snapshot.get('dependencies', []):
            inspected += 1
            if inspected > max_edges:
                result['unresolved'].append({'code': 'IMPACT_EDGE_BUDGET', 'snapshot_id': snapshot.get('snapshot_id')})
                break
            source = _ref(dependency.get('depends_on')) if isinstance(dependency, dict) else None
            target = _ref(dependency.get('dependent')) if isinstance(dependency, dict) else None
            evidence = dependency.get('evidence', []) if isinstance(dependency, dict) else []
            if (not source or not target or not isinstance(evidence, list) or not evidence or
                    any(not isinstance(item, dict) or not item for item in evidence)):
                result['unresolved'].append({'code': 'DEPENDENCY_UNRESOLVED', 'dependency': deepcopy(dependency)})
                continue
            links.setdefault(source, {}).setdefault(target, deepcopy(evidence))
        if inspected > max_edges:
            break
    pending = sorted(seeds); visited = set()
    while pending:
        key = pending.pop(0)
        if key in visited:
            continue
        if len(visited) >= max_nodes:
            result['unresolved'].append({'code': 'IMPACT_NODE_BUDGET',
                                         'unexpanded': [{'kind': k, 'id': i} for k, i in [key] + pending if (k, i) not in visited]})
            break
        visited.add(key)
        result['affected'].append({'kind': key[0], 'id': key[1], 'reason_refs': reasons.get(key, []),
                                   'action': 'recheck', 'executable': False})
        for target, evidence in sorted(links.get(key, {}).items()):
            result['trace'].append({'depends_on': {'kind': key[0], 'id': key[1]},
                                    'dependent': {'kind': target[0], 'id': target[1]}, 'evidence': evidence})
            reason = {'kind': key[0], 'id': key[1]}
            if reason not in reasons.setdefault(target, []):
                reasons[target].append(reason)
            if target not in visited and target not in pending:
                pending.append(target)
    result['traversal_complete'] = not result['unresolved']
    return result
