"""Synthetic source-bound scenarios; none are asserted food requirements."""
from copy import deepcopy

import pytest

from food_kb.taxonomy.classify import classify_food
from food_kb.taxonomy.conditions import evaluate_condition, validate_condition
from food_kb.taxonomy.inheritance import inherit_requirements, semantic_signature


EVIDENCE = [{'source_id': 'synthetic', 'quote': 'SYNTHETIC fixture; not a food standard.'}]


def fact(value, kind='string', state='verified', role='sample_fact'):
    return {'raw_value': value, 'normalized_value': value, 'type': kind, 'source_ref': 'SYNTHETIC_INPUT',
            'role': role, 'verification_state': state}


def predicate(field='process', value='熟制'):
    return {'op': 'eq', 'field': field, 'value': value}


def condition(ast=None, state=None):
    return {'ast': ast, 'state': state or ('structured' if ast else 'unconditional'),
            'raw': 'SYNTHETIC condition', 'evidence': deepcopy(EVIDENCE), 'required_facts': []}


def node(node_id, name, ast=None, kind='category'):
    return {'id': node_id, 'name': name, 'kind': kind,
            'names': [{'value': name, 'kind': 'canonical', 'verification_state': 'source_claim', 'evidence': deepcopy(EVIDENCE)}],
            'condition': condition(ast), 'exclusions': [], 'evidence': deepcopy(EVIDENCE), 'review_state': 'review_pending'}


def edge(child, parent):
    return {'id': child + ':' + parent, 'child_id': child, 'parent_id': parent, 'kind': 'parent',
            'condition': condition(), 'evidence': deepcopy(EVIDENCE), 'review_state': 'review_pending'}


def requirement(requirement_id, node_id, project='合成项目', value='1', basis='湿基', method='SYNTH A'):
    return {'id': requirement_id, 'node_id': node_id, 'semantic_state': 'complete',
            'semantic': {'project': project, 'value': value, 'unit': 'mg/kg', 'comparator': '≤',
                         'basis_raw': basis, 'method_raw': method},
            'condition': condition(), 'exclusions': [], 'overrides': [], 'evidence': deepcopy(EVIDENCE),
            'review_state': 'review_pending', 'coverage_state': 'complete'}


def namespace(nodes, relations=(), requirements=(), name='SYNTHETIC_A', coverage='complete'):
    return {'namespace': name, 'standard': 'SYNTHETIC', 'standard_version_id': name + ':v1',
            'identity_status': 'unverified', 'coverage_state': coverage, 'semantic_coverage': 'partial',
            'nodes': nodes, 'relations': list(relations), 'requirements': list(requirements)}


def snapshot(*groups):
    return {'schema_version': 'taxonomy-1', 'snapshot_id': 'SYNTHETIC_SNAPSHOT',
            'namespaces': list(groups), 'review_state': 'review_pending', 'executable': False}


@pytest.mark.parametrize(('facts', 'expected'), [({}, 'unknown'), ({'process': '熟制'}, 'unknown'),
    ({'process': fact('熟制', state='unverified')}, 'unknown'), ({'process': fact('熟制')}, 'applicable'),
    ({'process': fact('生鲜')}, 'not_applicable'), ({'process': fact('熟制', state='conflicting')}, 'conflicting'),
    ({'process': [fact('熟制'), fact('生鲜')]}, 'conflicting'),
    ({'process': fact('熟制', role='self_reported_category')}, 'unknown')])
def test_four_condition_states(facts, expected):
    assert evaluate_condition(predicate(), facts)['status'] == expected


@pytest.mark.parametrize('removed', ['raw_value', 'normalized_value', 'source_ref', 'role', 'type'])
def test_incomplete_fact_cannot_satisfy_exists(removed):
    value = fact('熟制'); del value[removed]
    assert evaluate_condition({'op': 'exists', 'field': 'process'}, {'process': value})['status'] == 'unknown'


