"""Exact sample facts, source expansion and honest gaps against the real transcription."""
from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from food_kb.chunking.manual import load_manual
from food_kb.chunking.structural import build_bundle
from food_kb.common import identity
from food_kb.knowledge.extract import StructuredProvider
from food_kb.knowledge.facts import export_query_examples, query_facts


MANUAL = Path(__file__).resolve().parents[2] / 'docs/knowledge-base/manual/GB1886.1-2015'
pytestmark = pytest.mark.skipif(not (MANUAL / 'source-map.json').is_file(),
                               reason='Private manual source bundle is not distributed with public code.')


@pytest.fixture(scope='module')
def report():
    manual = load_manual(MANUAL)
    bundle = build_bundle(manual, identity('test-facts-parse', manual['mapping']['markdown_sha256']))
    candidates = StructuredProvider().extract(bundle, {'version_id': str(identity('test-facts-version', 'GB1886.1'))})
    return {'extraction_id': str(identity('test-facts-extraction', 'GB1886.1')),
            'blocks': bundle['blocks'], 'records': [
                {'record_id': str(identity('test-facts-record', candidate['logical_key'])),
                 'revision': 'r1-test-facts', 'review_state': 'review_pending',
                 'candidate': candidate, 'validation': {'publication_blockers': []}} for candidate in candidates]}


def test_iron_name_and_symbol_queries_keep_exact_decimal_and_literal_source(report):
    chinese = query_facts(report, project='铁')
    symbol = query_facts(report, project='Fe')
    assert chinese == symbol | {'query': chinese['query']}
    assert len(chinese['records']) == 1
    iron = chinese['records'][0]
    assert iron['fields']['project']['value'] == '铁（Fe）'
    assert iron['fields']['value']['value'] == '0.0035'
    assert iron['fields']['value']['raw'] == '0.003 5'
    assert iron['fields']['basis']['value'] == '以干基计'
    assert iron['fields']['method_reference']['value'] == '附录 A 中 A.7'
    assert any(source['source']['pdf_pages'] == [3] and '0.003 5' in source['text_raw'] for source in iron['source_blocks'])
    assert any(target['anchor'] == 'A.7' and target['relation'] == 'tested_by' for target in iron['related_records'])


def test_unspecified_basis_keeps_both_total_alkali_candidates_and_explicit_gaps(report):
    result = query_facts(report, project='总碱量')
    assert {(record['fields']['basis']['value'], record['fields']['value']['value']) for record in result['records']} == {
        ('以干基计', '99.2'), ('以湿基计', '97.9')}
    assert any('不能合并' in note for note in result['notes'])
    assert all(record['applicability'] == 'unknown' and not record['executable'] for record in result['records'])
    assert all('measurement_basis' in record['required_facts'] for record in result['records'])
    dry = query_facts(report, project='总碱量', basis='以干基计')['records']
    assert len(dry) == 1 and dry[0]['fields']['value']['value'] == '99.2'
    assert dry[0]['applicability'] == 'unknown'  # Filtering does not verify a real sample's basis.


@pytest.mark.parametrize('name', ['Pb', '铅', '重', '铁含量', 'Fe2', 'fe'])
def test_aliases_never_use_substring_or_expression_basis_as_analyte(report, name):
    result = query_facts(report, project=name)
    assert result['status'] == 'no_match_in_snapshot'
    assert result['records'] == []
    assert result['scope']['snapshot_records_read'] == 21
    assert result['scope']['scope_catalog_coverage'] == 'partial'
    assert any('不代表标准没有' in note for note in result['notes'])


def test_heavy_metals_keeps_expression_basis_separate(report):
    record = query_facts(report, project='重金属')['records'][0]
    assert record['fields']['project']['value'] == '重金属'
    assert record['fields']['expressed_as']['value'] == '以 Pb 计'
    assert record['fields']['value']['value'] == '10.0'
    assert record['fields']['unit']['value'] == 'mg/kg'
    assert any(target['anchor'] == 'A.8' for target in record['related_records'])


def test_chloride_method_query_expands_submethods_from_original_blocks(report):
    record = query_facts(report, record_type='method', anchor='A.6')['records'][0]
    submethods = record['fields']['submethods']['value']
    assert {item['anchor'] for item in submethods} == {'A.6.1', 'A.6.2'}
    assert all({'block_id', 'anchor', 'title'} <= item.keys() and 'text' not in item for item in submethods)
    assert any(source['anchor'] == 'A.6.2' and '仲裁法' in source['title'] for source in record['source_blocks'])
    assert 'sections' not in record['fields']
    assert record['semantic_coverage']['state'] == 'partial'


