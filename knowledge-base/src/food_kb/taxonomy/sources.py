"""Build candidate classifications from frozen, source-preserving legacy indexes.

No row is expert approved. Prose remains unresolved rather than pretending that
an extracted name proves the ingredients, process, part or task applicability.
"""
import re
import unicodedata
from collections import defaultdict

from ..common import KBError, digest, identity

PROCESSOR_VERSION = 'taxonomy-sources-2'


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def condition(raw, evidence, required_facts=()):
    if not required_facts:
        required_facts = [field for field, marker in (
            ('ingredients', '原料'), ('process', '工艺|工序|加工'), ('food_part', '部位|去除'),
            ('intended_use', '用途|用于'), ('packaging', '包装')) if re.search(marker, raw)]
    return {'ast': None, 'state': 'unresolved', 'raw': raw,
            'required_facts': list(required_facts), 'evidence': evidence}


def source_exclusions(raw, evidence_rows):
    if not re.search(r'不包括|除外|不在.*范围|仅抽|仅限', raw):
        return []
    result = condition(raw, evidence_rows)
    result.update(names=[], scope_label=None)
    return [result]


def evidence(record, pointer, quote, original=None, **locator):
    return {'source_key': record['source_key'], 'index_asset_id': record['index_asset_id'],
            'source_asset_id': original or record['index_asset_id'], 'json_pointer': pointer,
            'legacy_id': str(record['legacy_id']), 'quote': quote,
            'locator': locator or {'location_status': 'source_pointer'},
            'locator_quality': 'page_claim' if locator.get('pdf_page') else 'source_pointer'}


def node(namespace, key, name, evidence_rows, raw='', kind='category', code=None):
    return {'id': identity('taxonomy-node', namespace, key), 'name': name, 'code': code,
            'kind': kind, 'parent_id': None,
            'names': [{'value': name, 'kind': 'canonical', 'evidence': evidence_rows,
                       'verification_state': 'source_claim'}],
            'condition': condition(raw or name, evidence_rows), 'exclusions': source_exclusions(raw or name, evidence_rows),
            'evidence': evidence_rows, 'review_state': 'review_pending',
            'definition': {'raw': raw or name, 'statements': []}}


def edge(namespace, child, parent, kind, evidence_rows, raw=''):
    return {'id': identity('taxonomy-relation', namespace, child, parent, kind, evidence_rows),
            'child_id': child, 'parent_id': parent, 'kind': kind,
            'condition': condition(raw or '关联候选，需核对定义和样品条件', evidence_rows),
            'evidence': evidence_rows, 'review_state': 'review_pending'}


def namespace(name, standard, version, identity_status='unverified'):
    return {'namespace': name, 'standard': standard, 'standard_version_id': version,
            'identity_status': identity_status, 'coverage_state': 'partial',
            'semantic_coverage': 'partial', 'nodes': [], 'relations': [],
            'requirements': [], 'gaps': [], 'version_applicability': 'unknown'}


def _source_names(raw, evidence_rows):
    """Keep exact listed food names; never use arbitrary substring matching."""
    value = unicodedata.normalize('NFKC', raw).strip()
    # Parenthetic qualifiers stay in the definition and remain unresolved.
    base = re.split(r'[（(]', value, maxsplit=1)[0].strip()
    result = []
    if base and base != value:
        result.append({'value': base, 'kind': 'normalization_hint', 'evidence': evidence_rows,
                       'verification_state': 'source_claim'})
    # An exclusion or ingredient/process clause is never a positive list.
    if re.search(r'不包括|除外|不在|原料|工艺|加工而成|制成的', value):
        return result
    for match in re.finditer(r'(?<!不)(?:包括|例如|如)[:：]?([^()（）]+)', value):
        tail = match[1].split('等')[0]
        for term in re.split(r'[、，,和及\s]+', tail):
            if 2 <= len(term) <= 18 and not re.search(r'原料|加工|工艺|生产|食品|制成|不包括|除外', term):
                result.append({'value': term, 'kind': 'explicit_example', 'evidence': evidence_rows,
                               'verification_state': 'source_claim'})
    return result