@pytest.mark.parametrize(('op', 'first', 'second', 'expected'), [
    ('all', '熟制', None, 'unknown'), ('all', '生鲜', None, 'not_applicable'),
    ('any', '熟制', None, 'applicable'), ('any', '生鲜', None, 'unknown'),
    ('all', '熟制', '冲突', 'conflicting'), ('any', '熟制', '冲突', 'conflicting')])
def test_boolean_combinations_keep_unknown_and_conflict(op, first, second, expected):
    facts = {'process': fact(first)}
    if second:
        facts['food_part'] = fact(second, state='conflicting')
    ast = {'op': op, 'args': [predicate(), predicate('food_part', '根')]}
    assert evaluate_condition(ast, facts)['status'] == expected


def test_not_unknown_stays_unknown_and_registered_proportion_is_numeric():
    assert evaluate_condition({'op': 'not', 'arg': predicate()}, {})['status'] == 'unknown'
    ast = {'op': 'range', 'field': 'ingredient_proportion', 'value_type': 'number',
           'lower': '10', 'upper': '20', 'lower_inclusive': True, 'upper_inclusive': False}
    assert evaluate_condition(ast, {'ingredient_proportion': fact('10', 'number')})['status'] == 'applicable'
    assert evaluate_condition(ast, {'ingredient_proportion': fact('20', 'number')})['status'] == 'not_applicable'
    assert evaluate_condition(ast, {'ingredient_proportion': fact('10', 'string')})['status'] == 'unknown'


@pytest.mark.parametrize('ast', [{'op': 'exec', 'code': '1'}, {'op': 'all', 'args': 'wrong'},
    {'op': 'all', 'args': None}, {'op': ['not'], 'arg': predicate()},
    {'op': 'eq', 'field': 'unregistered', 'value': 'x'}, {'op': 'eq', 'field': [], 'value': 'x'},
    {'op': 'range', 'field': 'ingredient_proportion', 'lower': 'NaN', 'upper': '10',
     'lower_inclusive': True, 'upper_inclusive': True}])
def test_invalid_predicate_returns_diagnostics(ast):
    result = evaluate_condition(ast, {})
    assert result['status'] == 'unknown' and result['issues']


def test_condition_tree_budget_and_cycle_do_not_crash():
    ast = predicate()
    for _ in range(40):
        ast = {'op': 'not', 'arg': ast}
    assert validate_condition(ast)
    assert evaluate_condition(ast, {})['status'] == 'unknown'
    cycle = {'op': 'not'}; cycle['arg'] = cycle
    assert evaluate_condition(cycle, {})['status'] == 'unknown'


@pytest.mark.parametrize('declared', ['process', ['unregistered'], [None], {}, None])
def test_invalid_required_facts_are_rejected(declared):
    value = condition(predicate()); value['required_facts'] = declared
    assert evaluate_condition(value, {'process': fact('熟制')})['issues']


@pytest.mark.parametrize('evidence', [None, [], 'source', {'source_id': 'x'}, [None], [{}]])
def test_source_predicate_needs_evidence_list(evidence):
    value = condition(); value['evidence'] = evidence
    assert evaluate_condition(value, {})['status'] == 'unknown'


def test_unresolved_prose_and_self_reported_categories_never_become_true():
    value = condition(predicate(), state='unresolved')
    assert evaluate_condition(value, {'process': fact('熟制')})['status'] == 'unknown'
    value['required_facts'] = ['process']
    assert evaluate_condition(value, {'process': [fact('熟制'), fact('生鲜')]})['status'] == 'conflicting'
    for field in ('category_id', 'category_namespace'):
        assert evaluate_condition(predicate(field, 'A'), {field: fact('A')})['status'] == 'unknown'


@pytest.mark.parametrize(('query', 'expected'), [('苹果', ['apple']), ('苹果汁', ['juice']),
    ('萝卜', ['root']), ('萝卜叶', ['leaf']), ('苹果罐头', [])])
