"""Synthetic contract probes; these are not approved food requirements."""
from copy import deepcopy

import pytest

from food_kb.knowledge.conditions import evaluate_condition, validate_condition
from food_kb.knowledge.templates import RECORD_FIELDS, STANDARD_TEMPLATES
from food_kb.knowledge.validate import validate_candidate, validate_candidates


@pytest.fixture
def bundle():
    source = {'asset_id': 'synthetic-pdf', 'markdown_asset_id': 'synthetic-md', 'parse_id': 'synthetic-parse',
              'pdf_pages': [1], 'line_start': 1, 'line_end': 4, 'line_ranges': [[1, 4]],
              'printed_pages': [1], 'bbox': None, 'locator_quality': 'synthetic'}
    return {'parse_id': 'synthetic-parse', 'source': source,
            'blocks': [{'block_id': 'block-1', 'source': source, 'text_raw':
                        'SYNTHETIC 食品添加剂碳酸钠；碳酸钠(以Na₂CO₃计，以干基计)/% ≥ 99.2\n'
                        '铁(以Fe计，以干基计)/% ≤ 0.003 5\n'
                        'GB/T SYNTHETIC；白色；生产；清洗；责任人\n'
                        '采用湿基时条件待明确；部分；通用；2026-01-01；类别NS；方法名称；定义正文'}],
            'chunks': [{'chunk_id': 'chunk-1', 'block_ids': ['block-1']}]}


def field(bundle, raw, value=None, reason=None):
    return {'raw': raw, 'value': raw if value is None else value, 'normalization_reason': reason,
            'evidence': [{'block_id': 'block-1', 'quote': raw, 'source': deepcopy(bundle['blocks'][0]['source'])}]}


@pytest.fixture
def candidate(bundle):
    return {'schema_version': 'knowledge-1', 'logical_key': 'synthetic:carbonate', 'record_type': 'requirement',
            'standard_type': 'product', 'title': 'SYNTHETIC 碳酸钠下限', 'executable': False,
            'source_chunk_ids': ['chunk-1'], 'references': [], 'relations': [],
            'condition': {'ast': {'op': 'all', 'args': [
                {'op': 'eq', 'field': 'product_identity', 'value': '食品添加剂碳酸钠'},
                {'op': 'eq', 'field': 'measurement_basis', 'value': '干基'}]},
                'raw': '食品添加剂碳酸钠；以干基计', 'unresolved': [],
                'required_facts': ['product_identity', 'measurement_basis']},
            'fields': {'subject': field(bundle, '食品添加剂碳酸钠'),
                       'project': field(bundle, '碳酸钠(以Na₂CO₃计，以干基计)'),
                       'requirement_type': field(bundle, '≥', 'lower_limit'),
                       'comparator': field(bundle, '≥'), 'value': field(bundle, '99.2'),
                       'unit': field(bundle, '%'), 'basis': field(bundle, '以干基计'),
                       'expressed_as': field(bundle, '以Na₂CO₃计', 'Na₂CO₃')}}


def codes(result, key='issues'):
    return {item['code'] for item in result[key]}


def verified(value, kind='string', state='verified'):
    return {'type': kind, 'raw_value': value, 'normalized_value': value,
            'source_ref': 'SYNTHETIC_FACT', 'role': 'sample_fact', 'verification_state': state}


def eq(field='product_identity', value='食品添加剂碳酸钠'):
    return {'op': 'eq', 'field': field, 'value': value}


def test_four_standard_templates_are_separate():
    assert set(STANDARD_TEMPLATES) == {'general', 'product', 'practice', 'method'}
    assert 'responsible_party' in STANDARD_TEMPLATES['practice']['fields']
    assert 'formulas' in STANDARD_TEMPLATES['method']['fields']
    assert RECORD_FIELDS['standard_scope_catalog'] == ('subject', 'scope_text')


def test_numeric_candidate_preserves_expressed_as_and_basis(candidate, bundle):
    result = validate_candidate(candidate, bundle)
    assert result['passed'], result
    assert not result['publication_blockers']
    assert candidate['fields']['project']['raw'].endswith('以干基计)')
    assert candidate['executable'] is False


