"""Real GB 1886.1 source regressions, without database or external model calls."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

import pytest

from food_kb.chunking.manual import load_manual
from food_kb.chunking.structural import build_bundle
from food_kb.common import identity
from food_kb.knowledge.extract import StructuredProvider
from food_kb.knowledge.validate import validate_candidates


MANUAL_DIRECTORY = Path(__file__).resolve().parents[2] / 'docs/knowledge-base/manual/GB1886.1-2015'
pytestmark = pytest.mark.skipif(not (MANUAL_DIRECTORY / 'source-map.json').is_file(),
                               reason='Private manual source bundle is not distributed with public code.')
METADATA = {'version_id': str(identity('test-knowledge-real-version', 'GB 1886.1-2015'))}


@pytest.fixture(scope='module')
def extracted():
    manual = load_manual(MANUAL_DIRECTORY)
    bundle = build_bundle(manual, identity('test-knowledge-real-parse', manual['mapping']['markdown_sha256']))
    before = deepcopy(bundle)
    candidates = StructuredProvider().extract(bundle, METADATA)
    assert bundle == before
    return bundle, candidates


def field(record, key):
    return record['fields'][key]


def method(candidates, anchor):
    return next(record for record in candidates if record['logical_key'].endswith(':method:' + anchor))


def test_actual_sample_has_nine_rows_and_complete_method_roots(extracted):
    bundle, candidates = extracted
    counts = Counter(record['record_type'] for record in candidates)
    assert counts['requirement'] == 9
    assert counts['definition'] == 2
    assert counts['standard_scope_catalog'] == 1
    expected = {'A.' + str(number) for number in range(3, 10)} | {'A.6.1', 'A.6.2'}
    roots = {record['logical_key'].split(':method:')[1] for record in candidates if record['record_type'] == 'method'}
    assert roots == expected
    assert all(record['executable'] is False for record in candidates)
    validation = validate_candidates(candidates, bundle)
    assert validation['passed'], validation['issues']
    assert validation['counts']['machine_passed'] == len(candidates)
    keys = {record['logical_key'] for record in candidates}
    assert all(relation['target_key'] in keys for record in candidates for relation in record['relations'])


def test_iron_decimal_preserves_literal_spacing_and_dry_basis(extracted):
    _, candidates = extracted
    iron = next(record for record in candidates if record['record_type'] == 'requirement' and 'Fe' in record['title'])
    assert field(iron, 'value')['raw'] == '0.003 5'
    assert field(iron, 'value')['value'] == '0.0035'
    assert field(iron, 'value')['normalization_reason']
    assert field(iron, 'comparator')['value'] == '≤'
    assert field(iron, 'unit')['value'] == '%'
    assert field(iron, 'basis')['value'] == '以干基计'
    quote = field(iron, 'value')['evidence'][0]
    assert quote['quote'] == '0.003 5'
    assert quote['source']['pdf_pages'] == [3]
    assert quote['source']['line_ranges'] == [[92, 92]]
    assert quote['source']['bbox'] is None
    assert any(relation['kind'] == 'tested_by' and relation['target_key'].endswith(':method:A.7')
               for relation in iron['relations'])
    assert any(argument['field'] == 'measurement_basis' and argument['value'] == '以干基计'
               for argument in iron['condition']['ast']['args'])


def test_total_alkali_dry_and_wet_requirements_remain_distinct(extracted):
    _, candidates = extracted
    rows = [record for record in candidates if record['record_type'] == 'requirement' and '总碱量' in record['title']]
    assert len(rows) == 2
    assert {(field(record, 'basis')['value'], field(record, 'value')['value']) for record in rows} == {
        ('以干基计', '99.2'), ('以湿基计', '97.9')}
    assert all(field(record, 'comparator')['value'] == '≥' for record in rows)
    assert all(field(record, 'unit')['value'] == '%' for record in rows)
    assert rows[0]['condition']['ast'] != rows[1]['condition']['ast']
    assert len({record['logical_key'] for record in rows}) == 2


def test_heavy_metals_preserve_pb_expression_not_a_lead_analyte_substitution(extracted):
    _, candidates = extracted
    record = next(record for record in candidates if record['record_type'] == 'requirement' and '重金属' in record['title'])
    assert '重金属' in field(record, 'project')['value']
    assert field(record, 'expressed_as')['value'] == '以 Pb 计'
    assert field(record, 'expressed_as')['evidence'][0]['quote'] == '以 Pb 计'
    assert field(record, 'value')['value'] == '10.0'
    assert field(record, 'unit')['value'] == 'mg/kg'
    assert field(record, 'method_reference')['value'] == '附录 A 中 A.8'
    assert any(relation['target_key'].endswith(':method:A.8') for relation in record['relations'])


def test_inherited_sensory_method_points_to_original_merged_cell(extracted):
    _, candidates = extracted
    first = next(record for record in candidates if record['record_type'] == 'requirement' and record['title'] == '色泽')
    second = next(record for record in candidates if record['record_type'] == 'requirement' and record['title'] == '状态')
    assert field(first, 'value')['value'] == '白色'
    assert field(second, 'value')['value'] == '结晶粉末'
    first_evidence = field(first, 'method_reference')['evidence'][0]
    second_evidence = field(second, 'method_reference')['evidence'][0]
    assert second_evidence['cell']['inherited'] is True
    assert first_evidence['cell']['source_cell'] == second_evidence['cell']['source_cell']
    assert first_evidence['block_id'] == second_evidence['block_id']
    assert second_evidence['source']['line_ranges'] == [[76, 76]]
    assert field(first, 'method_reference')['value'] == field(second, 'method_reference')['value']


def test_crosspage_formula_and_all_eight_equations_keep_variables(extracted):
    bundle, candidates = extracted
    blocks = {b['block_id']: b for b in bundle['blocks']}
    total = method(candidates, 'A.5')
    formula = next(item for item in field(total, 'formulas')['value'] if item['anchor'] == 'A.5.5')
    quote = next(item for item in field(total, 'formulas')['evidence'] if item['block_id'] == formula['block_id'])
    assert quote['source']['pdf_pages'] == [5, 6]
    assert formula['title'] == quote['quote']
    text = blocks[formula['block_id']]['text_raw']
    assert '式中' in text and '$c$' in text and '$V_0$' in text and '$m_3$' in text
    assert 'text' not in formula  # Full source stays in the KB06 block, not in each derived field.
    tags = set()
    for record in candidates:
        if record['record_type'] != 'method':
            continue
        for value in field(record, 'formulas')['value'] or []:
            text = blocks[value['block_id']]['text_raw']
            tags.update(re.findall(r'\\tag\{(A\.\d+)\}', text))
            assert text.count('$$') % 2 == 0
            assert '式中' in text
    assert tags == {'A.' + str(number) for number in range(1, 9)}
    # Whole method recovery includes every original clause, even unclassified headings.
    original_keys = {block['key'] for block in bundle['blocks'] if block['key'] == 'A.6' or block['key'].startswith('A.6.')}
    saved_keys = {item['anchor'] for item in field(method(candidates, 'A.6'), 'sections')['value']}
    assert saved_keys == original_keys


def test_external_references_and_standard_catalog_remain_explicitly_pending(extracted):
    bundle, candidates = extracted
    scope = next(record for record in candidates if record['record_type'] == 'standard_scope_catalog')
    assert field(scope, 'coverage_state')['value'] == 'partial'
    assert field(scope, 'namespaces')['value'] is None
    assert field(scope, 'regime')['value'] is None
    assert field(scope, 'date_conditions')['value'] is None
    projects = field(scope, 'projects')
    assert len(projects['value']) == len(projects['evidence']) == 9
    assert all(value in '\n'.join(e['quote'] for e in projects['evidence']) for value in projects['value'])
    assert field(scope, 'hazards')['value'] is None
    arsenic = next(record for record in candidates if record['record_type'] == 'requirement' and '砷' in record['title'])
    assert any(reference['target'] == 'GB 5009.76' and reference['state'] == 'unresolved'
               for reference in arsenic['references'])
    result = validate_candidates(candidates, bundle)
    item = next(item for item in result['candidates'] if item['logical_key'] == arsenic['logical_key'])
    assert any(blocker['code'] == 'REFERENCE_UNRESOLVED' for blocker in item['publication_blockers'])
    assert item['passed']  # Structural correctness does not grant business approval.


@pytest.mark.parametrize('defect', ['incomplete_context', 'not_decimal'])
def test_bad_table_row_remains_outside_executable_candidates(extracted, defect):
    bundle, _ = extracted
    changed = deepcopy(bundle)
    iron = next(chunk for chunk in changed['chunks'] if chunk['anchor'] == 'table-2:row:4')
    if defect == 'incomplete_context':
        iron['quality']['status'] = 'context_incomplete'
    else:
        iron['table']['cells'][1]['value'] = '0.003 ?'
    candidates = StructuredProvider().extract(changed, METADATA)
    assert not any(record['record_type'] == 'requirement' and 'Fe' in record['title'] for record in candidates)
    assert all(record['executable'] is False for record in candidates)


def test_complex_scope_is_unresolved_instead_of_an_unconditional_match(extracted):
    bundle, _ = extracted
    changed = deepcopy(bundle)
    scope = next(block for block in changed['blocks'] if block['key'] == '1')
    scope['text_raw'] = '## 1 范围\n\n本标准适用于食品甲；但不适用于食品乙。'
    candidates = StructuredProvider().extract(changed, METADATA)
    scope_record = next(record for record in candidates if record['record_type'] == 'standard_scope_catalog')
    assert scope_record['condition']['ast'] is None
    assert scope_record['condition']['unresolved']
    assert field(scope_record, 'subject')['value'] is None
    assert all(record['executable'] is False for record in candidates)


def test_repeat_extraction_keeps_exact_candidates_and_input(extracted):
    bundle, candidates = extracted
    before = deepcopy(bundle)
    assert StructuredProvider().extract(bundle, METADATA) == candidates
    assert bundle == before


def test_project_names_separate_basis_expression_unit_and_operator(extracted):
    _, candidates = extracted
    names = [field(r, 'project')['value'] for r in candidates if r['record_type'] == 'requirement']
    assert names == ['色泽', '状态', '总碱量', '总碱量', '氯化物', '铁（Fe）', '重金属', '砷（As）', '水不溶物']
    iron = next(r for r in candidates if r['record_type'] == 'requirement' and r['title'] == '铁（Fe）')
    assert '以干基计' in field(iron, 'project')['raw'] and '%' in field(iron, 'project')['raw']
    assert field(iron, 'project')['normalization_reason']


def test_methods_have_role_parameters_and_explicit_source_only_limits(extracted):
    _, candidates = extracted
    iron = method(candidates, 'A.7')
    params = field(iron, 'parameters')['value']
    assert len(params) == 1
    assert params[0]['role'] == 'parallel_absolute_difference'
    assert (params[0]['value'], params[0]['unit'], params[0]['comparator']) == ('0.0005', '%', '≤')
    assert '0.000 5%' in params[0]['raw']
    assert iron['semantic_coverage']['state'] == 'partial'
    assert 'formula_recalculation' in iron['semantic_coverage']['unsupported_checks']
    assert all(p['role'] == 'parallel_absolute_difference' for r in candidates if r['record_type'] == 'method'
               for p in field(r, 'parameters')['value'])
    for r in candidates:
        if r['record_type'] == 'method':
            assert all('text' not in ref for ref in field(r, 'sections')['value'])


def test_formula_dependencies_keep_w0_reference_and_distinct_submethods(extracted):
    bundle, candidates = extracted
    blocks = {b['block_id']: b for b in bundle['blocks']}
    for anchor, tag in [('A.7', 'A.7'), ('A.9', 'A.8'), ('A.6.1', 'A.5'), ('A.6.2', 'A.6')]:
        record = method(candidates, anchor)
        deps = field(record, 'calculation_dependencies')['value']
        assert len(deps) == 1
        dep = deps[0]
        assert (dep['source_method'], dep['target_method'], dep['symbol'], dep['formula_ids']) == (anchor, 'A.4', 'w_0', [tag])
        assert dep['raw'] in blocks[dep['source_block_id']]['text_raw']
        assert any(r['kind'] == 'calculation_depends_on' and r['target_key'].endswith(':method:A.4') for r in record['relations'])
    assert {r['anchor'] for r in field(method(candidates, 'A.5'), 'submethods')['value']} >= {'A.5.4.1', 'A.5.4.2'}
    assert {r['anchor'] for r in field(method(candidates, 'A.6'), 'submethods')['value']} >= {'A.6.1', 'A.6.2'}
    assert {r['anchor'] for r in field(method(candidates, 'A.9'), 'submethods')['value']} >= {'A.9.4.1.1', 'A.9.4.1.2'}
    assert field(method(candidates, 'A.3'), 'submethods')['value'] is None
    assert 'A.3.2' in {r['anchor'] for r in field(method(candidates, 'A.3'), 'sections')['value']}