def test_names_are_exact_and_parts_do_not_cross(query, expected):
    data = snapshot(namespace([node('apple', '苹果'), node('juice', '苹果汁'), node('root', '萝卜'), node('leaf', '萝卜叶')]))
    result = classify_food(data, {'food_name': fact(query)})
    assert [branch['category_id'] for branch in result['namespaces'][0]['branches']] == expected


def test_alias_requires_its_own_verification_and_evidence():
    item = node('apple', '苹果')
    item['names'].append({'value': '合成俗称', 'kind': 'alias', 'verification_state': 'source_claim', 'evidence': EVIDENCE})
    data = snapshot(namespace([item])); facts = {'food_name': fact('合成俗称')}
    assert not classify_food(data, facts)['namespaces'][0]['branches']
    item['names'][1]['verification_state'] = 'verified'
    assert classify_food(data, facts)['namespaces'][0]['branches'][0]['status'] == 'applicable'
    item['names'][1]['evidence'] = []
    assert not classify_food(data, facts)['namespaces'][0]['branches']


def test_each_standard_namespace_retains_its_category_and_source_path():
    data = snapshot(namespace([node('A:01', '苹果')]), namespace([node('B:10', '苹果')], name='SYNTHETIC_B'))
    result = classify_food(data, {'food_name': fact('苹果')})
    assert [(group['namespace'], group['branches'][0]['category_id']) for group in result['namespaces']] == [
        ('SYNTHETIC_A', 'A:01'), ('SYNTHETIC_B', 'B:10')]
    assert len(classify_food(data, {'food_name': fact('苹果')}, ['SYNTHETIC_B'])['namespaces']) == 1
    data['namespaces'][0]['version_binding'] = {'basis': 'synthetic_claim', 'state': 'unverified'}
    data['namespaces'][0]['gaps'] = [{'kind': 'synthetic_missing_scope'}]
    group = classify_food(data, {'food_name': fact('苹果')})['namespaces'][0]
    assert group['version_binding_state'] == 'unverified' and group['version_applicability'] == 'unknown'
    assert group['gap_count'] == 1 and group['gaps'][0]['kind'] == 'synthetic_missing_scope'


def test_milk_retains_process_branches_and_exact_engineering_hints_are_not_aliases():
    data = snapshot(namespace([node('pasteurized', '巴氏杀菌乳', predicate(value='巴氏杀菌')),
                               node('sterile', '灭菌乳', predicate(value='灭菌'))]))
    data['normalization_hints'] = [{'value': '牛奶', 'targets': ['巴氏杀菌乳', '灭菌乳'], 'kind': 'engineering_ambiguity',
                                  'verification_state': 'unverified', 'evidence': deepcopy(EVIDENCE)}]
    facts = {'food_name': fact('牛奶')}
    branches = classify_food(data, facts)['namespaces'][0]['branches']
    assert len(branches) == 2 and all(branch['status'] == 'unknown' for branch in branches)
    assert all('process' in branch['missing_facts'] and not branch['matched_names'] for branch in branches)
    facts['process'] = fact('巴氏杀菌')
    branches = classify_food(data, facts)['namespaces'][0]['branches']
    assert branches[0]['status'] == 'applicable' and branches[1]['status'] == 'not_applicable'
    assert not classify_food(data, {'food_name': fact('牛奶味糖果')})['namespaces'][0]['branches']


def test_self_reported_category_is_only_hint_even_when_marked_verified():
    item = node('A', '合成类', predicate('category_id', 'A'))
    result = classify_food(snapshot(namespace([item])), {'food_name': fact('未知名'), 'category_id': fact('A')})
    branch = result['namespaces'][0]['branches'][0]
    assert branch['status'] == 'unknown' and 'self_reported_category_hint' in branch['candidate_reasons']
    assert 'category_id' in branch['missing_facts']


def test_unknown_name_and_no_relationship_count_is_unknown_not_zero():
    data = snapshot(namespace([node('A', '苹果')], coverage='partial'))
    group = classify_food(data, {'food_name': fact('未知俗称')})['namespaces'][0]
    assert group['status'] == 'unknown' and group['association_count'] is None
    branch = classify_food(data, {'food_name': fact('苹果')})['namespaces'][0]['branches'][0]
    assert branch['association_count'] is None and branch['observed_candidates'] == 0