def test_numeric_spacing_needs_reason(candidate, bundle):
    candidate['fields']['project'] = field(bundle, '铁(以Fe计，以干基计)')
    candidate['fields']['value'] = field(bundle, '0.003 5', '0.0035', '删除原文数值内部排版空格，保留精确十进制值')
    candidate['fields']['expressed_as'] = field(bundle, '以Fe计', 'Fe')
    candidate['fields']['requirement_type'] = field(bundle, '≤', 'upper_limit')
    candidate['fields']['comparator'] = field(bundle, '≤')
    assert validate_candidate(candidate, bundle)['passed']
    candidate['fields']['value']['normalization_reason'] = None
    assert 'NORMALIZATION_REASON' in codes(validate_candidate(candidate, bundle))


@pytest.mark.parametrize('change,expected', [
    ('missing_evidence', 'FIELD_EVIDENCE_MISSING'), ('fake_quote', 'FIELD_QUOTE_MISMATCH'),
    ('fake_page', 'FIELD_SOURCE_MISMATCH'), ('fake_line', 'FIELD_SOURCE_MISMATCH'),
    ('fake_asset', 'FIELD_SOURCE_MISMATCH'), ('fake_block', 'FIELD_BLOCK_MISSING'),
    ('float', 'VALUE_TYPE'), ('changed_value', 'VALUE_CHANGED'),
    ('changed_unit', 'UNIT_CHANGED'), ('executable', 'CANDIDATE_EXECUTABLE'),
    ('approved', 'CANDIDATE_REVIEW_CLAIM'), ('source_chunk', 'SOURCE_CHUNK_MISSING'),
])
def test_rejects_corrupted_candidates(candidate, bundle, change, expected):
    value = candidate['fields']['value']
    if change == 'missing_evidence': value['evidence'] = []
    elif change == 'fake_quote': value['evidence'][0]['quote'] = '不存在的值99.2'
    elif change == 'fake_page': value['evidence'][0]['source']['pdf_pages'] = [99]
    elif change == 'fake_line': value['evidence'][0]['source']['line_start'] = 3
    elif change == 'fake_asset': value['evidence'][0]['source']['asset_id'] = 'foreign'
    elif change == 'fake_block': value['evidence'][0]['block_id'] = 'foreign'
    elif change == 'float': value['value'] = 99.2
    elif change == 'changed_value': value.update(value='99.3', normalization_reason='任意改值仍然不允许')
    elif change == 'changed_unit': candidate['fields']['unit'].update(value='mg/kg', normalization_reason='不支持的换单位')
    elif change == 'executable': candidate['executable'] = True
    elif change == 'approved': candidate['approved_by'] = 'invented-expert'
    elif change == 'source_chunk': candidate['source_chunk_ids'] = ['foreign']
    result = validate_candidate(candidate, bundle)
    assert not result['passed']
    assert expected in codes(result), result


def test_missing_unit_retains_candidate_with_publication_gap(candidate, bundle):
    candidate['fields'].pop('unit')
    result = validate_candidate(candidate, bundle)
    assert result['passed']
    assert 'UNIT_MISSING' in codes(result, 'publication_blockers')


def test_qualitative_requirement_does_not_invent_unit(candidate, bundle):
    candidate['fields']['project'] = field(bundle, '白色')
    candidate['fields']['requirement_type'] = field(bundle, '白色', 'qualitative')
    candidate['fields']['value'] = field(bundle, '白色')
    candidate['fields']['comparator'] = {'raw': '', 'value': None, 'evidence': [], 'normalization_reason': None}
    candidate['fields']['unit'] = {'raw': '', 'value': None, 'evidence': [], 'normalization_reason': None}
    result = validate_candidate(candidate, bundle)
    assert result['passed'], result
    assert 'UNIT_MISSING' not in codes(result, 'publication_blockers')
    assert not result['publication_blockers']


def test_unresolved_condition_is_candidate_only(candidate, bundle):
    candidate['condition']['unresolved'] = [{'raw': '采用湿基时条件待明确', 'reason': '无法可靠解析',
                                           'evidence': field(bundle, '采用湿基时条件待明确')['evidence']}]
    result = validate_candidate(candidate, bundle)
    assert result['passed']
    assert 'CONDITION_UNRESOLVED' in codes(result, 'publication_blockers')


