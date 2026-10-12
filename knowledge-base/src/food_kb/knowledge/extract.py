"""Deterministic, source-bound product tables and method sections, without model calls."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re
from typing import Protocol

from ..common import KBError


PROCESSOR_VERSION = 'knowledge-facts-4'


class ExtractionProvider(Protocol):
    name: str
    version: str

    def extract(self, bundle: dict, document_metadata: dict) -> list[dict]: ...


def evidence(block, quote=None, cell=None):
    quote = block['text_raw'] if quote is None else quote
    if not quote or quote not in block['text_raw']:
        raise KBError('EXTRACTION_QUOTE_MISMATCH', 'A field quote is not literal source text.')
    value = {'block_id': block['block_id'], 'quote': quote, 'source': deepcopy(block['source'])}
    if cell is not None:
        value['cell'] = deepcopy(cell)
    return value


def field(value, sources=(), *, raw=None, reason=None):
    anchors = [evidence(b) if 'block_id' in b and 'quote' not in b else b for b in sources]
    return {'raw': '\n\n'.join(e['quote'] for e in anchors) if raw is None else raw,
            'value': value, 'evidence': anchors, 'normalization_reason': reason}


def not_specified(sources):
    return field(None, sources, raw='', reason='所选完整条款未单列此信息，需结合原文确认适用性') | {'state': 'not_specified'}


def _body(block):
    return re.sub(r'^#{1,6}[^\n]*\n?', '', block['text_raw']).strip()


def _references(sources):
    result = []
    for b in sources:
        for match in re.finditer(r'GB\s*(?:/T\s*)?\d+(?:\.\d+)*(?:\s*[—–-]\s*\d{4})?', b['text_raw']):
            raw = match[0]; target = re.sub(r'\s+', ' ', raw).strip()
            value = {'target': target, 'kind': 'reference_candidate',
                     'version_policy': 'dated' if re.search(r'[—–-]\s*\d{4}$', raw) else 'undated',
                     'state': 'unresolved', 'evidence': [evidence(b, raw)]}
            if not any(x['target'] == target for x in result):
                result.append(value)
    return result


def _source_ref(block):
    return {'block_id': block['block_id'], 'anchor': block['key'], 'title': block['title']}


def _reference_field(sources):
    anchors = [evidence(b, b['title']) for b in sources]
    if not anchors:
        return None
    return field([_source_ref(b) for b in sources], anchors,
                 reason='这里只保存原文引用；完整正文、公式和变量从KB06源块展开，未重复改写')


def _project_name(raw):
    # Remove only the supported table annotations, retaining chemical symbols.
    name = re.sub(r'[（(]以\s*[^（）()]+?\s*计[）)]', '', raw)
    return re.sub(r'\s*[,，/]?\s*(?:\$w\$/%|[（(]mg/kg[）)]|mg/kg|%)?\s*[≤≥<>]?\s*$', '', name).strip(' ，,／/')


def _method_facts(members, method_roots):
    checks = []; parameters = []; dependencies = []
    for b in members:
        text = b['text_raw']
        roles = []
        if re.search(r'(?:试验|试样|参比).*溶液的制备', b['title']):
            roles.append(('sample_preparation', '试样或比对溶液制备条件'))
        if '空白' in text:
            roles.append(('blank_test', '空白试验及其引用'))
        if '分析步骤' in b['title'] or re.search(r'^A\.[\d.]+\s+(?:测定|工作曲线的绘制)$', b['title']):
            roles.append(('measurement_conditions', '操作与测定条件'))
        if re.search(r'平行测定结果的算术平均值', text):
            roles.append(('parallel_result_aggregation', '平行结果取算术平均值'))
        if re.search(r'GB\s*(?:/T\s*)?\d+', text):
            roles.append(('required_reference', '本条款涉及的外部标准引用'))
        checks.extend(_source_ref(b) | {'role': role, 'label': label} for role, label in roles)
        if re.search(r'平行测定|重复性条件', text):
            for match in re.finditer(r'绝对差值不大于\s*(\d+(?:[ .]\d+)*)\s*%', text):
                parameters.append({'role': 'parallel_absolute_difference', 'label': '平行测定绝对差值上限',
                    'value': re.sub(r'\s+', '', match[1]), 'unit': '%', 'comparator': '≤',
                    'raw': match[0], 'source_block_id': b['block_id']})
        for line in text.splitlines():
            match = re.search(r'\$([^$]+)\$[—–-]+由\s*(A\.\d+(?:\.\d+)*)\s*所测得', line)
            if match:
                owners = [root['key'] for root in method_roots if b['key'] == root['key'] or b['key'].startswith(root['key'] + '.')]
                dependencies.append({'source_method': max(owners, key=len), 'target_method': match[2],
                    'symbol': match[1], 'formula_ids': re.findall(r'\\tag\{(A\.\d+)\}', text),
                    'raw': line.removeprefix('- ').strip(), 'source_block_id': b['block_id']})
    return checks, parameters, dependencies


class StructuredProvider:
    name = 'local-structured'
    version = PROCESSOR_VERSION

    def extract(self, bundle, document_metadata):
        blocks = {b['block_id']: b for b in bundle['blocks']}
        keys = {b['key']: b for b in bundle['blocks']}
        chunks = {c['anchor']: c for c in bundle['chunks']}
        version_id = str(document_metadata['version_id']); records = []
        scope = next((b for b in bundle['blocks'] if re.search(r'本标准适用于', b['text_raw'])), None)
        if scope is None:
            return []
        scope_text = _body(scope)
        match = re.fullmatch(r'本标准适用于([^，,；;。.!！？?\n]+?)[。.]?', scope_text)
        if match and re.search(r'不适用|除外|但|以及|及其|或者|或|如果|若|当|用于|经|采用|制得|制成|以.+为', match[1]):
            match = None
        if not match:
            # Do not convert complex applicability prose to an unconditional predicate.
            subject = None; scope_unresolved = [{'raw': scope_text, 'reason': '复杂范围须语义标注', 'evidence': [evidence(scope, scope_text)]}]
        else:
            subject = match[1].rstrip('。'); scope_unresolved = []

        def condition(basis=None):
            args = [{'op': 'eq', 'field': 'product_identity', 'value_type': 'string', 'value': subject}] if subject else []
            if basis:
                args.append({'op': 'eq', 'field': 'measurement_basis', 'value_type': 'string', 'value': basis})
            return {'ast': {'op': 'all', 'args': args} if args else None, 'raw': scope_text + ('；' + basis if basis else ''),
                    'unresolved': deepcopy(scope_unresolved), 'required_facts': ['product_identity'] + (['measurement_basis'] if basis else [])}

        def candidate(kind, anchor, title, fields, sources, *, standard_type='product', basis=None):
            sources = sources + [blocks[e['block_id']] for value in fields.values() for e in value['evidence']]
            source_ids = [chunks[b['key']]['chunk_id'] for b in sources if b['key'] in chunks]
            applicability = condition(basis)
            applicability['evidence'] = [evidence(scope, scope_text)] + (fields['basis']['evidence'] if basis else [])
            value = {'schema_version': 'knowledge-1', 'logical_key': f'{version_id}:{kind}:{anchor}',
                     'record_type': kind, 'standard_type': standard_type, 'title': title,
                     'fields': fields, 'condition': applicability, 'references': _references(sources),
                     'relations': [], 'source_chunk_ids': list(dict.fromkeys(source_ids)), 'executable': False}
            records.append(value)
            return value

        subject_field = field(subject, [evidence(scope, scope_text)], raw=scope_text,
                              reason='从“本标准适用于”句保留明确对象；分类及实际样品事实待核')
        scope_record = candidate('standard_scope_catalog', scope['key'], '标准适用范围',
            {'subject': subject_field, 'scope_text': field(scope_text, [evidence(scope, scope_text)], raw=scope_text),
             'regime': field(None), 'date_conditions': field(None), 'namespaces': field(None),
             'coverage_state': field('partial', [evidence(scope, scope_text)], raw=scope_text,
                 reason='本次仅整理所选原文，不代表食品类别全部适用标准已收齐')}, [scope])

        for b in bundle['blocks']:
            if b['kind'] != 'section' or not re.search(r'分子式|相对分子质量', b['title']) or not _body(b):
                continue
            raw = _body(b)
            candidate('definition', b['key'], b['title'],
                      {'subject': deepcopy(subject_field), 'definition': field(raw, [evidence(b, raw)], raw=raw)}, [b, scope])

        table_rows = [c for c in bundle['chunks'] if c['kind'] == 'table_row']
        for chunk in table_rows:
            if not chunk['retrieval_enabled'] or chunk['quality']['status'] != 'complete_for_retrieval':
                continue
            table = chunk['table']; cells = table['cells']; headers = table['headers']
            if len(cells) != 3 or headers[0] != '项目' or headers[1] not in {'要求', '指标'} or headers[2] != '检验方法':
                continue
            row_block = blocks[chunk['block_ids'][0]]

            def cell_evidence(cell, quote=None):
                origin = cell['source_cell']; source_row = origin['row'] - len(chunks[table['fragment_key']]['table']['header_rows']) if 'fragment_key' in table else origin['row'] - 1
                source_key = f"{origin.get('fragment_key', chunk['parent_block_id'])}:row:{source_row}"
                source_block = keys.get(source_key, row_block)
                return evidence(source_block, cell['value'] if quote is None else quote,
                                {'column': cell['column'], 'source_cell': origin, 'inherited': cell['inherited']})

            project, limit, method = [c['value'] for c in cells]
            context = []
            current = blocks.get(row_block['parent_id'])
            while current:
                if current['kind'] == 'section':
                    context.append(current)
                current = blocks.get(current['parent_id'])
            comparator = re.search(r'[≤≥<>]', project)
            basis_match = re.search(r'以(?:干|湿)基计', project)
            basis = basis_match[0] if basis_match else None
            expressed = re.search(r'以\s*([^（）]+?)\s*计', project)
            expressed_as = expressed[0] if expressed and '基计' not in expressed[0] else None
            unit = 'mg/kg' if 'mg/kg' in project else '%' if '%' in project else None
            if comparator:
                normalized = re.sub(r'\s+', '', limit)
                try:
                    Decimal(normalized)
                except InvalidOperation:
                    continue
                requirement_type = 'lower_limit' if comparator[0] in {'≥', '>'} else 'upper_limit'
            else:
                normalized = limit; requirement_type = 'qualitative'
            project_name = _project_name(project)
            fields = {'subject': deepcopy(subject_field),
                      'project': field(project_name, [cell_evidence(cells[0])], raw=project,
                          reason='项目名称去除已分别保留的计量限定、单位与比较符；化学符号和原标签保留' if project_name != project else None),
                      'requirement_type': field(requirement_type,
                          [cell_evidence(cells[0], comparator[0])] if comparator else [cell_evidence(cells[1])],
                          raw=comparator[0] if comparator else limit, reason='按明确比较符或感官文字区分要求类型'),
                      'comparator': field(comparator[0], [cell_evidence(cells[0], comparator[0])], raw=comparator[0]) if comparator else field(None),
                      'value': field(normalized, [cell_evidence(cells[1])], raw=limit,
                                     reason='仅去掉数字中的排版空格，保留精确十进制字符串' if normalized != limit else None),
                      'unit': field(unit, [cell_evidence(cells[0], unit)], raw=unit) if unit else not_specified([cell_evidence(cells[0])]),
                      'basis': field(basis, [cell_evidence(cells[0], basis)], raw=basis) if basis else not_specified([cell_evidence(cells[0])]),
                      'expressed_as': field(expressed_as, [cell_evidence(cells[0], expressed_as)], raw=expressed_as) if expressed_as else not_specified([cell_evidence(cells[0])]),
                      'method_reference': field(method, [cell_evidence(cells[2])], raw=method),
                      'table_context': _reference_field(context) or not_specified([evidence(row_block, row_block['title'])])}
            record = candidate('requirement', chunk['anchor'], project_name, fields, [row_block, scope], basis=basis)
            record['references'].extend(_references([{'block_id': row_block['block_id'], 'text_raw': method, 'source': row_block['source']}]))
            internal = re.search(r'A\.\d+(?:\.\d+)*', method)
            if internal:
                record['relations'].append({'target_key': f'{version_id}:method:{internal[0]}', 'kind': 'tested_by',
                                            'evidence': [cell_evidence(cells[2])]})
            record['relations'].append({'target_key': scope_record['logical_key'], 'kind': 'applies_to',
                                        'evidence': [evidence(scope, scope_text)]})
            for note in table.get('footnotes', []):
                record['condition']['unresolved'].append({'raw': note, 'reason': '表注条件需显式标注后方可执行',
                    'evidence': [evidence(b) for b in bundle['blocks'] if b['kind'] == 'footnote' and b['text_raw'] == note]})

        def subtree(root):
            result = []
            for b in bundle['blocks']:
                current = b
                while current:
                    if current['block_id'] == root['block_id']:
                        result.append(b); break
                    current = blocks.get(current['parent_id'])
            return result

        method_roots = [b for b in bundle['blocks'] if
                        (re.fullmatch(r'A\.\d+', b['key']) and ('的测定' in b['title'] or '鉴别试验' in b['title'])) or
                        (re.fullmatch(r'A\.\d+\.\d+', b['key']) and re.search(r'法(?:[（(].*[）)])?$', b['title'])
                         and not b['title'].endswith('方法'))]
        for root in method_roots:
            members = subtree(root)
            name = re.sub(r'^A\.\d+(?:\.\d+)*\s*', '', root['title'])
            fields = {'method_name': field(name, [evidence(root, root['title'])], raw=root['title'], reason='去掉条款编号，原方法名称不改写'),
                      'method_anchor': field(root['key'], [evidence(root, root['title'])], reason='从方法标题保留条款编号'),
                      'scope': deepcopy(subject_field)}
            categories = {'principle': '方法提要', 'reagents': '试剂|材料|溶液', 'equipment': '仪器|设备',
                          'steps': '分析步骤|鉴别方法', 'formulas': '结果计算|配制', 'submethods': '法',
                          'general_requirements': '一般规定|安全提示'}
            for field_name, pattern in categories.items():
                selected = [b for b in members if any(re.search(pattern, p['title']) for p in members
                            if b['key'] == p['key'] or b['key'].startswith(p['key'] + '.'))]
                if field_name == 'formulas':
                    selected = [b for b in members if '$$' in b['text_raw']]
                if field_name == 'general_requirements':
                    selected = [b for b in bundle['blocks'] if re.fullmatch(r'A\.\d+', b['key']) and re.search(pattern, b['title'])]
                if field_name == 'submethods':
                    selected = [b for b in members if b != root and (any(b['block_id'] == m['block_id'] for m in method_roots)
                                or (not b['title'].endswith('方法') and re.search(r'法(?:[（(].*[）)])?$|以(?:干|湿)基计.*测定$', b['title'])))]
                fields[field_name] = _reference_field(selected) or not_specified([evidence(b, b['title']) for b in members])
                if field_name == 'submethods' and selected:
                    fields[field_name]['normalization_reason'] += '；这里关联方法或操作分支，是否为独立替代方法须按父级条款核对'
            precision = [b for b in members if re.search(r'平行测定|重复性|绝对差值', b['text_raw'])]
            title_sources = [evidence(b, b['title']) for b in members]
            fields['precision'] = _reference_field(precision) or not_specified(title_sources)
            checks, params, dependencies = _method_facts(members, method_roots)
            fields['method_checks'] = field(checks, [evidence(blocks[c['block_id']], blocks[c['block_id']]['title']) for c in checks] or [evidence(root, root['title'])],
                reason='列出需要回到原文核对的操作、空白、结果及引用条款；条件仍须结合实际实验记录')
            fields['parameters'] = field(params, [evidence(blocks[p['source_block_id']], p['raw']) for p in params] or [evidence(root, root['title'])],
                reason='仅将明确平行测定绝对差值上限规范为有角色的参数；其余数字保留在原文中，未作语义抽取')
            fields['calculation_dependencies'] = field(dependencies, [evidence(blocks[d['source_block_id']], d['raw']) for d in dependencies] or [evidence(root, root['title'])],
                reason='从公式变量说明中的明确条款引用建立计算依赖；未实现公式复算或干湿基换算')
            fields['lod_loq'] = not_specified(title_sources)
            fields['sections'] = _reference_field(members)
            record = candidate('method', root['key'], root['title'], fields, members + [scope], standard_type='method')
            record['semantic_coverage'] = {'state': 'partial',
                'structured_roles': ['method_identity', 'source_references'] + (['parallel_absolute_difference'] if params else []) + (['calculation_dependency'] if dependencies else []),
                'source_only': ['experimental_conditions', 'formula_variables', 'lod_loq_conditions'],
                'unsupported_checks': ['full_method_applicability', 'formula_recalculation'],
                'note': '方法原文完整关联；本轮仅整理明确身份、条款引用、平行差值和计算依赖，不能据此确认实际实验合规。'}
            for dependency in dependencies:
                record['relations'].append({'target_key': f"{version_id}:method:{dependency['target_method']}",
                    'kind': 'calculation_depends_on', 'evidence': [evidence(blocks[dependency['source_block_id']], dependency['raw'])]})

        requirements = [r for r in records if r['record_type'] == 'requirement']
        projects = [r['fields']['project'] for r in requirements]
        scope_record['fields']['projects'] = field([p['value'] for p in projects],
            [e for p in projects for e in p['evidence']], raw='\n'.join(p['raw'] for p in projects),
            reason='复用规范项目名称及原表出处；不推断危害分类、任务必检项目或全部适用标准') if projects else field(None)
        scope_record['fields']['hazards'] = field(None)
        scope_record['source_chunk_ids'] = list(dict.fromkeys(scope_record['source_chunk_ids'] +
            [chunk_id for r in requirements for chunk_id in r['source_chunk_ids']]))
        return records
