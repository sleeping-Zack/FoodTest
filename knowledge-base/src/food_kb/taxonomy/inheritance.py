"""Source-bound ancestry expansion, with independent requirement conditions."""
from collections import defaultdict
from copy import deepcopy
import json

from .conditions import checked_fact, combine_states, evaluate_condition


def _names(facts):
    state = evaluate_condition({'op': 'exists', 'field': 'food_name'}, facts)['status']
    if state != 'applicable':
        return []
    value = checked_fact('food_name', facts)
    values = value if isinstance(value, list) else [value]
    return [item.get('normalized_value') for item in values if isinstance(item, dict) and
            item.get('verification_state') == 'verified' and item.get('source_ref')]


def check_exclusions(exclusions, facts):
    """An exclusion predicate is positive when the exclusion actually applies."""
    results = []
    for exclusion in exclusions or []:
        result = evaluate_condition(exclusion, facts)
        name_state = evaluate_condition({'op': 'exists', 'field': 'food_name'}, facts)['status']
        if result['status'] == 'conflicting' or (exclusion.get('names') and name_state == 'conflicting'):
            result['status'] = 'conflicting'
        elif exclusion.get('names') and isinstance(exclusion.get('evidence'), list) and exclusion['evidence']:
            names = _names(facts)
            if names and any(name in exclusion['names'] for name in names):
                result['status'] = 'applicable'
            elif names and not exclusion.get('ast') and exclusion.get('state') == 'explicit_names':
                result['status'] = 'not_applicable'
        result['scope_label'] = exclusion.get('scope_label')
        results.append(result)
    return results


def object_applicability(value, facts):
    condition = evaluate_condition(value.get('condition'), facts)
    exclusions = check_exclusions(value.get('exclusions', []), facts)
    inverted = [{'applicable': 'not_applicable', 'not_applicable': 'applicable',
                 'unknown': 'unknown', 'conflicting': 'conflicting'}[item['status']]
                for item in exclusions]
    return {'status': combine_states([condition['status']] + inverted),
            'condition_result': condition, 'exclusions_checked': exclusions}