def test_scope_catalog_records_namespace_regime_date_gaps(candidate, bundle):
    candidate.update(record_type='standard_scope_catalog', fields={
        'subject': field(bundle, '食品添加剂碳酸钠'), 'scope_text': field(bundle, '食品添加剂碳酸钠'),
        'coverage_state': field(bundle, '部分', 'partial', '目录覆盖状态仅为部分')})
    result = validate_candidate(candidate, bundle)
    assert result['passed'], result
    gaps = {item.get('field') for item in result['publication_blockers'] if item['code'] == 'SCOPE_CATALOG_GAP'}
    assert gaps == {'namespaces', 'regime', 'date_conditions'}
    assert 'SCOPE_COVERAGE_PARTIAL' in codes(result, 'publication_blockers')


def test_scope_date_without_role_is_not_today(candidate, bundle):
    candidate.update(record_type='standard_scope_catalog', fields={
        'subject': field(bundle, '食品添加剂碳酸钠'), 'scope_text': field(bundle, '食品添加剂碳酸钠'),
        'date_conditions': field(bundle, '2026-01-01', {'date': '2026-01-01'})})
    assert 'DATE_ROLE_MISSING' in codes(validate_candidate(candidate, bundle), 'publication_blockers')


def test_external_reference_is_not_automatically_resolved(candidate, bundle):
    candidate['references'] = [{'target': 'GB/T SYNTHETIC', 'kind': 'external_standard',
                                'version_policy': 'undated', 'evidence': field(bundle, 'GB/T SYNTHETIC')['evidence']}]
    result = validate_candidate(candidate, bundle)
    assert result['passed']
    assert 'REFERENCE_UNRESOLVED' in codes(result, 'publication_blockers')


def test_same_project_conflicts_are_not_overwritten(candidate, bundle):
    bundle['blocks'][0]['text_raw'] += '；SYNTHETIC 同项目另一条款 ≥99.3'
    other = deepcopy(candidate); other['logical_key'] = 'synthetic:carbonate:conflict'
    other['fields']['value'] = field(bundle, '99.3')
    result = validate_candidates([candidate, other], bundle)
    assert result['passed'], result
    assert len(result['candidates']) == 2
    assert result['counts']['conflicts'] == 1
    assert all('FIELD_CONFLICT' in codes(item, 'publication_blockers') for item in result['candidates'])


@pytest.mark.parametrize('ast', [
    {'op': 'eval', 'value': '__import__("os").system("anything")'},
    {'op': 'eq', 'field': 'arbitrary.sql.path', 'value': 'x'},
    {'op': 'eq', 'field': 'product_identity', 'value': 'x', 'code': 'print(1)'},
    {'op': 'all', 'args': []}, {'op': ['eval']}, {'op': 'eq', 'field': [], 'value': 'x'},
    {'op': 'lt', 'field': 'business_date', 'value_type': [], 'value': 'x'},
])
def test_restricted_ast_rejects_arbitrary_input(ast):
    assert validate_condition(ast)
    assert evaluate_condition(ast, {})['status'] == 'unknown'


def test_missing_or_unverified_fact_is_unknown():
    assert evaluate_condition(eq(), {})['status'] == 'unknown'
    assert evaluate_condition(eq(), {'product_identity': '食品添加剂碳酸钠'})['status'] == 'unknown'
    assert evaluate_condition(eq(), {'product_identity': verified('食品添加剂碳酸钠', state='candidate')})['status'] == 'unknown'


@pytest.mark.parametrize('operator,states,expected', [
    ('all', ['yes', 'missing'], 'unknown'), ('all', ['no', 'missing'], 'not_satisfied'),
    ('any', ['yes', 'missing'], 'satisfied'), ('any', ['no', 'missing'], 'unknown'),
])
def test_three_valued_all_any(operator, states, expected):
    ast = {'op': operator, 'args': [eq('product_identity', 'yes'), eq('process', 'yes')]}
    facts = {name: verified(value) for name, value in zip(('product_identity', 'process'), states) if value != 'missing'}
    assert evaluate_condition(ast, facts)['status'] == expected


def test_not_unknown_remains_unknown():
    assert evaluate_condition({'op': 'not', 'arg': eq()}, {})['status'] == 'unknown'


def test_conflicting_facts_never_pick_one():
    fact = [verified('食品添加剂碳酸钠'), verified('其他产品')]
    result = evaluate_condition(eq(), {'product_identity': fact})
    assert result['status'] == 'conflicting'
    assert result['required_facts'] == ['product_identity']
    assert evaluate_condition(eq(), {'product_identity': verified('x', state='conflicting')})['status'] == 'conflicting'