def test_complete_sourced_nian_gao_path_is_visible_but_never_task_mandatory():
    group = namespace([node('nian', '年糕'), node('rice', '米粉制品'), node('table', '合成检验表', kind='inspection_table')],
                      [edge('nian', 'rice'), edge('rice', 'table')], [requirement('r', 'table')])
    branch = classify_food(snapshot(group), {'food_name': fact('年糕')})['namespaces'][0]['branches'][0]
    item = branch['requirements'][0]
    assert [step['label'] for step in item['source_path'] if step['kind'] == 'node'] == ['年糕', '米粉制品', '合成检验表']
    assert len([step for step in item['source_path'] if step['kind'] == 'relation']) == 2
    assert item['status'] == 'applicable' and item['trust_state'] == 'review_pending'
    assert not item['mandatory_for_task'] and not item['executable']


def test_each_inherited_scope_and_requirement_is_checked_separately():
    parent = node('parent', '上级', predicate(value='熟制'))
    first = requirement('one', 'parent'); second = requirement('two', 'parent')
    second['condition'] = condition(predicate('food_part', '叶'))
    group = namespace([node('child', '萝卜'), parent], [edge('child', 'parent')], [first, second])
    result = inherit_requirements(group, 'child', {'process': fact('熟制'), 'food_part': fact('根')})
    assert [item['status'] for item in result['requirements']] == ['applicable', 'not_applicable']
    assert all(item['status'] == 'unknown' for item in inherit_requirements(group, 'child', {})['requirements'])
    assert 'process' in inherit_requirements(group, 'child', {})['missing_facts']


def test_explicit_name_exclusion_requires_complete_non_conflicting_facts():
    item = node('root', '萝卜')
    item['exclusions'] = [{'ast': None, 'state': 'explicit_names', 'raw': 'SYNTHETIC 不包括萝卜叶',
                           'names': ['萝卜叶'], 'evidence': deepcopy(EVIDENCE), 'required_facts': ['food_name']}]
    item['names'].append({'value': '萝卜叶', 'kind': 'explicit_example', 'evidence': EVIDENCE})
    data = snapshot(namespace([item]))
    assert classify_food(data, {'food_name': fact('萝卜叶')})['namespaces'][0]['branches'][0]['status'] == 'not_applicable'
    incomplete = fact('萝卜叶'); del incomplete['raw_value']
    assert classify_food(data, {'food_name': incomplete})['namespaces'][0]['branches'][0]['status'] == 'unknown'
    conflicting = {'food_name': [fact('萝卜'), fact('萝卜叶')]}
    branch = classify_food(data, conflicting)['namespaces'][0]['branches'][0]
    assert branch['status'] == 'conflicting' and branch['exclusions_checked'][0]['status'] == 'conflicting'


def test_unproven_edges_and_cycles_are_not_silent_complete_ancestry():
    group = namespace([node('a', '苹果'), node('b', '上级')], [edge('a', 'b'), edge('b', 'a')], [requirement('r', 'b')])
    result = inherit_requirements(group, 'a', {})
    assert any(issue['code'] == 'INHERITANCE_CYCLE' for issue in result['issues'])
    group['relations'][0]['evidence'] = []
    result = inherit_requirements(group, 'a', {})
    assert not result['requirements'] and any(issue['code'] == 'INHERITANCE_SOURCE_GAP' for issue in result['issues'])