def semantic_signature(requirement):
    """Only explicitly complete semantics permit cross-record equivalence.

    Identical source IDs are handled separately as the same association. A
    project name or shared cited standard alone never proves equivalence.
    """
    semantic = requirement.get('semantic')
    if requirement.get('semantic_state') != 'complete' or not isinstance(semantic, dict) or not semantic:
        return None
    if not semantic.get('project'):
        return None
    condition = requirement.get('condition') or {}
    if condition.get('state') not in {'structured', 'resolved', 'unconditional'}:
        return None
    exclusions = requirement.get('exclusions', [])
    if any(item.get('state') not in {'structured', 'resolved', 'unconditional', 'explicit_names'} for item in exclusions):
        return None
    def predicate(item):
        return {key: item.get(key) for key in ('state', 'ast', 'raw', 'names')}
    return json.dumps({'semantic': semantic, 'condition': predicate(condition),
                       'exclusions': [predicate(item) for item in exclusions]},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def inherit_requirements(namespace, category_id, facts):
    """Expand evidenced paths; unknown branches remain visible and non-executable."""
    nodes = {node['id']: node for node in namespace.get('nodes', [])}
    edges = defaultdict(list)
    issues = []
    for edge in namespace.get('relations', []):
        if edge.get('child_id') not in nodes or edge.get('parent_id') not in nodes or not edge.get('evidence'):
            issues.append({'code': 'INHERITANCE_SOURCE_GAP', 'relation_id': edge.get('id')})
            continue
        edges[edge['child_id']].append(edge)
    by_node = defaultdict(list)
    for requirement in namespace.get('requirements', []):
        by_node[requirement.get('node_id')].append(requirement)
    expanded = defaultdict(list)
    ancestry_paths = []
    path_budget = 10000
    visited_paths = 0

    def walk(node_id, path, states, seen):
        nonlocal visited_paths
        visited_paths += 1
        if visited_paths > path_budget or len(path) > 64:
            issues.append({'code': 'INHERITANCE_BUDGET', 'category_id': node_id})
            return
        node = nodes[node_id]
        applicability = object_applicability(node, facts)
        step = {'kind': 'node', 'category_id': node_id, 'label': node['name'],
                'node_kind': node.get('kind', 'category'), 'code': node.get('code'), 'evidence': node.get('evidence', []),
                'status': applicability['status'], 'condition_result': applicability['condition_result'],
                'exclusions_checked': applicability['exclusions_checked']}
        current_path = path + [step]
        current_states = states + [applicability['status']]
        for requirement in by_node[node_id]:
            checked = object_applicability(requirement, facts)
            item = deepcopy(requirement)
            item.update(checked)
            item.update(status=combine_states(current_states + [checked['status']]),
                        source_path=current_path, executable=False, mandatory_for_task=False,
                        trust_state=requirement.get('review_state', 'review_pending'),
                        semantic_comparison_state='complete' if semantic_signature(requirement) else 'partial')
            if not requirement.get('evidence'):
                item['status'] = 'unknown'
                item['issues'] = [{'code': 'REQUIREMENT_SOURCE_GAP'}]
            expanded[requirement['id']].append(item)
        continued = False
        for edge in sorted(edges[node_id], key=lambda value: value['id']):
            parent = edge['parent_id']
            if parent in seen:
                issues.append({'code': 'INHERITANCE_CYCLE', 'relation_id': edge['id']})
                continue
            continued = True
            edge_result = evaluate_condition(edge.get('condition'), facts)
            edge_step = {'kind': 'relation', 'relation_id': edge['id'], 'relation_kind': edge['kind'],
                         'child_id': node_id, 'parent_id': parent, 'evidence': edge['evidence'],
                         'status': edge_result['status'], 'condition_result': edge_result}
            walk(parent, current_path + [edge_step], current_states + [edge_result['status']], seen | {parent})
        if not continued:
            ancestry_paths.append(current_path)

    if category_id not in nodes:
        return {'requirements': [], 'association_count': None, 'coverage_state': 'unknown',
                'issues': [{'code': 'CATEGORY_NOT_IN_SNAPSHOT'}]}
    walk(category_id, [], [], {category_id})
    requirements = []
    for requirement_id, paths in sorted(expanded.items()):
        item = paths[0]
        item['status'] = combine_states([value['status'] for value in paths], 'any')
        item['source_paths'] = [value['source_path'] for value in paths]
        requirements.append(item)

    # Only an explicit, sourced more-specific override may suppress an ancestor.
    available = {item['id']: item for item in requirements}
    for specific in requirements:
        for override in specific.get('overrides', []):
            if not isinstance(override, dict) or not override.get('evidence'):
                issues.append({'code': 'OVERRIDE_UNVERIFIED', 'requirement_id': specific['id']})
                continue
            target = available.get(override.get('requirement_id'))
            if not target or target['id'] == specific['id']:
                issues.append({'code': 'OVERRIDE_TARGET_GAP', 'requirement_id': specific['id']})
                continue
            check = evaluate_condition(override.get('condition'), facts)
            relation = {'by_requirement_id': specific['id'], 'condition_result': check,
                        'evidence': override['evidence']}
            target.setdefault('more_specific_checks', []).append(relation)
            target_nodes = [step['category_id'] for step in target['source_path'] if step['kind'] == 'node']
            is_descendant = (specific.get('node_id') in target_nodes and
                             target.get('node_id') in target_nodes and
                             target_nodes.index(specific['node_id']) < target_nodes.index(target['node_id']))
            if not is_descendant:
                issues.append({'code': 'OVERRIDE_SCOPE_GAP', 'requirement_id': specific['id']})
                continue
            if specific['status'] == 'not_applicable':
                continue
            if specific['status'] == 'applicable' and check['status'] == 'applicable' and is_descendant:
                target['status'] = 'not_applicable'
                target['suppressed_by'] = specific['id']
            elif check['status'] == 'conflicting' or specific['status'] == 'conflicting':
                target['status'] = 'conflicting'
            elif check['status'] != 'not_applicable' and target['status'] == 'applicable':
                target['status'] = 'unknown'

    missing = sorted({field for item in requirements for path in item['source_paths'] for step in path
                      for field in step.get('condition_result', {}).get('missing_facts', [])} |
                     {field for path in ancestry_paths for step in path
                      for field in step.get('condition_result', {}).get('missing_facts', [])} |
                     {field for path in ancestry_paths for step in path for result in step.get('exclusions_checked', [])
                      for field in result['missing_facts']} |
                     {field for item in requirements for field in item['condition_result']['missing_facts']} |
                     {field for item in requirements for result in item['exclusions_checked']
                      for field in result['missing_facts']})
    complete = namespace.get('coverage_state') == 'complete' and not issues
    return {'requirements': requirements, 'ancestry_paths': ancestry_paths,
            'association_count': len(requirements) if requirements or complete else None,
            'coverage_state': 'complete' if complete else 'partial', 'missing_facts': missing,
            'issues': issues, 'executable': False, 'mandatory_for_task': False}