def build_standard(record, version, identity_status):
    raw = record['raw_payload']
    ns = namespace(raw['namespace'], raw['standard'], version, identity_status)
    original = raw['sha256']
    by_source_id = {}
    for i, row in enumerate(raw['coded_nodes']):
        if not row.get('label', '').strip() or row.get('label_status') != '原文类别':
            ns['gaps'].append({'kind': 'unusable_source_label', 'source_id': row.get('id')})
            continue
        ev = [evidence(record, f'/coded_nodes/{i}', row.get('label', ''), original,
                       pdf_page=row['pdf_page'], source_line=row.get('source_line'), location_status='source_line')]
        item = node(ns['namespace'], row['id'], row['label'], ev, code=row['classification_code'])
        item['names'] += _source_names(row['label'], ev)
        by_source_id[row['id']] = item
        ns['nodes'].append(item)
    if raw['coded_nodes']:
        for row in raw['coded_nodes']:
            item = by_source_id.get(row['id'])
            if not item or not row.get('parent_id'):
                continue
            parent = by_source_id.get(row['parent_id'])
            if not parent:
                ns['gaps'].append({'kind': 'missing_parent', 'source_id': row['id']})
                continue
            item['parent_id'] = parent['id']
            ns['relations'].append(edge(ns['namespace'], item['id'], parent['id'], 'parent', item['evidence']))
        return ns
    groups = {}
    for ti, table in enumerate(raw['tables']):
        group = ''
        for ri, row in enumerate(table['cells']):
            if len(row) < 2 or not any(row):
                continue
            if compact(str(row[0] or '')) in ('食品类别', '食品类别(名称)', '散装即食食品类别', '动物种类', '序号'):
                continue
            if row[0]:
                group = re.sub(r'\s+', '', str(row[0]))
            if not group or group.startswith(('注', '续表')):
                continue
            quote = ' | '.join(str(c or '') for c in row)
            ev = [evidence(record, f'/tables/{ti}/cells/{ri}', quote, original,
                           pdf_page=table['pdf_page'], bbox=table.get('bbox'), row_index=ri,
                           location_status='table_row')]
            if group not in groups:
                groups[group] = node(ns['namespace'], ['group', group], group, ev, quote, 'source_group')
                ns['nodes'].append(groups[group])
            description = str(row[1] or '')
            if ns['namespace'] == 'GB_2763-2026':
                # The first line is a subtype only when followed by further text.
                lines = [x.strip() for x in description.splitlines() if re.search(r'[\u4e00-\u9fff]', x)]
                subtype = lines[0] if len(lines) > 1 and len(compact(lines[0])) <= 18 else group
                labels = [(subtype, description)]
            elif ns['namespace'] in ('GB_2761-2017', 'GB_2762-2025', 'GB_29921-2021'):
                labels = [(line.strip(), line) for line in description.splitlines()
                          if re.search(r'[\u4e00-\u9fff]', line)]
            else:
                labels = [(group + ' / ' + re.sub(r'\s+', '', description), quote)]
            for li, (label, terms) in enumerate(labels):
                item = node(ns['namespace'], [table['id'], ri, li], label, ev, quote)
                item['parent_id'] = groups[group]['id']
                item['names'] += _source_names(terms, ev)
                item['measurement_part'] = str(row[2] or '') if ns['namespace'] == 'GB_2763-2026' and len(row) > 2 else None
                if ns['namespace'] == 'GB_2763-2026':
                    # Here the appendix explicitly lists species, not ingredients.
                    for term in re.split(r'[、，,\s]+', terms):
                        term = term.rstrip('等').strip(':：')
                        if 2 <= len(term) <= 12 and re.fullmatch(r'[\u4e00-\u9fff]+', term):
                            item['names'].append({'value': term, 'kind': 'explicit_example', 'evidence': ev,
                                                  'verification_state': 'source_claim'})
                ns['nodes'].append(item)
                ns['relations'].append(edge(ns['namespace'], item['id'], groups[group]['id'], 'parent', ev))
    return ns