def test_explicit_more_specific_override_respects_conditions_and_ancestry():
    general = requirement('general', 'parent'); special = requirement('special', 'child', value='2')
    special['condition'] = condition(predicate(value='熟制'))
    special['overrides'] = [{'requirement_id': 'general', 'evidence': EVIDENCE, 'condition': condition()}]
    group = namespace([node('child', '苹果'), node('parent', '上级')], [edge('child', 'parent')], [general, special])
    cooked = {item['id']: item for item in inherit_requirements(group, 'child', {'process': fact('熟制')})['requirements']}
    assert cooked['general']['status'] == 'not_applicable' and cooked['general']['suppressed_by'] == 'special'
    raw = {item['id']: item for item in inherit_requirements(group, 'child', {'process': fact('生鲜')})['requirements']}
    assert raw['general']['status'] == 'applicable' and raw['special']['status'] == 'not_applicable'
    unknown = {item['id']: item for item in inherit_requirements(group, 'child', {})['requirements']}
    assert unknown['general']['status'] == 'unknown'
    special['overrides'] = ['general']
    result = inherit_requirements(group, 'child', {'process': fact('熟制')})
    assert any(issue['code'] == 'OVERRIDE_UNVERIFIED' for issue in result['issues'])


def test_common_and_unique_requirements_use_complete_semantics():
    left = node('left', '合成食品'); right = node('right', '合成食品')
    reqs = [requirement('left-common', 'left'), requirement('right-common', 'right'),
            requirement('left-dry', 'left', basis='干基'), requirement('right-method', 'right', method='SYNTH B')]
    group = classify_food(snapshot(namespace([left, right], requirements=reqs)), {'food_name': fact('合成食品')})['namespaces'][0]
    assert len(group['common_requirements']) == 1
    assert [len(item['requirements']) for item in group['branch_specific_requirements']] == [1, 1]
    reqs[0]['semantic_state'] = 'partial'
    assert semantic_signature(reqs[0]) is None
    group = classify_food(snapshot(namespace([left, right], requirements=reqs)), {'food_name': fact('合成食品')})['namespaces'][0]
    assert not group['common_requirements'] and group['comparison_unknown'][0]['requirement_id'] == 'left-common'


def test_same_source_association_does_not_claim_full_semantic_equivalence():
    shared = requirement('shared', 'table'); shared['semantic_state'] = 'partial'
    group = namespace([node('a', '合成食品'), node('b', '合成食品'), node('table', '合成表', kind='inspection_table')],
                      [edge('a', 'table'), edge('b', 'table')], [shared])
    result = classify_food(snapshot(group), {'food_name': fact('合成食品')})['namespaces'][0]
    assert not result['common_requirements']
    assert result['common_associations'][0]['requirement_id'] == 'shared'


def test_read_only_input_snapshot_and_fact_objects_are_not_mutated():
    data = snapshot(namespace([node('a', '苹果')], requirements=[requirement('r', 'a')]))
    facts = {'food_name': fact('苹果')}; original_data = deepcopy(data); original_facts = deepcopy(facts)
    classify_food(data, facts)
    assert data == original_data and facts == original_facts


@pytest.mark.parametrize(('facts', 'expected'), [({}, 'unknown'), ({'primary_heat_treatment': fact('巴氏杀菌')}, 'unknown'),
    ({'primary_heat_treatment': fact('灭菌')}, 'not_applicable'),
    ({'primary_heat_treatment': [fact('巴氏杀菌'), fact('灭菌')]}, 'conflicting')])
def test_necessary_partial_condition_can_exclude_but_cannot_confirm(facts, expected):
    value = condition(predicate('primary_heat_treatment', '巴氏杀菌'), state='partial')
    value['unresolved'] = [{'raw': '其他真实定义条件尚未结构化。'}]
    value['required_facts'] = ['primary_heat_treatment', 'raw_material']
    result = evaluate_condition(value, facts)
    assert result['status'] == expected and 'raw_material' in result['missing_facts']


