"""Read-only classification against one immutable, standard-specific snapshot."""
from collections import defaultdict
from copy import deepcopy
import re
import unicodedata

from .conditions import checked_fact, combine_states, evaluate_condition
from .inheritance import inherit_requirements, object_applicability, semantic_signature

ALGORITHM_VERSION = 'taxonomy-engine-1'


def normalize_name(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)) if isinstance(value, str) else ''


def _fact_values(facts, field):
    value = facts.get(field) if isinstance(facts, dict) else None
    values = value if isinstance(value, list) else [value]
    return [item for item in values if isinstance(item, dict) and isinstance(item.get('normalized_value'), str)]


def _name_fact_state(facts):
    value = checked_fact('food_name', facts)
    return evaluate_condition({'op': 'exists', 'field': 'food_name'}, {'food_name': value})['status']


def _condition_gaps(branch):
    checks = [branch['condition_result']] + branch['exclusions_checked']
    for path in branch.get('inheritance_paths', []):
        for step in path:
            checks.append(step.get('condition_result', {}))
            checks.extend(step.get('exclusions_checked', []))
    for requirement in branch['requirements']:
        checks.extend([requirement['condition_result']] + requirement['exclusions_checked'])
        for path in requirement['source_paths']:
            for step in path:
                checks.append(step.get('condition_result', {}))
                checks.extend(step.get('exclusions_checked', []))
    return [check for check in checks if check.get('status') in {'unknown', 'conflicting'}]


def _shared_requirements(branches):
    possible = [branch for branch in branches if branch['status'] != 'not_applicable']
    if not possible:
        return [], [], [], []
    maps = []
    for branch in possible:
        mapping = defaultdict(list)
        for requirement in branch['requirements']:
            if requirement['status'] == 'not_applicable':
                continue
            signature = semantic_signature(requirement)
            if signature:
                mapping[signature].append(requirement)
        maps.append(mapping)
    shared_keys = set.intersection(*(set(value) for value in maps)) if len(maps) > 1 else set()
    common = [{'semantic_signature': signature,
               'semantic': maps[0][signature][0]['semantic'],
               'members': [{'category_id': branch['category_id'], 'requirement_ids': [item['id'] for item in mapping[signature]]}
                           for branch, mapping in zip(possible, maps)],
               'status': combine_states([item['status'] for mapping in maps for item in mapping[signature]]),
               'executable': False, 'mandatory_for_task': False}
              for signature in sorted(shared_keys)]
    # An identical immutable source ID proves only a common source association.
    id_sets = [{item['id'] for item in branch['requirements'] if item['status'] != 'not_applicable'} for branch in possible]
    common_ids = set.intersection(*id_sets) if len(possible) > 1 else set()
    associations = [{'requirement_id': requirement_id, 'category_ids': [branch['category_id'] for branch in possible],
                     'comparison_state': 'same_source_association', 'executable': False, 'mandatory_for_task': False}
                    for requirement_id in sorted(common_ids)]
    specific = []; unknown = []
    for branch, mapping in zip(possible, maps):
        distinct = [item for signature, values in mapping.items() if signature not in shared_keys for item in values]
        specific.append({'category_id': branch['category_id'], 'requirements': distinct})
        unknown.extend({'category_id': branch['category_id'], 'requirement_id': item['id'],
                        'reason': 'comparison_unknown', 'semantic': item.get('semantic')}
                       for item in branch['requirements'] if item['status'] != 'not_applicable' and
                       not semantic_signature(item) and item['id'] not in common_ids)
    return common, associations, specific, unknown