@pytest.mark.parametrize('op,value,bound,expected', [
    ('gt', '2.1', '2', 'satisfied'), ('ge', '2', '2', 'satisfied'),
    ('lt', '2', '2', 'not_satisfied'), ('le', '2', '2', 'satisfied'),
])
def test_exact_decimal_comparisons(op, value, bound, expected):
    ast = {'op': op, 'field': 'component', 'value_type': 'number', 'value': bound}
    assert evaluate_condition(ast, {'component': verified(value, 'number')})['status'] == expected


def test_range_and_set_membership():
    ast = {'op': 'range', 'field': 'business_date', 'value_type': 'date', 'lower': '2020-01-01',
           'upper': '2020-02-01', 'lower_inclusive': True, 'upper_inclusive': False}
    assert evaluate_condition(ast, {'business_date': verified('2020-02-01', 'date')})['status'] == 'not_satisfied'
    choice = {'op': 'in', 'field': 'process', 'value_type': 'string', 'value': ['熟制', '发酵']}
    assert evaluate_condition(choice, {'process': verified('发酵')})['status'] == 'satisfied'


def test_exists_needs_verified_provenance():
    ast = {'op': 'exists', 'field': 'process'}
    assert evaluate_condition(ast, {'process': {'normalized_value': '熟制'}})['status'] == 'unknown'
    assert evaluate_condition(ast, {'process': verified('熟制')})['status'] == 'satisfied'


def test_wrong_fact_type_is_unknown():
    ast = {'op': 'eq', 'field': 'component', 'value_type': 'number', 'value': '2'}
    assert evaluate_condition(ast, {'component': verified('2', 'string')})['status'] == 'unknown'


def test_nonfinite_and_incomplete_ranges_rejected():
    assert validate_condition({'op': 'lt', 'field': 'component', 'value_type': 'number', 'value': 'NaN'})
    assert validate_condition({'op': 'range', 'field': 'component', 'lower': '2', 'upper': '1',
                               'lower_inclusive': True, 'upper_inclusive': False})


def test_required_fact_ledger_matches_ast(candidate, bundle):
    candidate['condition']['required_facts'] = []
    assert 'CONDITION_FACTS_MISMATCH' in codes(validate_candidate(candidate, bundle))


def test_formula_type_is_candidate_without_executor(candidate, bundle):
    candidate['fields']['requirement_type'] = field(bundle, '99.2', 'formula')
    assert 'EXECUTOR_NOT_BOUND' in codes(validate_candidate(candidate, bundle), 'publication_blockers')


def test_normalization_reason_cannot_invent_subject_or_basis(candidate, bundle):
    candidate['fields']['subject'].update(value='苹果', normalization_reason='随意改名')
    candidate['fields']['basis'].update(value='湿基', normalization_reason='随意改基准')
    result = validate_candidate(candidate, bundle)
    assert 'FIELD_VALUE_UNSUPPORTED' in codes(result)
    assert not result['passed']


def test_limit_type_must_match_evidenced_direction(candidate, bundle):
    candidate['fields']['requirement_type']['value'] = 'upper_limit'
    candidate['fields']['comparator'] = field(bundle, '≤')
    assert 'REQUIREMENT_DIRECTION_CHANGED' in codes(validate_candidate(candidate, bundle))


def test_free_text_condition_must_still_be_source_bound(candidate, bundle):
    candidate['condition']['raw'] = '凭空加入新条件'
    assert 'CONDITION_RAW_UNSUPPORTED' in codes(validate_candidate(candidate, bundle))


def test_measurement_basis_does_not_imply_component_basis(candidate, bundle):
    candidate['fields']['project'] = field(bundle, '以干基计')
    candidate['fields'].pop('expressed_as')
    result = validate_candidate(candidate, bundle)
    assert 'EXPRESSED_AS_MISSING' not in codes(result, 'publication_blockers')


def test_source_supported_optional_absence_is_not_unknown(candidate, bundle):
    candidate['fields']['precision'] = {
        'state': 'not_specified', 'raw': '', 'value': None,
        'normalization_reason': '所选完整章节未单列精密度条款，保留来源供人工复核',
        'evidence': field(bundle, '方法名称')['evidence']}
    result = validate_candidate(candidate, bundle)
    assert result['passed'], result
    assert not result['publication_blockers']
    candidate['fields']['precision']['evidence'] = []
    assert 'FIELD_EVIDENCE_MISSING' in codes(validate_candidate(candidate, bundle))


