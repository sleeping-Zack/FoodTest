"""Read a specified extraction snapshot by exact facts, without applicability decisions."""
from copy import deepcopy
import re

from .conditions import condition_facts, validate_condition


_METHOD_FIELDS = {'method_name', 'method_anchor', 'scope', 'method_checks', 'submethods', 'parameters',
                  'calculation_dependencies', 'formulas', 'precision', 'general_requirements', 'lod_loq'}


def _name(value):
    return re.sub(r'\s+', '', value).replace('(', '（').replace(')', '）') if isinstance(value, str) else ''


def _project_names(project):
    value = _name(project.get('value')); names = {value}
    # Only a symbol in the project name is an analyte alias. "以 Pb 计" is a basis.
    match = re.fullmatch(r'([^（）]+)（([A-Z][a-z]?)）', value)
    if match:
        quotes = ''.join(_name(item.get('quote')) for item in project.get('evidence', []))
        names.update(alias for alias in match.groups() if alias in quotes)
    return names


def _anchor(candidate):
    return candidate['logical_key'].split(':', 2)[2]


def _source_blocks(value, blocks):
    """Resolve original blocks once; a mismatching locator never supplies a source."""
    resolved = {}; gaps = []

    def gap(code, block_id):
        item = {'code': code, 'block_id': block_id,
                'message': '来源引用缺失或与原块不一致，须核对后使用。'}
        if item not in gaps:
            gaps.append(item)

    def visit(item):
        if isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            if 'block_id' in item:
                block = blocks.get(item['block_id'])
                if block is None:
                    gap('SOURCE_BLOCK_MISSING', item['block_id'])
                elif (('anchor' in item and item['anchor'] != block['key']) or
                      ('title' in item and item['title'] != block['title']) or
                      ('quote' in item and (not item['quote'] or item['quote'] not in block['text_raw'])) or
                      ('source' in item and item['source'] != block['source'])):
                    gap('SOURCE_REFERENCE_MISMATCH', item['block_id'])
                else:
                    resolved[block['block_id']] = {'block_id': block['block_id'], 'anchor': block['key'],
                        'title': block['title'], 'text_raw': block['text_raw'], 'source': deepcopy(block['source'])}
            for child in item.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)
    return list(resolved.values()), gaps


