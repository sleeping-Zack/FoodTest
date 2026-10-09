"""Evidence-first classification over the project's read-only standards library.

Matching proposes categories. It never infers mandatory tests or numeric limits.
Every result preserves its own standard namespace and source location.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from product_definitions import build_definitions, list_names

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'references/食品标准资料库'
SNAPSHOT = '2026-10-09'
VERSION = 'food-classifier-1.2.0'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or '')).lower()


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def food_tokens(value):
    """Keep lexical boundaries: 苹果 must not match 苹果汁, 萝卜 must not match 萝卜叶."""
    pieces = re.split(r'[\s、,，()（）:：;；/]+', unicodedata.normalize('NFKC', value or ''))
    return {re.sub(r'^(?:例如|包括|含)|等$', '', p).lower() for p in pieces if p}


def family(code):
    return re.sub(r'[-—]\d{4}$', '', code)


def aliases(label):
    label = norm(label)
    values = {label, re.sub(r'\(.*?\)', '', label)}
    values.update(re.findall(r'\(([^()]*)\)', label))
    values.update(norm(v) for v in list_names(label))
    values.update(re.split(r'[、,()；;]|以及|及|(?<!调)(?<!饱)和|或', label))
    return {v for v in values if 1 <= len(v) <= 55 and v != '其他' and not v.startswith(('包括', '不包括', '如'))}


def definition_name_terms(label):
    """Only literal names in positive source lists; do not reuse title splitting."""
    value = norm(label)
    names = {value}
    parts = list_names(label)
    names.update(norm(p) for p in parts)
    for part in parts or [label]:
        bracket = re.fullmatch(r'([^（(]+)[（(]([^()（）]+)[）)]', part)
        if not bracket or re.search(r'除外|不包括|加工|原料|比例|\d|用于|适用', bracket[2]):
            continue
        names.add(norm(bracket[1]))
        inner = re.sub(r'^(?:含|如|包括|又名)', '', bracket[2])
        names.update(norm(p) for p in list_names(inner))
    return names


# These are name-normalization hints, not standard classification rules.
ALIASES = {'白萝卜': '萝卜', '土豆': '马铃薯', '西红柿': '番茄', '番薯': '甘薯',
           '红薯': '甘薯', '地瓜': '甘薯', '百香果': '西番莲', '酸奶': '发酵乳',
           '巴氏奶': '巴氏杀菌乳',
           '生牛乳': '生乳', '矿泉水': '饮用天然矿泉水', '纯净水': '饮用纯净水'}
# Ambiguous colloquial names intentionally do not resolve to a single product.
AMBIGUOUS = {'牛奶': ['巴氏杀菌乳', '灭菌乳', '调制乳', '生乳'],
             '鲜牛奶': ['巴氏杀菌乳', '高温杀菌乳', '生乳'],
             '生鲜牛奶': ['巴氏杀菌乳', '高温杀菌乳', '生乳'],
             '纯牛奶': ['巴氏杀菌乳', '灭菌乳'],
             '酸奶': ['发酵乳', '风味发酵乳'],
             '面粉': ['小麦粉', '玉米粉', '其他谷物碾磨加工品'],
             '鱼': ['淡水鱼', '海水鱼'], '奶粉': ['乳粉', '调制乳粉', '婴幼儿配方食品']}
PROCESS_WORDS = {
    'fresh': ['新鲜', '鲜', '生鲜', '未经加工'],
    'frozen': ['冷冻', '冻', '速冻'],
    'cooked': ['熟制', '熟', '烤', '卤', '油炸', '煮'],
    'dried': ['干制', '脱水', '干'],
    'pickled': ['腌制', '腌渍', '酱腌', '腌'],
    'fermented': ['发酵'],
    'drink': ['饮料', '果汁', '蔬菜汁', '汁'],
}
PROCESS_LABELS = {'': '未说明', 'fresh': '新鲜 / 未加工', 'frozen': '冷冻 / 速冻',
                  'cooked': '熟制', 'dried': '干制', 'pickled': '腌渍',
                  'fermented': '发酵', 'drink': '果汁 / 饮料'}
RAW_CHAPTERS = {'C33', 'C34', 'C35', 'C36', 'C37', 'C38', 'C39'}


class Library:
    def __init__(self, root=ROOT, base_path=''):
        self.root = Path(root).resolve()
        self.base_path = base_path
        self.lib = self.root / 'references/食品标准资料库'
        self.guide = read_json(self.lib / '索引/食品项目标准方法关联.json')
        self.registry = read_json(self.lib / '索引/标准总目录.json')
        self.by_id = {r['id']: r for r in self.registry}
        self.by_family = defaultdict(list)
        self.files = {}
        self.files_by_path = {}
        self.by_code = defaultdict(list)
        for record in self.registry:
            self.by_family[record['family']].append(record)
            self.by_code[record['code']].append(record)
            for attachment in record.get('attachments', []):
                if attachment.get('download_status') in ('downloaded', 'existing_local'):
                    self.register_file(attachment.get('path'), attachment)
        self.guide_file = self.register_file(self.guide['source'], {'sha256': self.guide.get('sha256'), 'page_count': self.guide.get('pdf_pages')})
        cached_pages = read_json(Path(__file__).resolve().parent / 'resources/guide-pages.json')
        if cached_pages['sha256'] != self.guide['sha256']:
            raise ValueError('细则页索引与资料库版本不一致，请刷新来源缓存。')
        self.guide_pages = cached_pages['pages']
        self.taxonomies = [read_json(p) for p in sorted((self.lib / '分类体系').glob('*.json'))]
        for taxonomy in self.taxonomies:
            taxonomy['file_id'] = self.register_file(taxonomy['source'], {'sha256': taxonomy['sha256']})
        self.chapters = {c['id']: c for c in self.guide['chapters']}
        self.sections = {s['id']: s for s in self.guide['sections']}
        self.entries = {}
        self.build_entries()
        self.definitions = {d['section_id']: d for d in build_definitions(self.guide, self.guide_pages)}
        self.inspection_bridges = read_json(Path(__file__).resolve().parent / 'resources/inspection-bridges.json')['bridges']
        self.build_definition_links()
        self.taxonomy_nodes = {t['namespace']: self.build_taxonomy_nodes(t) for t in self.taxonomies}
        self.edges = defaultdict(list)
        for edge in read_json(self.lib / '索引/标准逐层引用关系.json'):
            self.edges[edge['source_id']].append(edge)
        fingerprint_sources = [self.lib / p for p in ['索引/标准总目录.json', '索引/食品项目标准方法关联.json', '索引/标准逐层引用关系.json']]
        fingerprint_sources += sorted((self.lib / '分类体系').glob('*.json'))
        fingerprint_sources.append(Path(__file__).resolve().parent / 'resources/guide-pages.json')
        fingerprint_sources.append(Path(__file__).resolve().parent / 'product_definitions.py')
        fingerprint_sources.append(Path(__file__).resolve())
        fingerprint_sources.append(Path(__file__).resolve().parent / 'resources/inspection-bridges.json')
        self.fingerprint = hashlib.sha256(''.join(hashlib.sha256(p.read_bytes()).hexdigest() for p in fingerprint_sources).encode()).hexdigest()

    def register_file(self, relative, metadata=None):
        if not relative:
            return None
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root / 'references') or path.suffix.lower() not in {'.pdf', '.doc', '.docx'} or not path.is_file():
            return None
        key = path.relative_to(self.root).as_posix()
        file_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        if file_id not in self.files:
            self.files[file_id] = {'id': file_id, 'path': path, 'name': path.name, 'relative_path': key,
                                   'size': path.stat().st_size, **(metadata or {})}
            self.files[file_id]['path'] = path
        self.files_by_path[key] = file_id
        return file_id

    def file_info(self, file_id):
        value = self.files.get(file_id)
        if not value:
            return None
        return {k: value.get(k) for k in ['id', 'name', 'size', 'sha256', 'page_count', 'extraction_status', 'unreliable_text_pages']} | {
            'url': self.base_path + '/api/files/' + file_id, 'format': value['path'].suffix[1:],
            'download_url': self.base_path + '/api/files/' + file_id + '?download=1'}

    def evidence(self, file_id, page, quote, locator='', bbox=None, printed_page=None):
        return {'file_id': file_id, 'pdf_page': page, 'printed_page': printed_page, 'quote': quote,
                'locator': locator, 'bbox': bbox, 'file': self.file_info(file_id)}

    def build_entries(self):
        rows_by_caption = defaultdict(list)
        tables_by_caption = defaultdict(list)
        for row in self.guide['inspection_rows']:
            rows_by_caption[(row['section_id'], norm(row['caption']))].append(row)
        for table in self.guide['tables']:
            if table['kind'] == 'inspection_items':
                tables_by_caption[(table['section_id'], norm(table['caption']))].append(table)
        classification = []
        for table in self.guide['tables']:
            if table['kind'] == 'other_table' and '所属' in table['caption']:
                for row in table['cells'][1:]:
                    if len(row) >= 3 and row[1] and row[2]:
                        classification.append((table, row))
        for section in self.guide['sections']:
            chapter = self.chapters[section['chapter_id']]['name']
            common = {'section_id': section['id'], 'chapter_id': section['chapter_id'], 'chapter': chapter,
                      'section_name': section['name'], 'scope': section['scope'], 'product_types': section['product_types']}
            sid = section['id']
            self.entries[sid] = dict(common, id=sid, label=section['name'], kind='section',
                aliases=aliases(section['name']), rows=[r for r in self.guide['inspection_rows'] if r['section_id'] == sid],
                evidence=self.evidence(self.guide_file, section['pdf_page'], section['scope'] + '\n' + section['product_types'],
                                       section['heading'] + ' · 适用范围 / 产品种类（章节起始页）', printed_page=section['printed_page']),
                classification=None, notes=[])
            for (section_id, caption), rows in rows_by_caption.items():
                if section_id != sid:
                    continue
                tables = tables_by_caption[(section_id, caption)]
                first = tables[0]
                label = re.sub(r'^表\d+[-－]\d+', '', first['caption'])
                label = re.sub(r'检验项目.*$', '', label).strip()
                eid = 'leaf-' + first['id']
                classified = None
                for table, row in classification:
                    if table['section_id'] == sid and aliases(row[1]) & aliases(label):
                        classified = {'label': clean(row[2]), 'evidence': self.evidence(self.guide_file, table['pdf_page'],
                            ' | '.join(str(c or '') for c in row), table['caption'], table['bbox'], table['printed_page'])}
                        break
                self.entries[eid] = dict(common, id=eid, label=label, kind='leaf', aliases=aliases(label), rows=rows,
                    evidence=self.evidence(self.guide_file, first['pdf_page'], first['caption'], first['caption'],
                                           first['bbox'], first['printed_page']), classification=classified,
                    notes=list(dict.fromkeys(n for t in tables for n in t.get('notes', []))))
                # Footnote markers are retained in the source caption, not treated
                # as part of a food's name (e.g. 煎炸过程用油a).
                if re.search(r'[\u4e00-\u9fff）)][a-h]$', label):
                    self.entries[eid]['aliases'].update(aliases(label[:-1]))

    def build_taxonomy_nodes(self, taxonomy):
        nodes = []
        if taxonomy['coded_nodes']:
            for n in taxonomy['coded_nodes']:
                if not n['label'].strip() or n.get('label_status') != '原文类别':
                    continue
                nodes.append({'label': clean(n['label']), 'code': n['classification_code'], 'parent_id': n.get('parent_id'),
                              'evidence': self.evidence(taxonomy['file_id'], n['pdf_page'], n['classification_code'] + ' ' + n['label'],
                                                       taxonomy['title'] + ' · ' + n['classification_code'])})
            return nodes
        group = ''
        for table in taxonomy['tables']:
            for index, row in enumerate(table['cells']):
                if not row or not any(row):
                    continue
                if norm(row[0]) in ['食品类别', '食品类别(名称)', '序号']:
                    continue
                if len(row) < 2:
                    continue
                if row[0]:
                    group = clean(row[0])
                description = row[1] or ''
                quote = ' | '.join(str(c or '') for c in row)
                if taxonomy['namespace'] == 'GB_2763-2026':
                    lines = [clean(x) for x in description.split('\n') if re.search(r'[\u4e00-\u9fff]', x)]
                    subtitle = lines[0] if lines else ''
                    label = group + (' / ' + subtitle if len(subtitle) <= 18 else '')
                    nodes.append({'label': label, 'code': None, 'terms': description, 'part': row[2] if len(row) > 2 else '',
                        'evidence': self.evidence(taxonomy['file_id'], table['pdf_page'], quote,
                            taxonomy['title'] + f' · 表格第 {index + 1} 行', table.get('bbox'))})
                elif taxonomy['namespace'] in ['GB_2762-2025', 'GB_2761-2017', 'GB_29921-2021']:
                    for line in description.split('\n'):
                        if not re.search(r'[\u4e00-\u9fff]', line):
                            continue
                        label = clean(line)
                        nodes.append({'label': label, 'code': None, 'group': group, 'terms': label,
                            'evidence': self.evidence(taxonomy['file_id'], table['pdf_page'], quote,
                                taxonomy['title'] + f' · 表格第 {index + 1} 行', table.get('bbox'))})
                else:
                    nodes.append({'label': clean(group), 'code': None, 'terms': quote,
                        'evidence': self.evidence(taxonomy['file_id'], table['pdf_page'], quote,
                            taxonomy['title'] + f' · 表格第 {index + 1} 行', table.get('bbox'))})
        return nodes

    def metadata(self):
        return {'version': VERSION, 'snapshot': SNAPSHOT, 'fingerprint': self.fingerprint,
                'chapters': len(self.chapters), 'sections': len(self.sections),
                'food_entries': sum(x['kind'] == 'leaf' for x in self.entries.values()),
                'inspection_rows': len(self.guide['inspection_rows']), 'standards': len(self.registry),
                'taxonomies': len(self.taxonomies), 'product_definitions': len(self.definitions), 'process_options': PROCESS_LABELS,
                'limitations': ['资料快照截至 2026-10-09；国标版本仍须结合样品日期、过渡条款核定。',
                    '关联范围为本库细则及分类表已收录的依据，尚未覆盖全部食品、全部国标。',
                    '分类候选和细则项目不等于本次任务的必检清单；例外、适用范围及方法条件须核对。']}

    def catalog(self, query='', chapter=''):
        q = norm(query)
        values = [self.public_entry(e) for e in self.entries.values() if
                  (not chapter or e['chapter_id'] == chapter) and
                  (not q or q in norm(e['label'] + e['section_name'] + e['chapter']))]
        return {'chapters': [{'id': k, 'name': v['name']} for k, v in self.chapters.items()], 'entries': values}

    def public_entry(self, e, score=None, reason=None):
        value = {k: e[k] for k in ['id', 'label', 'kind', 'chapter', 'chapter_id', 'section_id', 'section_name', 'evidence', 'classification']}
        value['row_count'] = len(e['rows'])
        if score is not None:
            value.update(score=score, reason=reason)
        return value

    def build_definition_links(self):
        self.definition_terms = defaultdict(list)
        for definition in self.definitions.values():
            sid = definition['section_id']
            definition['chapter'] = self.chapters[definition['chapter_id']]['name']
            for statement in definition['statements']:
                ev = self.evidence(self.guide_file, statement['pdf_page'], statement['quote'],
                    definition['heading'] + ' · 产品种类 · ' + statement['id'],
                    printed_page=statement['pdf_page'] - self.guide['page_offset'])
                ev['pdf_end_page'] = statement['pdf_end_page']
                ev['location_status'] = statement['location_status']
                statement['evidence'] = ev
            lookup = {s['id']: s for s in definition['statements']}
            children = [e for e in self.entries.values() if e['kind'] == 'leaf' and e['section_id'] == sid]
            bridges = {}
            for bridge in self.inspection_bridges:
                if bridge['section_id'] != sid:
                    continue
                statements = [s for s in definition['statements'] if norm(bridge['anchor']) in norm(s['quote'])]
                targets = [e for e in children if e['label'] in bridge['targets']]
                if not statements or len(targets) != len(bridge['targets']):
                    raise ValueError('检验表用语关联与原文不一致：' + bridge['name'])
                bridges[norm(bridge['name'])] = (targets, statements[0]['evidence'])
            for relation in definition['relationships']:
                targets = []
                matched_index = None
                bridge_evidence = None
                for index in range(len(relation['path']) - 1, -1, -1):
                    label = relation['path'][index]
                    # No substring matching: 小麦 as ingredient must not become 小麦粉.
                    terms = {norm(label), norm(label.removesuffix('类'))}
                    exact = [e for e in children if norm(e['label']) in terms]
                    targets = exact or [e for e in children if terms & e['aliases']]
                    if not targets and norm(label) in bridges:
                        targets, bridge_evidence = bridges[norm(label)]
                    if targets:
                        matched_index = index
                        break
                # One project table covers the entire section; otherwise do not
                # arbitrarily select one child's inspection items.
                if not targets and len(children) == 1:
                    targets = children
                    relation['link_kind'] = 'single_section_table'
                elif targets:
                    relation['link_kind'] = 'direct' if matched_index == len(relation['path']) - 1 else 'ancestor'
                else:
                    relation['link_kind'] = 'unmapped'
                relation['matched_ancestor'] = relation['path'][matched_index] if matched_index is not None else None
                relation['bridge_evidence'] = bridge_evidence
                relation['entry_ids'] = [e['id'] for e in targets]
                relation['evidence'] = lookup[relation['statement_id']]['evidence']
            # A broad source node may cover several child tables. Resolve its
            # descendants within the same branch, never union the whole section.
            resolved = [r for r in definition['relationships'] if r['entry_ids']]
            for relation in definition['relationships']:
                if not relation['entry_ids']:
                    prefix = relation['path']
                    descendants = [r for r in resolved if len(r['path']) > len(prefix) and r['path'][:len(prefix)] == prefix]
                    ids = list(dict.fromkeys(eid for r in descendants for eid in r['entry_ids']))
                    if not ids:
                        ids = [e['id'] for e in children if e['classification'] and norm(e['classification']['label']) == norm(relation['name'])]
                    if ids:
                        relation['entry_ids'] = ids
                        relation['link_kind'] = 'descendant_options'
                relation['link_state'] = 'unmapped' if not relation['entry_ids'] else 'unique' if len(relation['entry_ids']) == 1 else 'multiple'
                relation['search_names'] = sorted(definition_name_terms(relation['name']))
                for eid in relation['entry_ids'] or [None]:
                    for name in relation['search_names']:
                        self.definition_terms[name].append(dict(relation, section_id=sid, entry_id=eid))
            for exclusion in definition['exclusions']:
                exclusion['evidence'] = lookup[exclusion['statement_id']]['evidence']
                scope = norm(exclusion.get('scope_label'))
                section_scope = scope in {norm(definition['name']), norm(definition['name'])+'产品'}
                blocked = [e['id'] for e in children if scope in e['aliases']]
                if not blocked and not section_scope:
                    blocked = list(dict.fromkeys(eid for r in definition['relationships'] if scope in {norm(p) for p in r['path']} for eid in r['entry_ids']))
                exclusion['blocked_entry_ids'] = [sid] + [e['id'] for e in children] if section_scope else blocked
                exclusion['scope_state'] = 'section' if section_scope else 'branch' if blocked else 'scope_unresolved'
            definition['inspection_entries'] = [self.public_entry(e) for e in children]
            definition['evidence'] = self.evidence(self.guide_file, definition['pdf_page'], definition['raw_text'],
                definition['heading'] + ' · 产品种类（完整分节）',
                printed_page=definition['pdf_page'] - self.guide['page_offset'])
            definition['evidence']['pdf_end_page'] = definition['pdf_end_page']

    def definition_catalog(self, query='', chapter=''):
        q = norm(query)
        values = [d for d in self.definitions.values() if (not chapter or d['chapter_id'] == chapter)
                  and (not q or q in norm(d['name'] + d['chapter'] + d['readable_text']))]
        return {'version': VERSION, 'fingerprint': self.fingerprint, 'total': len(self.definitions),
                'source': self.file_info(self.guide_file), 'definitions': values}

    def definition_matches(self, query):
        return [r for t in self.query_terms(query) for r in self.definition_terms.get(t, [])]

    def definition_exclusions(self, query):
        terms = set(self.query_terms(query))
        return [dict(x, section_id=d['section_id'], section_name=d['name'])
                for d in self.definitions.values() for x in d['exclusions']
                if terms & {norm(n) for n in x['names']}]

    def query_terms(self, query):
        q = norm(query)
        terms = [q]
        stripped = re.sub(r'^(?:新鲜|生鲜|鲜食|冷冻|冰鲜|鲜|生)(?=.{2,})', '', q)
        if stripped != q:
            terms.append(stripped)
        for t in list(terms):
            if t in ALIASES:
                terms.append(ALIASES[t])
        return list(dict.fromkeys(terms))

    def find_candidates(self, query, process=''):
        terms = self.query_terms(query)
        q = terms[0]
        broad = AMBIGUOUS.get(q, [])
        routed_labels, derived_process = self.processing_candidates(q, process)
        definition_hits = self.definition_matches(query)
        excluded_entries = {eid for x in self.definition_exclusions(query) for eid in x['blocked_entry_ids']}
        results = []
        for entry in self.entries.values():
            score, reason = 0, ''
            matched = next((t for t in terms if t in entry['aliases']), None)
            if matched:
                score = 100 if matched == q else 94
                reason = f'名称命中“{matched}”' if matched == q else f'名称归一后检索“{matched}”（内置名称提示，须核对样品）'
            elif broad and any(norm(t) in entry['aliases'] for t in broad):
                score, reason = 83, '通用名称对应多个产品类型'
            elif any(len(t) > 1 and t in norm(entry['label']) for t in terms):
                score, reason = 76, '类别名称包含输入词，需要核对完整定义'
            hits = [h for h in definition_hits if h['entry_id'] == entry['id']]
            if hits and score < 90:
                # Parsed lists are candidates, not verified semantic judgements.
                score = 88 if entry['kind'] == 'leaf' else 82
                reason = '产品种类原文列举：' + ' → '.join(hits[0]['path']) + '；请核对定义条件'
            if entry['kind'] == 'leaf' and entry['label'] in routed_labels:
                score, reason = max(score, 84), '根据名称中的加工方式召回；请核对产品定义、配料与食用方式'
            if not score and entry['kind'] == 'leaf' and any(len(a) >= 2 and q.endswith(a) and a != q for a in entry['aliases']):
                score, reason = 72, '名称末尾命中食品类别；复合产品须核对完整定义'
            # A processed food must not inherit its raw ingredient's classification.
            effective_process = process or derived_process
            if effective_process in {'cooked', 'dried', 'pickled', 'fermented', 'drink'} and entry['chapter_id'] in RAW_CHAPTERS:
                score = 0
            if entry['id'] in excluded_entries:
                score = 0
            if score:
                if entry['kind'] == 'section' and any(e['kind'] == 'leaf' and e['section_id'] == entry['id'] and
                     any(t in e['aliases'] for t in terms) for e in self.entries.values()):
                    score -= 15
                results.append(self.public_entry(entry, score, reason) | {'definition_matches': hits})
        results.sort(key=lambda x: (-x['score'], x['kind'] != 'leaf', x['id']))
        return results[:24]

    def processing_candidates(self, q, process):
        derived = process
        base = q
        for suffix, kind in [('罐头', 'canned'), ('饮料', 'drink'), ('汁', 'drink'),
                             ('干', 'dried'), ('酱', 'paste')]:
            if q.endswith(suffix) and len(q) > len(suffix):
                base, derived = q[:-len(suffix)], process or kind
                break
        prefix = re.match(r'^(烤|熏|卤|酱卤|炸|腌|干制|冻)(.+)$', q)
        if prefix:
            word, base = prefix.groups()
            derived = process or ('pickled' if word == '腌' else 'dried' if word == '干制' else 'frozen' if word == '冻' else 'cooked')
        base_terms = self.query_terms(base)
        if base in ['鸡', '鸭', '鹅', '猪', '牛', '羊']:
            base_terms.append(base + '肉')
        raw_chapters = {e['chapter_id'] for e in self.entries.values() if e['kind'] == 'leaf' and any(t in e['aliases'] for t in base_terms)}
        labels = []
        if 'C36' in raw_chapters:
            labels = {'drink': ['果蔬汁类及其饮料'], 'dried': ['水果干制品'], 'pickled': ['蜜饯'],
                      'paste': ['果酱'], 'frozen': ['速冻水果制品']}.get(derived, [])
        if 'C34' in raw_chapters:
            mushroom = any('菌' in e['label'] and any(t in e['aliases'] for t in base_terms) for e in self.entries.values())
            labels = {'drink': ['果蔬汁类及其饮料'], 'dried': ['干制食用菌' if mushroom else '蔬菜干制品'],
                      'pickled': ['腌渍食用菌' if mushroom else '酱腌菜'], 'frozen': ['速冻蔬菜制品']}.get(derived, [])
        if 'C33' in raw_chapters and derived == 'cooked':
            labels = ['熏烧烤肉制品'] if q.startswith(('烤', '熏')) else ['油炸肉制品'] if q.startswith('炸') else ['酱卤肉制品'] if q.startswith(('卤','酱卤')) else ['酱卤肉制品', '熏烧烤肉制品', '油炸肉制品']
        if q == '三文鱼' or (q.endswith('鱼') and not raw_chapters):
            labels = ['淡水鱼', '海水鱼']
        return labels, derived

    def standards_for(self, entry):
        uses = defaultdict(list)
        for row in entry['rows']:
            for side, role in [('basis_codes', '判定 / 限量依据'), ('method_codes', '检验方法')]:
                for code in row.get(side, []):
                    uses[code].append({'role': role, 'item': row['item_raw'], 'row_id': row['id'], 'pdf_page': row['pdf_page'],
                        'evidence': self.row_citation(row, code)})
        # Section-level citations are relevant candidates, not all mandatory for each leaf.
        for code in self.sections[entry['section_id']]['citation_codes']:
            if code not in uses:
                uses[code].append({'role': '所在分节引用 · 适用性待核', 'item': '',
                    'evidence': self.guide_citation(code, self.sections[entry['section_id']])})
        return [self.resolve_standard(code, evidence) for code, evidence in sorted(uses.items(), key=lambda kv: (not kv[0].startswith('GB'), kv[0]))]

    def row_citation(self, row, code):
        regex = re.compile(re.escape(norm(code)) + r'(?![\d.])')
        raw = ' | '.join(str(x or '') for x in row['raw_cells'])
        if not regex.search(norm(raw)):
            for fragment in row.get('continuation_fragments', []):
                quote = ' | '.join(str(x or '') for x in fragment['raw_cells'])
                if regex.search(norm(quote)):
                    return self.evidence(self.guide_file, fragment['pdf_page'], quote,
                        row['caption'] + ' · 跨页续表（承接第 ' + str(row['number']) + ' 项）',
                        printed_page=fragment['pdf_page'] - self.guide['page_offset'])
        return self.evidence(self.guide_file, row['pdf_page'], raw, row['caption'], printed_page=row['printed_page'])

    def guide_citation(self, code, section):
        # Locate the cited standard number itself, not an unrelated product definition.
        compact = norm(code)
        regex = re.compile(re.escape(compact) + r'(?![\d.])')
        for page in self.guide_pages[section['pdf_page'] - 1:section['pdf_end_page']]:
            text = norm(page['text'])
            match = regex.search(text)
            if match:
                # Quote the original lines containing the number, preserving original whitespace.
                lines = page['text'].splitlines()
                index = next((i for i, line in enumerate(lines) if regex.search(norm(line))), None)
                excerpt = '\n'.join(lines[max(0, index-1):index+3]) if index is not None else page['text']
                return self.evidence(self.guide_file, page['pdf_page'], excerpt,
                    section['heading'] + ' · 标准号引用 ' + code, printed_page=page['pdf_page'] - self.guide['page_offset'])
        # Explicitly mark a section locator if text-layout problems prevent exact placement.
        return self.evidence(self.guide_file, section['pdf_page'], section['basis'],
                             section['heading'] + ' · 检验依据（跨页范围，标准号精确位置待核）', printed_page=section['printed_page'])

    def record_public(self, record):
        attachments = []
        for attachment in record.get('attachments', []):
            key = str(attachment.get('path', '')).replace('\\', '/')
            if key in self.files_by_path:
                attachments.append(self.file_info(self.files_by_path[key]))
        blocked = bool(record.get('is_draft') or record.get('identity_conflict') or record.get('is_unidentified'))
        status = record.get('official_status') or '待核验'
        if record.get('is_draft'):
            status = '报批稿 / 征求意见稿'
        elif record.get('identity_conflict'):
            status = '附件身份冲突'
        implementation = record.get('implementation_date') or ''
        if not blocked and re.fullmatch(r'\d{4}-\d{2}-\d{2}', implementation) and implementation > SNAPSHOT:
            status = '即将实施'
        result = {k: record.get(k) for k in ['id', 'code', 'family', 'title', 'publication_date', 'implementation_date', 'detail_url', 'catalog_source', 'is_amendment']}
        result.update(status=status, files=attachments, excluded=blocked,
                      current_candidate=status == '现行' and not blocked and not record.get('is_amendment'),
                      as_of=record.get('as_of', SNAPSHOT))
        return result

    def resolve_standard(self, code, uses=None):
        dated = bool(re.search(r'[-—]\d{4}$', code))
        records = self.by_family.get(family(code), [])
        versions = [self.record_public(r) for r in records]
        versions.sort(key=lambda r: (r['excluded'], not r['current_candidate'], not bool(r['files']), r['code']), reverse=False)
        relevant = [v for v in versions if not dated or v['code'] == code]
        good = [v for v in relevant if not v['excluded'] and not v.get('is_amendment') and v['files']]
        current = [v for v in good if v['current_candidate']]
        return {'requested_code': code, 'family': family(code), 'national': code.startswith('GB ' ) or code.startswith('GB/T '),
                'dated_reference': dated, 'uses': uses or [], 'versions': versions,
                'available': bool(good), 'current_available': bool(current),
                'status': '有指定版原文' if dated and good else '有现行候选原文' if current else '仅有待核或历史版本' if good else '缺正式原文',
                'selection_note': '有年号引用：保留指定版本，后续修订不可自动替换。' if dated else '无年号引用：列出版本候选，按适用日期、废止/替代公告及过渡条款核定。'}

    def taxonomy_results(self, query, entry, process='', packaging=''):
        terms = self.query_terms(query)
        routed, derived = self.processing_candidates(norm(query), process)
        effective_process = process or derived
        if routed and effective_process not in ('', 'fresh', 'frozen'):
            # Searching a raw name plus a processing attribute must not surface raw categories.
            terms = [norm(query)] if derived and norm(query) not in self.query_terms(re.sub(r'(汁|干|酱)$','',norm(query))) else []
        if entry:
            terms += [norm(entry['label'])]
        terms = list(dict.fromkeys(terms))
        results = []
        for tax in self.taxonomies:
            nodes = self.taxonomy_nodes[tax['namespace']]
            targets = []
            condition = ''
            # These independent rules deliberately stop at the supported parent category.
            if entry and process == 'fresh':
                chapter = entry['chapter_id']
                if tax['namespace'] in ['GB_2760-2024', 'GB_14880-2012']:
                    target = {'C36': '新鲜水果', 'C34': '新鲜蔬菜', 'C37': '鲜蛋'}.get(chapter)
                    if chapter == 'C34' and '菌' in entry['label']:
                        target = '新鲜食用菌和藻类'
                    if target:
                        targets = [n for n in nodes if n['label'] == target]
                        condition = '用户补充为新鲜 / 未加工；独立核对本标准的同名类别。细分类别仍需表面处理等信息。'
                elif tax['namespace'] in ['GB_2762-2025', 'GB_2761-2017']:
                    target = {'C36': '新鲜水果', 'C34': '新鲜蔬菜', 'C33': '畜禽肉', 'C37': '鲜蛋'}.get(chapter)
                    if chapter == 'C34' and '菌' in entry['label']:
                        target = '新鲜食用菌'
                    if target:
                        targets = [n for n in nodes if norm(n['label']).startswith(target)]
                        condition = '依据已补充的加工状态，定位本标准类别；污染物各表的细分、部位和例外须继续核对。'
            matches = []
            for n in nodes:
                content = norm(n.get('terms', n['label']))
                tokens = food_tokens(n.get('terms', n['label']))
                score = 0
                for term in terms:
                    if term == norm(n['label']):
                        score = max(score, 100)
                    elif term in tokens:
                        score = max(score, 90)
                    elif len(term) > 1 and term in aliases(n['label']):
                        score = max(score, 85)
                if score:
                    matches.append((score, n))
            matches.sort(key=lambda pair: (-pair[0], len(pair[1]['label'])))
            # Prefer a directly named subcategory (e.g. 萝卜 in 块根和块茎蔬菜) over a parent.
            if targets and matches and tax['namespace'] in ['GB_2762-2025', 'GB_2761-2017']:
                fine = [n for score, n in matches if score >= 85 and any(norm(t.get('group')) == norm(n.get('group')) for t in targets)]
                if fine:
                    targets = fine[:3]
            if not targets:
                targets = [n for _, n in matches[:5]]
            # Raw-table occurrences can include a different part or processed form.
            state = 'candidate' if targets else 'unresolved'
            note = condition or '原文名称检索命中；须核对配料、加工方式、测定部位和排除条款后确认。'
            if tax['namespace'] in ['GB_31650-2019', 'GB_31650.1-2022']:
                note = '兽残标准按动物种类、组织及药物分别规定；名称命中不能替代组织与药物条件。'
            if tax['namespace'] == 'GB_29921-2021':
                note = '须确认预包装、即食状态、商业无菌要求及本标准排除范围。'
                if packaging != 'prepackaged':
                    state = 'needs_details' if targets else 'unresolved'
            if tax['namespace'] == 'GB_31607-2021':
                note = '须确认散装、即食、非餐饮服务，以及热处理方式；目前尚未自动作出适用结论。'
            if not targets:
                note += ' 当前输入未找到可靠的分类映射；不代表本标准不适用。'
            candidates = []
            for n in targets:
                candidates.append({k: n.get(k) for k in ['label', 'code', 'group', 'part', 'evidence']})
            results.append({'namespace': tax['namespace'], 'standard': tax['standard'], 'title': tax['title'],
                            'state': state, 'note': note, 'source_note': tax['note'], 'candidates': candidates,
                            'evidence': self.evidence(tax['file_id'], tax['start_pdf_page'], '', tax['title']),
                            'version': self.resolve_standard(tax['standard'])})
        return results

    def classify(self, payload):
        query = str(payload.get('food', '')).strip()
        if not 1 <= len(query) <= 100:
            raise ValueError('请填写 1–100 字的食品名称。')
        process = str(payload.get('process', ''))
        if process not in PROCESS_LABELS:
            raise ValueError('加工状态无效。')
        packaging = str(payload.get('packaging', ''))
        if packaging not in ['', 'prepackaged', 'bulk']:
            raise ValueError('包装状态无效。')
        notes = str(payload.get('notes', '')).strip()
        if len(notes) > 1000:
            raise ValueError('补充说明最多 1000 字。')
        candidates = self.find_candidates(query, process)
        selected_id = str(payload.get('selected_id', ''))
        selected = None
        requested_section = None
        status = 'not_covered'
        reason = '没有找到足够可靠的分类依据，可补充规范名称、配料和工艺，或从食品目录选择候选。'
        if selected_id:
            if selected_id not in self.entries:
                raise ValueError('所选食品类别不存在。')
            selected = self.entries[selected_id]
            if selected['id'] in {eid for x in self.definition_exclusions(query) for eid in x['blocked_entry_ids']}:
                raise ValueError('该名称在所选类别的“产品种类”中被明确排除，请核对原文并选择其他类别。')
            status = 'user_selected'
            reason = '已按用户选择展开此类别的依据；该选择保留为人工输入，不等同于专家结论。'
            if selected['kind'] == 'section':
                requested_section, selected = selected, None
        elif candidates:
            top = candidates[0]
            tied = [c for c in candidates if c['score'] >= 90 and c['kind'] == top['kind']]
            strong = {c['id'] for c in candidates if c['definition_matches'] or c['score'] >= 90}
            if top['kind'] == 'leaf' and top['score'] >= 90 and len(tied) == 1 and norm(query) not in AMBIGUOUS and not any(
                    c['definition_matches'] and c['id'] != top['id'] for c in candidates):
                selected = self.entries[top['id']]
                status = 'name_match'
                reason = top['reason'] + '。请核对下列范围条件，再使用检验项目和文件。'
            elif len(strong) == 1 and top['kind'] == 'leaf' and top['definition_matches'] and norm(query) not in AMBIGUOUS and all(
                    h['link_state'] == 'unique' and h['link_kind'] != 'descendant_options' for h in top['definition_matches']):
                selected = self.entries[top['id']]
                status = 'definition_match'
                reason = '已沿产品种类原文自动关联“' + selected['label'] + '”检验项目表，无需另搜上级名称；样品范围和条件仍待核对。'
            else:
                status = 'needs_details'
                reason = '现有名称对应多个类别或仅命中描述，请补充产品状态并选择与实物相符的类别。'
                if top['kind'] == 'section' and top['score'] >= 90:
                    requested_section = self.entries[top['id']]
        # Broad sections expose alternative tables, not an unqualified union of
        # tests from every child. The food name is kept throughout the workflow.
        option_entries = {c['id']: c for c in candidates if c['kind'] == 'leaf'}
        if requested_section:
            option_entries = {e['id']: self.public_entry(e) | {'definition_matches': [], 'reason': '此分节下的检验表，需确认具体类型'}
                              for e in self.entries.values() if e['kind'] == 'leaf' and e['section_id'] == requested_section['id']}
            status = 'needs_details'
            reason = '该类别下有多张检验项目表，已列出各分支，无需重新搜索上级名称。'
            if len(option_entries) == 1:
                selected = self.entries[next(iter(option_entries))]
                status = 'definition_match' if not selected_id else 'user_selected'
                reason = '已关联该分节唯一的检验项目表；样品范围和条件仍待核对。'
        questions = []
        if selected and selected['chapter_id'] in RAW_CHAPTERS and not process:
            status = 'needs_details'
            questions.append('该食品是否为新鲜、未经加工的产品？加工后可能进入另一分节。')
            reason = '已定位名称对应的原料类别，尚缺加工状态。以下依据按候选类别展示。'
        if selected and process in {'cooked', 'dried', 'pickled', 'fermented', 'drink'} and selected['chapter_id'] in RAW_CHAPTERS:
            raise ValueError('所选原料类别与加工状态冲突，请选择加工后的食品类别。')
        if norm(query) in AMBIGUOUS:
            questions.append('请按包装标签、执行标准和加工工艺，选择具体产品类型。')
        if notes:
            questions.append('补充说明已保留供复核；当前规则不自动解释自由文本中的配料和否定条件。')
        known_whole_name = any(norm(query) == norm(e['label']) for e in self.entries.values()) or any(norm(query) == norm(h['name']) for h in self.definition_matches(query))
        if len(re.split(r'[、,，;；\n]', query)) > 1 and not known_whole_name:
            status, selected = 'needs_details', None
            option_entries = {}
            reason = '请一次输入一种食品；复合食品请填写成品名称，并补充配料与加工方式。'
        rows, standards = [], []
        if selected:
            standards = self.standards_for(selected)
            rows = self.inspection_rows_for(selected)
        taxonomies = self.taxonomy_results(query, selected, process, packaging)
        active_ids = {selected['id']} if selected else {c['id'] for c in candidates}
        matches = [h for h in self.definition_matches(query) if h['entry_id'] in active_ids or (not selected and h['entry_id'] is None)]
        unresolved = [h for h in matches if h['link_state'] == 'unmapped']
        if not selected and unresolved and not option_entries:
            status = 'needs_details'
            reason = '已找到产品种类原文，但尚无可靠的检验表关联；这是关联缺口，不表示该食品没有检验项目。'
        if not selected and unresolved and not option_entries:
            sections = {h['section_id'] for h in unresolved}
            option_entries = {e['id']: self.public_entry(e) | {'definition_matches': [], 'reason': '原文关联待补；同分节检验表仅供核对'}
                              for e in self.entries.values() if e['kind'] == 'leaf' and e['section_id'] in sections}
        options = list(option_entries.values()) if not selected else []
        item_state = 'resolved' if selected else 'unmapped' if unresolved else 'ambiguous' if options else 'not_covered'
        links = [dict(path=h['path'], matched_ancestor=h['matched_ancestor'], link_kind=h['link_kind'],
                      definition_evidence=h['evidence'], bridge_evidence=h.get('bridge_evidence'), table_evidence=selected['evidence'])
                 for h in matches if selected and h['entry_id'] == selected['id']]
        result = {'query': query, 'input': {'food': query, 'process': process, 'packaging': packaging, 'notes': notes, 'selected_id': selected_id},
                  'status': status, 'reason': reason, 'questions': questions, 'snapshot': SNAPSHOT, 'engine_version': VERSION,
                  'data_fingerprint': self.fingerprint, 'candidates': candidates,
                  'definition_exclusions': self.definition_exclusions(query),
                  'definition_matches': matches,
                  'inspection_resolution': {'state': item_state, 'automatic': bool(selected and not selected_id),
                      'food': query, 'table': self.public_entry(selected) if selected else None,
                      'links': links, 'options': options, 'unresolved': unresolved},
                  'product_definition': self.definitions[selected['section_id']] if selected else None,
                  'selected': self.public_entry(selected) | {'scope': selected['scope'], 'product_types': selected['product_types'], 'notes': selected['notes']} if selected else None,
                  'taxonomies': taxonomies, 'standards': standards, 'inspection_rows': rows,
                  'limitations': self.metadata()['limitations']}
        national = [s for s in standards if s['national']]
        result['counts'] = {'items': len(rows), 'national': len(national), 'missing': sum(not s['available'] for s in national),
                            'available': sum(s['available'] for s in national), 'other': len(standards) - len(national)}
        if not selected:
            result['counts'] = {key: None for key in result['counts']}
        return result

    def inspection_rows_for(self, entry):
        return [{k: row.get(k) for k in ['id', 'number', 'item_raw', 'basis_raw', 'method_raw', 'basis_codes', 'method_codes', 'review_status', 'continuation_fragments', 'cell_inheritance']} | {
                'evidence': self.evidence(self.guide_file, row['pdf_page'], ' | '.join(str(x or '') for x in row['raw_cells']), row['caption'], printed_page=row['printed_page'])}
                for row in entry['rows']]

    def inspection_preview(self, entry_id):
        entry = self.entries.get(entry_id)
        if not entry or entry['kind'] != 'leaf':
            raise KeyError(entry_id)
        return {'entry': self.public_entry(entry), 'rows': self.inspection_rows_for(entry), 'notes': entry['notes'],
                'note': '此处为分支项目预览；请按产品定义选择适用分支，不合并为全部必检项目。'}

    def standard_detail(self, record_id):
        record = self.by_id.get(record_id)
        if not record:
            raise KeyError(record_id)
        evidence = []
        for ev in record.get('evidence', [])[:20]:
            fid = self.files_by_path.get(str(ev.get('path', '')).replace('\\', '/'))
            if fid:
                f = self.files[fid]
                if ev.get('pdf_page') not in (f.get('unreliable_text_pages') or []):
                    evidence.append(self.evidence(fid, ev.get('pdf_page'), ev.get('excerpt', ''), ev.get('kind', '原文')))
        dependencies = []
        seen = set()
        for edge in self.edges.get(record_id, []):
            key = (edge['target_code'], edge['pdf_page'], edge['relation'])
            if key in seen:
                continue
            seen.add(key)
            fid = self.files_by_path.get(edge['source_path'].replace('\\', '/'))
            dependencies.append({'code': edge['target_code'], 'relation': edge['relation'],
                'evidence': self.evidence(fid, edge['pdf_page'], edge['excerpt'], '正文引用候选'),
                'resolution': self.resolve_standard(edge['target_code'])})
        return {'record': self.record_public(record), 'evidence': evidence, 'references': dependencies,
                'note': '下列是原文引用检索结果，包括规范性引用、历史替代和其他文字引用；尚未逐条确认用途，不自动加入此食品的必用标准。'}