def test_required_field_cannot_be_marked_not_specified(candidate, bundle):
    candidate['fields']['value'].update(state='not_specified', raw='', value=None, normalization_reason='没有值')
    assert 'FIELD_NOT_SPECIFIED' in codes(validate_candidate(candidate, bundle))


@pytest.mark.parametrize('record_type,standard_type,fields', [
    ('definition', 'product', {'subject': '食品添加剂碳酸钠', 'definition': '定义正文'}),
    ('method', 'method', {'method_name': '方法名称'}),
    ('practice', 'practice', {'stage': '生产', 'measure': '清洗', 'responsible_party': '责任人'}),
])
def test_other_record_types_keep_independent_evidence(candidate, bundle, record_type, standard_type, fields):
    candidate.update(record_type=record_type, standard_type=standard_type,
                     fields={name: field(bundle, raw) for name, raw in fields.items()})
    result = validate_candidate(candidate, bundle)
    assert result['passed'], result
    candidate['fields'][next(iter(fields))]['evidence'] = []
    assert 'FIELD_EVIDENCE_MISSING' in codes(validate_candidate(candidate, bundle))


@pytest.mark.parametrize('maximum,conflicts', [('100.0', 0), ('99.1', 1)])
def test_opposite_bounds_conflict_only_when_interval_is_empty(candidate, bundle, maximum, conflicts):
    bundle['blocks'][0]['text_raw'] += '；SYNTHETIC 同项目上限 ≤ ' + maximum
    upper = deepcopy(candidate); upper['logical_key'] = 'synthetic:carbonate:upper'
    upper['fields']['requirement_type'] = field(bundle, '≤', 'upper_limit')
    upper['fields']['comparator'] = field(bundle, '≤')
    upper['fields']['value'] = field(bundle, maximum)
    result = validate_candidates([candidate, upper], bundle)
    assert result['passed'], result
    assert result['counts']['conflicts'] == conflicts


def test_cell_provenance_cannot_be_fabricated(candidate, bundle):
    origin = {'fragment_key': 'synthetic-table', 'row': 2, 'column': 2}
    bundle['chunks'][0]['table'] = {'cells': [{'column': 2, 'source_cell': origin, 'inherited': False, 'value': '99.2'}]}
    candidate['fields']['value']['evidence'][0]['cell'] = {'column': 2, 'source_cell': origin, 'inherited': False}
    assert validate_candidate(candidate, bundle)['passed']
    candidate['fields']['value']['evidence'][0]['cell']['source_cell'] = {**origin, 'row': 999}
    assert 'FIELD_CELL_MISMATCH' in codes(validate_candidate(candidate, bundle))


def test_inherited_cell_keeps_physical_source_row(candidate, bundle):
    origin = {'fragment_key': 'synthetic-table', 'row': 2, 'column': 2}
    bundle['chunks'][0]['table'] = {'cells': [{'column': 2, 'source_cell': origin, 'inherited': False, 'value': '99.2'}]}
    bundle['chunks'].append({'chunk_id': 'second-row', 'block_ids': ['second-block'],
                             'table': {'cells': [{'column': 2, 'source_cell': origin, 'inherited': True, 'value': '99.2'}]}})
    candidate['fields']['value']['evidence'][0]['cell'] = {'column': 2, 'source_cell': origin, 'inherited': True}
    assert validate_candidate(candidate, bundle)['passed']


def test_reason_cannot_invent_qualitative_requirement(candidate, bundle):
    candidate['fields']['requirement_type'] = field(bundle, '白色', 'qualitative')
    candidate['fields']['value'] = field(bundle, '白色', '999', '任意改定性值')
    assert 'FIELD_VALUE_UNSUPPORTED' in codes(validate_candidate(candidate, bundle))