def test_iron_method_formula_and_dependency_are_literal_sources_not_recalculation(report):
    record = query_facts(report, record_type='method', anchor='A.7')['records'][0]
    assert any(item['target_method'] == 'A.4' and item['symbol'] == 'w_0'
               for item in record['fields']['calculation_dependencies']['value'])
    formula = next(item for item in record['source_blocks'] if item['anchor'] == 'A.7.5')
    assert '\\tag{A.7}' in formula['text_raw']
    assert all(text in formula['text_raw'] for text in ('w_0', 'A.4', '质量分数'))
    assert formula['source']['pdf_pages'] == [9]
    assert 'formula_recalculation' in record['semantic_coverage']['unsupported_checks']
    assert any(reference['target'] == 'GB/T 3049—2006' and reference['state'] == 'unresolved'
               for reference in record['references'])
    assert all(record['applicability'] == 'unknown' for record in query_facts(report, record_type='method', anchor='A.7')['records'])


def test_method_anchor_filter_is_exact_and_never_supplies_another_method(report):
    assert query_facts(report, record_type='method', anchor='A.70')['records'] == []
    found = query_facts(report, record_type='method', anchor='A.7')['records']
    assert len(found) == 1 and found[0]['anchor'] == 'A.7'
    assert found[0]['fields']['method_anchor']['value'] == 'A.7'
    assert not any(source['anchor'].startswith('A.6') for source in found[0]['source_blocks'])


@pytest.mark.parametrize('filter_', [{'project': ' '}, {'basis': ''}, {'anchor': '\t'}])
def test_empty_filter_is_not_a_wildcard_for_unknown_fields(report, filter_):
    with pytest.raises(ValueError):
        query_facts(report, **filter_)


@pytest.mark.parametrize('defect', ['missing_block', 'wrong_anchor', 'wrong_quote', 'wrong_page'])
def test_incorrect_source_reference_is_a_gap_and_cannot_supply_wrong_source(report, defect):
    changed = deepcopy(report)
    record = next(item for item in changed['records'] if item['candidate']['logical_key'].endswith(':method:A.7'))
    field = record['candidate']['fields']['formulas']
    target = field['value'][0]
    evidence = field['evidence'][0]
    if defect == 'missing_block':
        target['block_id'] = evidence['block_id'] = 'missing-source-block'
    elif defect == 'wrong_anchor':
        target['anchor'] = 'A.6.2'
    elif defect == 'wrong_quote':
        evidence['quote'] = '这句话不在原文中。'
    else:
        evidence['source']['pdf_pages'] = [999]
    found = query_facts(changed, record_type='method', anchor='A.7')['records'][0]
    assert any(item['code'] in {'SOURCE_BLOCK_MISSING', 'SOURCE_REFERENCE_MISMATCH'} for item in found['gaps'])
    assert not any(source['source']['pdf_pages'] == [999] for source in found['source_blocks'])
    assert found['applicability'] == 'unknown' and found['executable'] is False


def test_unknown_conditions_and_unresolved_method_are_never_passed(report):
    changed = deepcopy(report)
    iron = next(item for item in changed['records'] if item['candidate']['fields'].get('project', {}).get('value') == '铁（Fe）')
    iron['candidate']['condition']['ast'] = None
    iron['candidate']['condition']['unresolved'] = [{'raw': '复杂例外条件', 'reason': '待标注'}]
    iron['candidate']['relations'][0]['target_key'] = 'unknown:method:A.99'
    result = query_facts(changed, project='铁')['records'][0]
    assert {gap['code'] for gap in result['gaps']} >= {'CONDITION_UNRESOLVED', 'RELATED_RECORD_MISSING'}
    assert result['applicability'] == 'unknown' and result['executable'] is False


def test_five_examples_read_all_snapshot_records_without_mutation(report):
    before = deepcopy(report)
    examples = export_query_examples(report)
    assert report == before
    assert len(examples) == 5 and len({item['id'] for item in examples}) == 5
    assert [len(item['result']['records']) for item in examples] == [1, 2, 1, 1, 1]
    assert all(item['result']['scope']['business_applicability'] == 'unknown' for item in examples)
    assert all(record['review_state'] == 'review_pending' and not record['executable']
               for item in examples for record in item['result']['records'])


def test_cli_reads_only_the_selected_extraction_and_passes_exact_filters(report, monkeypatch, capsys):
    from food_kb import cli
    from food_kb.knowledge import service

    before = deepcopy(report); connection = object(); config = object(); reads = []
    monkeypatch.setattr(cli.Config, 'load', staticmethod(lambda _: config))
    monkeypatch.setattr(cli, 'connect', lambda _: nullcontext(connection))

    def read(conn, settings, extraction_id):
        assert conn is connection and settings is config
        reads.append(extraction_id)
        return report

    monkeypatch.setattr(service, 'read_extraction', read)
    assert cli.main(['knowledge-facts', report['extraction_id'], '--project', '总碱量', '--basis', '以湿基计']) == 0
    result = json.loads(capsys.readouterr().out)
    assert reads == [report['extraction_id']]
    assert len(result['records']) == 1 and result['records'][0]['fields']['value']['value'] == '97.9'
    assert report == before and result['executable'] is False