def _page_text(pages, first, last):
    return ''.join(compact('\n'.join(line for line in page['text'].splitlines()
                   if not re.fullmatch(r'\s*\d+\s*', line)))
                   for page in pages[first - 1:last])


def _necessary_condition(item, statements):
    """Three explicitly sourced necessary constraints, never sufficient tests.

    primary_heat_treatment is the verified final heat-treatment category. A
    process fragment alone cannot exclude a product which was sterilized later.
    The percentage field names the milk ingredient, not an arbitrary proportion.
    """
    for statement, ev in statements.values():
        raw = statement['quote']
        text = compact(raw)
        name = compact(item['name'])
        if not text.startswith(name):
            continue
        ast = None
        if text.startswith(name + '指') and '经巴氏杀菌等工序' in text:
            ast = {'op': 'eq', 'field': 'primary_heat_treatment', 'value': '巴氏杀菌'}
        elif (text.startswith(name + '分为') and '超高温灭菌乳' in text and
              '保持灭菌乳' in text and '经灭菌等工序制成' in text):
            ast = {'op': 'eq', 'field': 'primary_heat_treatment', 'value': '灭菌'}
        elif text.startswith(name + '指以不低于'):
            match = re.search(r'不低于([0-9]+(?:\.[0-9]+)?)%的生牛\(羊\)乳或复原乳', text)
            if match:
                ast = {'op': 'ge', 'field': 'milk_ingredient_proportion_percent',
                       'value': match[1], 'value_type': 'number'}
        if ast:
            result = condition(raw, ev, [ast['field']])
            result.update(ast=ast, state='partial', necessary_only=True,
                          unresolved='Other definition clauses, exclusions and version applicability remain unresolved.')
            item['condition'] = result
            return


def _qualified_tables(section_name, path_nodes, tables):
    """Resolve an explicit parent(child) title only inside its source section.

    The parent must be the exact section definition and the child a unique
    descendant already evidenced by its original definition relation. This is
    not a general parenthetic alias rule or permission to union parent tables.
    """
    result = defaultdict(list)
    for table in tables.values():
        match = re.fullmatch(r'([^()]+)\(([^()]+)\)', compact(table['name']))
        if not match or match[1] != compact(section_name) or re.search(r'不包括|除外|仅限|仅抽|原料|工艺|比例', match[2]):
            continue
        candidates = [item for path, item in path_nodes.items() if len(path) > 1
                      and compact(path[0]) == match[1] and compact(item['name']) == match[2]]
        if len(candidates) == 1:
            result[candidates[0]['id']].append(table)
    return result