@pytest.fixture
def lean_method(candidate, bundle):
    """SYNTHETIC selected precision fact and formula dependency, not executable semantics."""
    source = deepcopy(bundle['blocks'][0]['source'])
    original = [
        {'block_id': 'method-root', 'key': 'A.7', 'title': 'A.7 铁的测定', 'text_raw': '### A.7 铁的测定'},
        {'block_id': 'calculation', 'key': 'A.7.5', 'title': 'A.7.5 结果计算',
         'text_raw': '#### A.7.5 结果计算\n\n$$w_4=w_0 \\tag{A.7}$$\n'
                     '- $w_0$——由 A.4 所测得合成试验的质量分数，仅用于测试。\n'
                     '取平行测定结果的算术平均值，绝对差值不大于 0.000 5%。'},
        {'block_id': 'dependency', 'key': 'A.4', 'title': 'A.4 灼烧失量的测定', 'text_raw': '### A.4 灼烧失量的测定'},
        {'block_id': 'other-method', 'key': 'A.8.5', 'title': 'A.8.5 结果计算', 'text_raw': '#### A.8.5 结果计算'},
    ]
    bundle['blocks'].extend({**item, 'source': deepcopy(source)} for item in original)
    bundle['chunks'].extend({'chunk_id': item['block_id'] + '-chunk', 'block_ids': [item['block_id']]} for item in original)

    def method_field(raw, value, block_id):
        return {'raw': raw, 'value': value, 'normalization_reason': 'SYNTHETIC 从明确句式提取或保留原文标题引用',
                'evidence': [{'block_id': block_id, 'quote': raw, 'source': deepcopy(source)}]}

    reference = {'block_id': 'calculation', 'anchor': 'A.7.5', 'title': 'A.7.5 结果计算'}
    candidate.update(record_type='method', standard_type='method', title='SYNTHETIC 铁的方法',
                     source_chunk_ids=['method-root-chunk', 'calculation-chunk'], references=[], relations=[],
                     condition={'ast': None, 'raw': '', 'unresolved': [], 'required_facts': []},
                     semantic_coverage={'state': 'partial', 'structured_roles': ['parallel_absolute_difference', 'calculation_dependency'],
                        'source_only': ['experimental_conditions', 'formula_variables', 'lod_loq_conditions'],
                        'unsupported_checks': ['full_method_applicability', 'formula_recalculation'],
                        'note': '仅验证少数明确事实，其他语义仍需回到原文'},
                     fields={'method_name': method_field('A.7 铁的测定', '铁的测定', 'method-root'),
                             'method_anchor': method_field('A.7 铁的测定', 'A.7', 'method-root'),
                             'formulas': method_field('A.7.5 结果计算', [deepcopy(reference)], 'calculation'),
                             'sections': method_field('A.7.5 结果计算', [deepcopy(reference)], 'calculation'),
                             'method_checks': method_field('A.7.5 结果计算', [{**reference, 'role': 'parallel_result_aggregation',
                                 'label': '平行结果汇总'}], 'calculation'),
                             'parameters': method_field('绝对差值不大于 0.000 5%', [{'role': 'parallel_absolute_difference',
                                 'label': '平行测定绝对差值上限', 'value': '0.0005', 'unit': '%', 'comparator': '≤',
                                 'raw': '绝对差值不大于 0.000 5%', 'source_block_id': 'calculation'}], 'calculation'),
                             'calculation_dependencies': method_field('$w_0$——由 A.4 所测得合成试验的质量分数，仅用于测试。', [{
                                 'source_method': 'A.7', 'target_method': 'A.4', 'symbol': 'w_0', 'formula_ids': ['A.7'],
                                 'raw': '$w_0$——由 A.4 所测得合成试验的质量分数，仅用于测试。', 'source_block_id': 'calculation'}], 'calculation')})
    return candidate


def test_lean_method_references_and_selected_facts_remain_partial(lean_method, bundle):
    result = validate_candidate(lean_method, bundle)
    assert result['passed'], result
    assert 'METHOD_SEMANTICS_PARTIAL' in codes(result, 'publication_blockers')