def query_facts(report, project=None, basis=None, record_type='requirement', anchor=None):
    """Select all matching candidates in this snapshot; query values are not sample facts."""
    if any(value is not None and not _name(value) for value in (project, basis, anchor)):
        raise ValueError('An exact project, basis or anchor filter cannot be empty.')
    blocks = {block['block_id']: block for block in report['blocks']}
    by_key = {item['candidate']['logical_key']: item for item in report['records']}
    records = []
    catalogs = [item['candidate']['fields'].get('coverage_state', {}).get('value')
                for item in report['records'] if item['candidate']['record_type'] == 'standard_scope_catalog']
    for item in report['records']:
        candidate = item['candidate']; fields = candidate['fields']
        if candidate['record_type'] != record_type:
            continue
        if project is not None and _name(project) not in _project_names(fields.get('project', {})):
            continue
        if basis is not None and _name(basis) != _name(fields.get('basis', {}).get('value')):
            continue
        if anchor is not None and anchor.strip() != _anchor(candidate):
            continue
        selected = {key: deepcopy(value) for key, value in fields.items()
                    if record_type != 'method' or key in _METHOD_FIELDS}
        condition = deepcopy(candidate.get('condition', {}))
        issues = validate_condition(condition.get('ast'))
        required = set(condition.get('required_facts', []))
        if not issues:
            required.update(condition_facts(condition.get('ast')))
        source_blocks, gaps = _source_blocks([selected, condition, candidate.get('references', [])], blocks)
        if condition.get('unresolved') or issues:
            gaps.append({'code': 'CONDITION_UNRESOLVED', 'message': '适用条件未完全解析，不能认定适用。'})
        if required:
            gaps.append({'code': 'SAMPLE_FACTS_UNVERIFIED', 'message': '查询条件仅用于筛选候选；实际样品事实仍待核实。'})
        related = []
        for relation in candidate.get('relations', []):
            target = by_key.get(relation['target_key'])
            if target is None:
                gaps.append({'code': 'RELATED_RECORD_MISSING', 'message': '引用目标不在本抽取快照内。',
                             'target_key': relation['target_key']})
            else:
                other = target['candidate']
                related.append({'record_id': target['record_id'], 'anchor': _anchor(other),
                    'title': other['title'], 'record_type': other['record_type'], 'relation': relation['kind']})
        validation = item.get('validation', {})
        gaps.extend(deepcopy(validation.get('publication_blockers', [])))
        record = {'record_id': item['record_id'], 'logical_key': candidate['logical_key'],
            'anchor': _anchor(candidate), 'title': candidate['title'], 'record_type': candidate['record_type'],
            'revision': item['revision'], 'review_state': item.get('review_state', 'review_pending'),
            'fields': selected, 'condition': condition, 'applicability': 'unknown',
            'required_facts': sorted(required), 'unresolved_conditions': condition.get('unresolved', []),
            'references': deepcopy(candidate.get('references', [])),
            'source_blocks': source_blocks, 'related_records': related, 'gaps': gaps, 'executable': False}
        if 'semantic_coverage' in candidate:
            record['semantic_coverage'] = deepcopy(candidate['semantic_coverage'])
        records.append(record)
    notes = ['完整读取指定抽取快照后精确筛选；结果为原文支持的候选，不构成业务适用或合格结论。',
             '审核状态来自冻结导出快照；最新人工审核状态需查询审核队列。']
    if not records:
        notes.append('本快照未命中，不代表标准没有此项要求，也不代表无需检验。')
    if project is not None and basis is None and len({_name(r['fields'].get('basis', {}).get('value')) for r in records}) > 1:
        notes.append('存在不同计量基准的候选；未指定基准时全部保留，不能合并或任选一条。')
    return {'status': 'matched_candidates' if records else 'no_match_in_snapshot',
        'extraction_id': report['extraction_id'],
        'document_metadata': deepcopy(report.get('document_metadata', {})),
        'query': {'project': project, 'basis': basis, 'record_type': record_type, 'anchor': anchor},
        'scope': {'snapshot_records_read': len(report['records']),
            'snapshot_read': 'all_registered_records_in_selected_snapshot',
            'scope_catalog_coverage': catalogs[0] if len(catalogs) == 1 else 'unknown',
            'business_applicability': 'unknown', 'snapshot_review_state': 'frozen_export'},
        'records': records, 'notes': notes, 'executable': False}


def export_query_examples(report):
    """Five read-only sample questions, kept outside the registered report fingerprint."""
    examples = [
        ('iron_requirement', '铁的要求', '铁的限值、计量基准与检验方法是什么？', {'project': '铁'}),
        ('total_alkali_basis', '总碱量与干湿基', '总碱量有哪些要求？未确定干湿基时能否只选一条？', {'project': '总碱量'}),
        ('heavy_metals_expression', '重金属与以何物计', '重金属的表达基准是什么？能否当成单独铅的限值？', {'project': '重金属'}),
        ('chloride_submethods', '氯化物的子方法', '氯化物测定有哪些子方法？仲裁方法和依据在哪里？',
            {'record_type': 'method', 'anchor': 'A.6'}),
        ('iron_calculation_sources', '铁的方法与计算依赖', '铁的测定引用哪些依据？干基计算依赖哪个测定结果？',
            {'record_type': 'method', 'anchor': 'A.7'}),
    ]
    return [{'id': identifier, 'label': label, 'question': question, 'query': query,
             'result': query_facts(report, **query)} for identifier, label, question, query in examples]
