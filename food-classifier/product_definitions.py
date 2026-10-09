"""Source-preserving index of 产品种类. Relationships are retrieval candidates.

Only explicit list constructions create names; ingredients in a definition do not.
Original clauses (including exclusions) remain authoritative over extracted names.
"""
import re
import unicodedata


def compact(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def without_folios(value):
    return '\n'.join(x for x in value.splitlines() if not re.fullmatch(r'\s*\d+\s*', x))


def at_top_level(text, position):
    depth = 0
    for char in text[:position]:
        depth += int(char in '（([') - int(char in '）)]')
    return depth == 0


def list_names(text, protected=()):
    """Split only top-level list separators, never commas inside parentheses."""
    text = text.strip('：:。；;，, ')
    parts, start, depth = [], 0, 0
    protected_spans = [(m.start(), m.end()) for term in protected for m in re.finditer(re.escape(term), text)]
    for i, char in enumerate(text):
        if char in '（([':
            depth += 1
        elif char in '）)]':
            depth = max(0, depth - 1)
        elif depth == 0 and char in '、和及，,':
            if any(left <= i < right for left, right in protected_spans):
                continue
            if char == '和' and i > 0 and text[i - 1] in '调饱':
                continue
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    names = []
    for part in parts:
        depth = 0
        for index, char in enumerate(part):
            depth += int(char in '（([') - int(char in '）)]')
            if char == '等' and depth == 0:
                part = part[:index]
                break
        part = re.sub(r'^(?:以及|包括|包含|如[:：]?)', '', part).strip()
        part = re.sub(r'(?:两个细类|以)$', '', part)
        if not 2 <= len(part) <= 32:
            continue
        if part == '其他' or re.search(r'为|是指|可直接|加工的|加工而成|原料|工艺流程|制成的|用于|生产的|添加了|不包括|除外|不在|主要|其中|\d|[:：。；;]', part):
            continue
        if part.count('（') != part.count('）') or part.count('(') != part.count(')'):
            continue
        # A parenthetical list is kept whole. No inferred synonyms are generated.
        names.append(part)
    return list(dict.fromkeys(names))


def build_definitions(guide, pages):
    result = []
    classification_names = {str(row[2]).replace('\n', '') for t in guide['tables'] if t['kind']=='other_table' and '所属' in t['caption'] for row in t['cells'][1:] if len(row)>2 and row[2]}
    for section in guide['sections']:
        relevant = pages[section['pdf_page'] - 1:section['pdf_end_page']]
        chars, page_numbers = [], []
        for page in relevant:
            text = compact(without_folios(page['text']))
            chars.append(text)
            page_numbers.extend([page['pdf_page']] * len(text))
        full = ''.join(chars)
        raw = section['product_types']
        start = full.find(compact(without_folios(raw)))
        if start < 0:
            raise ValueError(f"{section['id']} 产品种类全文无法与 PDF 页缓存对齐")
        end = start + len(compact(without_folios(raw)))
        # Drop standalone folio numbers only in the reading/parse view, not original.
        lines = [x.strip() for x in raw.splitlines() if x.strip() and not re.fullmatch(r'\d+', x.strip())]
        paragraphs, pending = [], ''
        for line in lines:
            if re.match(r'^\d+(?:\.\d+)+\s+', line):
                if pending:
                    paragraphs.append(pending)
                paragraphs.append(line)
                pending = ''
                continue
            pending += line
            if line.endswith(('。', '：', '；')):
                paragraphs.append(pending)
                pending = ''
        if pending:
            paragraphs.append(pending)
        # Only definition prose is parsed; embedded fruit/vegetable tables retain
        # the separate existing, table-cell-based classification path.
        prose = re.split(r'表\s*\d+[-－]\d+\s*具体抽检', ''.join(lines), maxsplit=1)[0]
        readable = '\n'.join(paragraphs)
        statements = []
        cursor = start
        for i, paragraph in enumerate(paragraphs):
            needle = compact(paragraph)
            hit = full.find(needle, cursor, end)
            # Cross-page paragraphs include folios in the original stream.
            # Locate the first and last 24 characters and explicitly retain spans.
            if hit < 0:
                hit = full.find(needle[:24], cursor, end)
            finish = full.find(needle[-24:], max(cursor, hit), end) if hit >= 0 else -1
            if hit < 0 or finish < 0:
                # A table may be one long block; exact full-section span remains.
                first, last = page_numbers[start], page_numbers[end - 1]
                location = 'section_span'
            else:
                first = page_numbers[hit]
                last = page_numbers[finish + len(needle[-24:]) - 1]
                cursor = finish + len(needle[-24:])
                location = 'paragraph_span'
            statements.append({'id': f"{section['id']}-P{i+1:02d}", 'quote': paragraph,
                'pdf_page': first, 'pdf_end_page': last, 'location_status': location,
                'is_condition': bool(re.search(r'不包括|除外|不在|仅抽|仅限|适用于|大于|小于|以上|以下|不得', paragraph))})

        relationships = []
        exclusions = []
        current = section['name']
        parents = {current: []}
        heading_paths = {}
        for statement in statements:
            paragraph = statement['quote']
            if compact(paragraph) not in compact(prose):
                continue
            heading = re.match(r'^(\d+(?:\.\d+)+)\s+(.+)$', paragraph)
            if heading and len(heading[2]) <= 32 and not re.search(r'包括|原料|工艺|为|是', heading[2]):
                current = heading[2]
                parent_number = heading[1].rsplit('.', 1)[0]
                parents[current] = heading_paths.get(parent_number, [section['name']])
                heading_paths[heading[1]] = parents[current] + [current]
                relationships.append({'name': current, 'path': parents[current] + [current],
                    'statement_id': statement['id'], 'relation': '原文小标题'})
                continue
            if heading:
                # A subheading and its prose can share one PDF line. Preserve it
                # as a clause, never turn the whole definition into a food name.
                paragraph = re.sub(r'^\d+(?:\.\d+)+\s+', '', paragraph)
            for sentence in re.split(r'[。；]', paragraph):
                sentence = sentence.strip()
                if not sentence:
                    continue
                operators = [m for m in re.finditer(r'主要包括|包括|(?<!成)(?<!组)分为|是指|是以|是在|是由|是|指|[:：]', sentence) if at_top_level(sentence, m.start())]
                subject = sentence[:operators[0].start()] if operators else ''
                if 2 <= len(subject) <= 40 and not re.search(r'[，,。；]|^(?:不|本细则|其中|如|产品|主要|按)', subject):
                    current = re.sub(r'[（(]GB[^）)]*[）)]', '', subject)
                    parents.setdefault(current, [section['name']] if current != section['name'] else [])
                excluded = re.search(r'不包括(.+)', sentence)
                if excluded:
                    exclusions.append({'quote': sentence, 'statement_id': statement['id'], 'scope_label': current,
                                       'names': list_names(excluded[1])})
                if re.search(r'除外|不在.*范围|仅抽|仅限', sentence):
                    exclusions.append({'quote': sentence, 'statement_id': statement['id'], 'scope_label': current, 'names': []})
                # Clip exclusion tail before collecting any positive names.
                positive = re.split(r'不包括|不在|除外', sentence)[0].rstrip('，,（(')
                markers = [m for m in re.finditer(r'包括|(?<!成)(?<!组)分为|(?:^|[，,])如[:：]?', positive) if at_top_level(positive, m.start())]
                if re.search(r'不在.*范围|除外|仅抽|仅限', sentence):
                    continue
                if re.search(r'可食用物料|附加于面饼|用于调味和提供营养', sentence):
                    continue
                if not markers:
                    if not re.fullmatch(r'[^，,。；:：是为经]+[、][^，,。；:：是为经]+', positive):
                        continue
                    tail = positive
                else:
                    marker = markers[-1]
                    tail = positive[marker.end():]
                # Reject lists of ingredients, processes, forms or conditional text.
                if re.search(r'为(?:主要)?原料|为辅料|经.*加工|制成的|工艺(?:加工|生产|制成)|等[）)]?(?:为|作为)|等.*原料', tail):
                    continue
                for name in list_names(tail, classification_names):
                    if name == current:
                        continue
                    path = list(dict.fromkeys(parents.get(current, [section['name']]) + [current, name]))
                    parents.setdefault(name, path[:-1])
                    relationships.append({'name': name, 'path': path, 'statement_id': statement['id'],
                                          'relation': '原文列举'})
        result.append({'section_id': section['id'], 'chapter_id': section['chapter_id'],
            'name': section['name'], 'heading': section['heading'], 'scope': section['scope'],
            'raw_text': raw, 'readable_text': readable, 'pdf_page': page_numbers[start],
            'pdf_end_page': page_numbers[end - 1], 'statements': statements,
            'relationships': relationships, 'exclusions': exclusions,
            'extraction_note': '全文逐节提取；品种名称仅从明确列举/小标题中保守提取，属于检索候选，未完成专家逐条审定。原料、工艺、比例、人群及排除条件须按原句核对。'})
    return result