@pytest.mark.parametrize('change,expected', [
    ('fake_title', 'METHOD_REFERENCE_CHANGED'), ('fake_anchor', 'METHOD_REFERENCE_CHANGED'),
    ('foreign_block', 'METHOD_BLOCK_UNBOUND'), ('missing_item_evidence', 'METHOD_ITEM_EVIDENCE'),
    ('copy_body', 'METHOD_REFERENCE_SCHEMA'), ('copy_raw', 'METHOD_REFERENCE_RAW'),
    ('other_method', 'METHOD_REFERENCE_OUTSIDE'), ('invented_role', 'METHOD_CHECK_ROLE'),
    ('wrong_shared_section', 'METHOD_REFERENCE_OUTSIDE'),
    ('missing_method_anchor', 'METHOD_ANCHOR_UNBOUND'), ('different_root_evidence', 'METHOD_ANCHOR_UNBOUND'),
    ('missing_coverage', 'METHOD_COVERAGE_SCHEMA'), ('complete_coverage', 'METHOD_COVERAGE_SCHEMA'),
    ('omit_limits', 'METHOD_COVERAGE_OVERCLAIM'),
])
def test_lean_method_cannot_change_source_identity_or_claim_complete(lean_method, bundle, change, expected):
    references = lean_method['fields']['formulas']
    reference = references['value'][0]
    if change == 'fake_title': reference['title'] = '伪造标题'
    elif change == 'fake_anchor': reference['anchor'] = 'A.99'
    elif change == 'foreign_block': reference['block_id'] = 'foreign'
    elif change == 'missing_item_evidence':
        references['evidence'] = deepcopy(lean_method['fields']['method_name']['evidence'])
    elif change == 'copy_body': reference['text'] = '不应重新保存的正文'
    elif change == 'copy_raw':
        references['raw'] = bundle['blocks'][2]['text_raw']
        references['evidence'][0]['quote'] = references['raw']
    elif change == 'other_method':
        other = bundle['blocks'][-1]
        references['value'] = [{'block_id': other['block_id'], 'anchor': other['key'], 'title': other['title']}]
        references['raw'] = other['title']
        references['evidence'] = [{'block_id': other['block_id'], 'quote': other['title'], 'source': deepcopy(other['source'])}]
        lean_method['source_chunk_ids'].append('other-method-chunk')
    elif change == 'invented_role': lean_method['fields']['method_checks']['value'][0]['role'] = 'product_limit'
    elif change == 'wrong_shared_section': lean_method['fields']['general_requirements'] = deepcopy(references)
    elif change == 'missing_method_anchor': lean_method['fields'].pop('method_anchor')
    elif change == 'different_root_evidence':
        lean_method['fields']['method_anchor']['evidence'] = deepcopy(lean_method['fields']['formulas']['evidence'])
    elif change == 'missing_coverage': lean_method.pop('semantic_coverage')
    elif change == 'complete_coverage': lean_method['semantic_coverage']['state'] = 'complete'
    elif change == 'omit_limits': lean_method['semantic_coverage']['unsupported_checks'] = []
    result = validate_candidate(lean_method, bundle)
    assert not result['passed'], result
    assert expected in codes(result), result


@pytest.mark.parametrize('key,value,expected', [
    ('value', '0.0035', 'METHOD_PARAMETER_CHANGED'), ('value', 0.0005, 'METHOD_PARAMETER_SCHEMA'),
    ('unit', 'mg/kg', 'METHOD_PARAMETER_UNSUPPORTED'), ('comparator', '≥', 'METHOD_PARAMETER_UNSUPPORTED'),
    ('role', 'product_upper_limit', 'METHOD_PARAMETER_SCHEMA'),
    ('source_block_id', 'method-root', 'METHOD_PARAMETER_UNSUPPORTED'),
])
def test_selected_precision_parameter_cannot_change_meaning(lean_method, bundle, key, value, expected):
    lean_method['fields']['parameters']['value'][0][key] = value
    assert expected in codes(validate_candidate(lean_method, bundle))


@pytest.mark.parametrize('key,value', [
    ('source_method', 'A.4'), ('target_method', 'A.8.5'), ('symbol', 'w_4'),
    ('formula_ids', ['A.8']), ('source_block_id', 'method-root'),
])
def test_calculation_dependency_cannot_swap_method_variable_or_formula(lean_method, bundle, key, value):
    lean_method['fields']['calculation_dependencies']['value'][0][key] = value
    assert 'METHOD_DEPENDENCY_UNSUPPORTED' in codes(validate_candidate(lean_method, bundle))


def test_legacy_method_payload_remains_compatible(lean_method, bundle):
    lean_method.pop('semantic_coverage')
    lean_method['fields'] = {'method_name': lean_method['fields']['method_name'], 'formulas': {
        'raw': 'A.7.5 结果计算', 'value': [{'anchor': 'A.7.5', 'title': 'A.7.5 结果计算', 'text': '旧代次全文'}],
        'normalization_reason': '旧代次章节分组', 'evidence': lean_method['fields']['formulas']['evidence']}}
    result = validate_candidate(lean_method, bundle)
    assert result['passed'], result
    assert 'METHOD_SEMANTICS_PARTIAL' not in codes(result, 'publication_blockers')