def classify_food(snapshot, facts, namespaces=None):
    """Names locate candidates; conditions and provenance determine each state.

    No snapshot is approved or published by this function, and classification
    never declares a row mandatory for the current inspection task.
    """
    facts = deepcopy(facts) if isinstance(facts, dict) else {}
    names = {normalize_name(item['normalized_value']) for item in _fact_values(facts, 'food_name')}
    names.discard('')
    normalization_hints = [hint for hint in snapshot.get('normalization_hints', [])
                           if normalize_name(hint.get('value')) in names and hint.get('evidence')]
    hint_targets = {normalize_name(target) for hint in normalization_hints for target in hint.get('targets', [])}
    hint_ids = {item['normalized_value'] for item in _fact_values(facts, 'category_id')}
    hint_namespaces = {item['normalized_value'] for item in _fact_values(facts, 'category_namespace')}
    selected = set(namespaces) if namespaces is not None else None
    result = {'schema_version': 'classification-1', 'algorithm_version': ALGORITHM_VERSION,
              'snapshot_id': snapshot.get('snapshot_id'),
              'food_name': ' / '.join(sorted(names)), 'facts': facts, 'namespaces': [],
              'scope_complete': False, 'executable': False, 'mandatory_for_task': False,
              'review_state': snapshot.get('review_state', 'review_pending'), 'issues': []}
    name_state = _name_fact_state(facts)
    if not names:
        result['issues'].append({'code': 'FOOD_NAME_MISSING', 'message': '缺少食品名称线索；分类输入需保留原始值和来源。'})
    for namespace in snapshot.get('namespaces', []):
        if selected is not None and namespace['namespace'] not in selected:
            continue
        group = {key: deepcopy(namespace.get(key)) for key in ('namespace', 'standard', 'standard_version_id',
                  'identity_status', 'coverage_state', 'semantic_coverage', 'version_binding', 'version_applicability')}
        group['version_binding_state'] = (namespace.get('version_binding') or {}).get('state', 'unverified')
        group['version_applicability'] = namespace.get('version_applicability', 'unknown')
        group['gaps'] = deepcopy(namespace.get('gaps', []))
        group['gap_count'] = len(group['gaps'])
        branches = []
        for node in namespace.get('nodes', []):
            if node.get('kind') == 'inspection_table':
                continue
            matched = []
            hinted_names = []
            for name in node.get('names', []):
                kind = name.get('kind')
                acceptable = kind in {'canonical', 'explicit_example'} or (
                    kind == 'alias' and name.get('verification_state') == 'verified')
                if acceptable and name.get('evidence') and normalize_name(name.get('value')) in names:
                    matched.append(deepcopy(name))
                if acceptable and name.get('evidence') and normalize_name(name.get('value')) in hint_targets:
                    hinted_names.append(deepcopy(name))
            hinted = node['id'] in hint_ids and (not hint_namespaces or namespace['namespace'] in hint_namespaces)
            node_result = object_applicability(node, facts)
            condition_candidate = bool(node.get('condition', {}).get('ast') and
                                       node_result['condition_result']['required_facts'] and
                                       node_result['condition_result']['status'] in {'applicable', 'conflicting'})
            if not matched and not hinted and not hinted_names and not condition_candidate:
                continue
            inherited = inherit_requirements(namespace, node['id'], facts)
            branch = {'category_id': node['id'], 'label': node['name'], 'code': node.get('code'),
                      'status': combine_states([node_result['status'], name_state]) if matched else node_result['status'],
                      'path': [{'category_id': node['id'], 'label': node['name'], 'evidence': node.get('evidence', [])}],
                      'supporting_evidence': deepcopy(node.get('evidence', [])) + [evidence for name in matched for evidence in name['evidence']],
                      'matched_names': matched, 'candidate_reasons': (['exact_source_name'] if matched else []) +
                         (['self_reported_category_hint'] if hinted else []) + (['engineering_normalization_hint'] if hinted_names else []) +
                         (['structured_definition'] if condition_candidate else []),
                      'normalization_hints': deepcopy(normalization_hints) if hinted_names else [],
                      'condition_result': node_result['condition_result'], 'exclusions_checked': node_result['exclusions_checked'],
                      'requirements': inherited['requirements'], 'association_count': inherited['association_count'],
                      'observed_candidates': len(inherited['requirements']), 'requirement_coverage_state': inherited['coverage_state'],
                      'missing_facts': sorted(set(inherited.get('missing_facts', [])) |
                         set(node_result['condition_result']['missing_facts']) |
                         {field for value in node_result['exclusions_checked'] for field in value['missing_facts']} |
                         ({'food_name'} if matched and name_state == 'unknown' else set())),
                      'issues': inherited['issues'], 'review_state': node.get('review_state', 'review_pending'),
                      'executable': False, 'mandatory_for_task': False}
            if not matched and not condition_candidate and node_result['status'] not in {'not_applicable', 'conflicting'}:
                branch['status'] = 'unknown'
            if name_state == 'conflicting':
                branch['status'] = 'conflicting'
            paths = inherited.get('ancestry_paths', [])
            branch['inheritance_paths'] = paths
            if paths:
                branch['path'] = [{key: step[key] for key in ('category_id', 'label', 'node_kind', 'evidence')}
                                  for step in paths[0] if step['kind'] == 'node']
            branch['unresolved_conditions'] = _condition_gaps(branch)
            branches.append(branch)
        common, associations, specific, unknown = _shared_requirements(branches)
        known_association_count = (len({item['id'] for branch in branches for item in branch['requirements']})
                                   if branches and all(branch['association_count'] is not None for branch in branches) else None)
        group.update(branches=branches, status=combine_states([branch['status'] for branch in branches], 'any') if branches else 'unknown',
                     common_requirements=common, common_associations=associations,
                     branch_specific_requirements=specific, comparison_unknown=unknown,
                     missing_facts=sorted({field for branch in branches for field in branch['missing_facts']}),
                     observed_candidates=len(branches), association_count=known_association_count,
                     scope_complete=False, executable=False, mandatory_for_task=False)
        if not branches:
            group['issues'] = [{'code': 'NO_EXACT_CANDIDATE', 'message': '本快照没有精确、有来源的名称关联；不表示没有适用类别或要求。'}]
        result['namespaces'].append(group)
    result['missing_facts'] = sorted({field for group in result['namespaces'] for field in group['missing_facts']} |
                                     ({'food_name'} if not names else set()))
    if selected:
        known = {group['namespace'] for group in result['namespaces']}
        result['issues'].extend({'code': 'NAMESPACE_NOT_IN_SNAPSHOT', 'namespace': value} for value in sorted(selected - known))
    return result