def test_source_necessary_process_condition_narrows_engineering_milk_hint():
    milk = node('milk', '巴氏杀菌乳', predicate('primary_heat_treatment', '巴氏杀菌'))
    sterile = node('sterile', '灭菌乳', predicate('primary_heat_treatment', '灭菌'))
    for value in (milk, sterile):
        value['condition']['state'] = 'partial'
        value['condition']['unresolved'] = [{'raw': '其他定义未结构化。'}]
    data = snapshot(namespace([milk, sterile])); data['normalization_hints'] = [
        {'value': '牛奶', 'targets': ['巴氏杀菌乳', '灭菌乳'], 'evidence': EVIDENCE}]
    group = classify_food(data, {'food_name': fact('牛奶'), 'primary_heat_treatment': fact('巴氏杀菌')})['namespaces'][0]
    assert [branch['status'] for branch in group['branches']] == ['unknown', 'not_applicable']
    assert all(not branch['executable'] and not branch['mandatory_for_task'] for branch in group['branches'])
    fragment = classify_food(data, {'food_name': fact('牛奶'), 'process': fact('巴氏杀菌')})['namespaces'][0]
    assert all(branch['status'] == 'unknown' for branch in fragment['branches'])


@pytest.mark.parametrize(('value', 'kind', 'expected'), [('79.9', 'number', 'not_applicable'),
    ('80', 'number', 'applicable'), ('85', 'string', 'unknown'), ('101', 'number', 'unknown'),
    ('-1', 'number', 'unknown'), ('NaN', 'number', 'unknown'), (True, 'number', 'unknown')])
def test_milk_ratio_requires_named_percentage_fact_with_valid_domain(value, kind, expected):
    ast = {'op': 'ge', 'field': 'milk_ingredient_proportion_percent', 'value_type': 'number', 'value': '80'}
    facts = {'milk_ingredient_proportion_percent': fact(value, kind), 'ingredient_proportion': fact('85', 'number')}
    assert evaluate_condition(ast, facts)['status'] == expected
    assert evaluate_condition(ast, {'ingredient_proportion': fact('85', 'number')})['status'] == 'unknown'


@pytest.mark.parametrize('value', ['巴氏', '超高温瞬时灭菌', '热处理', 'UHT', '不详', ''])
def test_final_heat_treatment_unknown_vocabulary_never_excludes_by_spelling(value):
    ast = predicate('primary_heat_treatment', '巴氏杀菌')
    result = evaluate_condition(condition(ast, state='partial'), {'primary_heat_treatment': fact(value)})
    assert result['status'] == 'unknown' and 'primary_heat_treatment' in result['missing_facts']


def test_extra_declared_required_fact_is_not_ignored_by_satisfied_ast():
    value = condition(predicate()); value['required_facts'] = ['process', 'raw_material']
    assert evaluate_condition(value, {'process': fact('熟制')})['status'] == 'unknown'
    assert evaluate_condition(value, {'process': fact('熟制'), 'raw_material': fact('米')})['status'] == 'applicable'
    assert evaluate_condition(value, {'process': fact('熟制'), 'raw_material': fact('米', state='conflicting')})['status'] == 'conflicting'
    assert evaluate_condition(value, {'process': fact('生鲜')})['status'] == 'not_applicable'
    unconditional = condition(); unconditional['required_facts'] = ['process']
    assert evaluate_condition(unconditional, {})['status'] == 'unknown'


def test_any_satisfied_ast_keeps_three_valued_logic_when_other_argument_missing():
    value = condition({'op': 'any', 'args': [predicate(), predicate('food_part', '根')]})
    value['required_facts'] = ['process', 'food_part']
    assert evaluate_condition(value, {'process': fact('熟制')})['status'] == 'applicable'


def test_classification_ancestry_is_visible_without_any_requirements():
    group = namespace([node('a', '苹果'), node('b', '仁果类', predicate('food_part', '果实')), node('c', '水果')],
                      [edge('a', 'b'), edge('b', 'c')], coverage='partial')
    branch = classify_food(snapshot(group), {'food_name': fact('苹果')})['namespaces'][0]['branches'][0]
    assert [step['label'] for step in branch['path']] == ['苹果', '仁果类', '水果']
    assert not branch['requirements'] and branch['association_count'] is None
    assert len([step for step in branch['inheritance_paths'][0] if step['kind'] == 'relation']) == 2
    assert 'food_part' in branch['missing_facts'] and branch['unresolved_conditions']