def build_guide(guide_record, definition_record, page_record, bridge_record, version, identity_status):
    guide, exported = guide_record['raw_payload'], definition_record['raw_payload']
    pages = page_record['raw_payload']['pages']
    if page_record['raw_payload']['sha256'] != guide['sha256'] or exported['source_sha256'] != guide['sha256']:
        raise KBError('TAXONOMY_SOURCE_CONFLICT', 'Definition/cache original fingerprints disagree.')
    ns = namespace('inspection-guide-2026-upper', '国家食品安全抽检实施细则（2026年版 上册）', version, identity_status)
    original = guide['sha256']
    sections = {s['id']: (i, s) for i, s in enumerate(guide['sections'])}
    entries = defaultdict(dict)
    table_by_caption = defaultdict(list)
    for i, table in enumerate(guide['tables']):
        if table['kind'] == 'inspection_items':
            table_by_caption[(table['section_id'], compact(table['caption']))].append((i, table))
    for (sid, caption), tables in table_by_caption.items():
        ti, first = tables[0]
        label = re.sub(r'^表\s*\d+[-－]\d+', '', first['caption'])
        label = re.sub(r'检验项目.*$', '', label).strip()
        ev = [evidence(guide_record, f'/tables/{ti}/caption', first['caption'], original,
                       pdf_page=first['pdf_page'], bbox=first.get('bbox'), location_status='table_caption')]
        item = node(ns['namespace'], ['table', sid, caption], label, ev, first['caption'], 'inspection_table')
        item['table_id'] = first['id']
        item['source_table_ids'] = [t['id'] for _, t in tables]
        item['notes'] = list(dict.fromkeys(n for _, t in tables for n in t.get('notes', [])))
        ns['nodes'].append(item)
        entries[sid][compact(label)] = item
    for di, definition in enumerate(exported['definitions']):
        sid = definition['section_id']
        if sid not in sections:
            raise KBError('TAXONOMY_SOURCE_CONFLICT', 'Definition section has no guide section.')
        si, section = sections[sid]
        if definition['raw_text'] != section['product_types']:
            raise KBError('TAXONOMY_SOURCE_CONFLICT', 'Definition differs from frozen product-type clause.')
        root_ev = [evidence(guide_record, f'/sections/{si}/product_types', section['product_types'], original,
                            pdf_page=definition['pdf_page'], pdf_end_page=definition['pdf_end_page'],
                            location_status='section_span')]
        root = node(ns['namespace'], [sid, 'root'], definition['name'], root_ev, section['scope'] + '\n' + section['product_types'])
        root['section_id'] = sid
        root['definition']['raw'] = definition['raw_text']
        ns['nodes'].append(root)
        path_nodes = {(definition['name'],): root}
        statements = {}
        for pi, statement in enumerate(definition['statements']):
            quote = statement['quote']
            if compact(quote) not in _page_text(pages, statement['pdf_page'], statement['pdf_end_page']):
                raise KBError('TAXONOMY_QUOTE_MISMATCH', 'Definition quote cannot be traced to its declared page span.')
            ev = [evidence(definition_record, f'/definitions/{di}/statements/{pi}/quote', quote, original,
                           pdf_page=statement['pdf_page'], pdf_end_page=statement['pdf_end_page'],
                           location_status=statement['location_status'])]
            statements[statement['id']] = (statement, ev)
            root['definition']['statements'].append({'id': statement['id'], 'raw': quote, 'evidence': ev})
        for relationship in definition['relationships']:
            statement, ev = statements[relationship['statement_id']]
            if compact(relationship['name']) not in compact(statement['quote']):
                raise KBError('TAXONOMY_QUOTE_MISMATCH', 'Listed name is absent from its source clause.')
            path = relationship['path']
            for level, name in enumerate(path):
                key = tuple(path[:level + 1])
                if key not in path_nodes:
                    item = node(ns['namespace'], [sid, key], name, ev, statement['quote'],
                                'explicit_example' if level == len(path) - 1 and relationship['relation'] == '原文列举' else 'category')
                    item['section_id'] = sid
                    path_nodes[key] = item
                    ns['nodes'].append(item)
                item = path_nodes[key]
                if ev not in [item['evidence']]:
                    for source in ev:
                        if source not in item['evidence']:
                            item['evidence'].append(source)
                if level:
                    parent = path_nodes[tuple(path[:level])]
                    item['parent_id'] = parent['id']
                    ns['relations'].append(edge(ns['namespace'], item['id'], parent['id'],
                                                'explicit_example' if item['kind'] == 'explicit_example' else 'parent', ev))
        for exclusion in definition['exclusions']:
            statement, ev = statements[exclusion['statement_id']]
            item = condition(exclusion['quote'], ev)
            item.update(names=exclusion['names'], scope_label=exclusion['scope_label'])
            owners = [n for n in path_nodes.values() if compact(n['name']) == compact(exclusion['scope_label'])]
            for owner in owners or [root]:
                owner['exclusions'].append(item)
        for item in path_nodes.values():
            _necessary_condition(item, statements)
        qualified = _qualified_tables(definition['name'], path_nodes, entries[sid])
        for path, item in path_nodes.items():
            matched = entries[sid].get(compact(item['name']))
            targets = ([matched] if matched else []) + qualified.get(item['id'], [])
            for target in targets:
                ns['relations'].append(edge(ns['namespace'], item['id'], target['id'], 'table_link',
                                           item['evidence'] + target['evidence']))
            if not targets and item['kind'] == 'explicit_example' and not any(
                    compact(name) in entries[sid] or qualified.get(path_nodes[tuple(path[:i + 1])]['id'])
                    for i, name in enumerate(path)):
                ns['gaps'].append({'kind': 'unmapped_inspection_table', 'node_id': item['id'], 'section_id': sid})
        for bi, bridge in enumerate(bridge_record['raw_payload']['bridges']):
            if bridge['section_id'] != sid:
                continue
            source = next(((statement, ev) for statement, ev in statements.values()
                           if compact(bridge['anchor']) in compact(statement['quote'])), None)
            targets = [entries[sid].get(compact(target)) for target in bridge['targets']]
            if source is None or any(t is None for t in targets):
                raise KBError('TAXONOMY_BRIDGE_MISMATCH', 'Manual bridge is missing its original anchor or exact target.')
            matches = [n for n in path_nodes.values() if compact(n['name']) == compact(bridge['name'])]
            bridge_ev = [evidence(bridge_record, f'/bridges/{bi}', bridge['anchor'])] + source[1]
            for item in matches:
                for target in targets:
                    ns['relations'].append(edge(ns['namespace'], item['id'], target['id'], 'manual_bridge', bridge_ev))
    for ri, row in enumerate(guide['inspection_rows']):
        table = entries[row['section_id']].get(compact(re.sub(r'检验项目.*$', '', re.sub(r'^表\s*\d+[-－]\d+', '', row['caption'])).strip()))
        if not table:
            ns['gaps'].append({'kind': 'unmapped_requirement', 'source_id': row['id']})
            continue
        ev = [evidence(guide_record, f'/inspection_rows/{ri}', ' | '.join(str(c or '') for c in row['raw_cells']),
                       original, pdf_page=row['pdf_page'], row_index=row['source_row'], location_status='table_row')]
        raw = (row.get('category_raw') or '') + '\n' + '\n'.join(table['notes'])
        ns['requirements'].append({'id': identity('taxonomy-requirement', ns['namespace'], row['id']),
            'node_id': table['id'], 'table_id': table['table_id'],
            'semantic': {'project': row['item_raw'], 'basis_raw': row['basis_raw'], 'method_raw': row['method_raw']},
            'condition': condition(raw or '检验表候选；本次任务制度和样品条件尚未核实', ev),
            'exclusions': [], 'overrides': [], 'evidence': ev, 'review_state': 'review_pending',
            'coverage_state': 'partial', 'task_required': 'unknown'})
    # Repeated clauses can support one edge; deduplicate content-identical edges.
    ns['relations'] = list({r['id']: r for r in ns['relations']}.values())
    return ns


def validate_snapshot(payload):
    if payload.get('schema_version') != 'taxonomy-1' or payload.get('executable') is not False or payload.get('review_state') != 'review_pending':
        raise KBError('TAXONOMY_SNAPSHOT_INVALID', 'Candidate snapshot must retain its schema and non-executable review boundary.')
    seen = set()
    for ns in payload['namespaces']:
        if ns['namespace'] in seen:
            raise KBError('TAXONOMY_NAMESPACE_CONFLICT', 'Namespace repeated in snapshot.')
        seen.add(ns['namespace'])
        nodes = {n['id']: n for n in ns['nodes']}
        if len(nodes) != len(ns['nodes']):
            raise KBError('TAXONOMY_NODE_CONFLICT', 'Duplicate node identity.')
        links = defaultdict(set)
        for link in ns['relations']:
            if link['child_id'] not in nodes or link['parent_id'] not in nodes or not link['evidence']:
                raise KBError('TAXONOMY_RELATION_INVALID', 'Relation crosses namespace or lacks source evidence.')
            links[link['child_id']].add(link['parent_id'])
        visiting, visited = set(), set()
        def visit(key):
            if key in visiting:
                raise KBError('TAXONOMY_CYCLE', 'Classification source relation contains a cycle.')
            if key in visited:
                return
            visiting.add(key)
            for parent in links[key]:
                visit(parent)
            visiting.remove(key); visited.add(key)
        for key in nodes:
            visit(key)
        for item in ns['requirements']:
            if item['node_id'] not in nodes or not item['evidence']:
                raise KBError('TAXONOMY_RELATION_INVALID', 'Inspection requirement has no same-namespace source node.')
    return payload
