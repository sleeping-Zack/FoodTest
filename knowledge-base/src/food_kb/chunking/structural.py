"""Sections first, logical table rows second; preserve whole formula sections."""
import re

from ..common import digest, identity
from .tables import extract_tables, merge_continuations
from .validate import validate_bundle


STRATEGY_VERSION = 'structure-md-1'
STRATEGY_CONFIG = {'boundary': 'direct-heading-content', 'tables': 'explicit-spans-only',
                   'formulas': 'whole-clause-with-variables', 'max_chars_warning': 12000}


def _ranges(numbers):
    result = []
    for n in sorted(set(numbers)):
        if result and result[-1][1] == n - 1:
            result[-1][1] = n
        else:
            result.append([n, n])
    return result


def clause_references(text):
    # Formula labels (A.1) and \tag{A.1} are not clause references.
    text = re.sub(r'^#{1,6}\s+A\.\d+(?:\.\d+)*\s*', '', text, flags=re.M)
    text = re.sub(r'\\tag\{[^}]+\}|[（(]\s*A\.\d+(?:\.\d+)*\s*[）)]', '', text)
    return list(dict.fromkeys(re.findall(r'(?<![\w.])A\.\d+(?:\.\d+)*(?![\w.])', text)))


def build_bundle(manual, parse_id, strategy_version=STRATEGY_VERSION):
    lines = manual['lines']; mapping = manual['mapping']; page_for_line = manual['page_for_line']
    asset = mapping['source_sha256']; md_asset = mapping['markdown_sha256']
    config_hash = digest(STRATEGY_CONFIG)
    run_id = identity('chunk-run', parse_id, strategy_version, config_hash)
    meaningful = {n for n, line in enumerate(lines, 1) if n in page_for_line and
                  line.strip() and not line.lstrip().startswith('<!--')}
    blocks = []; chunks = []; links = []; by_key = {}; occupied = set()
    headings = mapping['headings']; children = {h['parent_id'] for h in headings}

    def source(numbers):
        numbers = sorted(set(numbers)); pages = sorted({page_for_line[n] for n in numbers})
        return {'asset_id': asset, 'markdown_asset_id': md_asset, 'parse_id': parse_id,
                'line_start': numbers[0], 'line_end': numbers[-1], 'line_ranges': _ranges(numbers),
                'pdf_pages': pages, 'printed_pages': [mapping['pages'][p - 1]['printed_page'] for p in pages],
                'bbox': None, 'locator_quality': 'manual_page_and_line'}

    def block(key, title, kind, numbers, parent_key=None, metadata=None):
        numbers = sorted(set(numbers) & meaningful)
        if not numbers:
            return None
        raw_lines = []
        for index, n in enumerate(numbers):
            if index and n > numbers[index - 1] + 1 and any(not line.strip() for line in lines[numbers[index - 1]:n - 1]):
                raw_lines.append('')
            raw_lines.append(lines[n - 1])
        value = {'block_id': identity('manual-block', parse_id, key), 'key': key,
                 'parent_id': by_key[parent_key]['block_id'] if parent_key else None,
                 'kind': kind, 'title': title, 'text_raw': '\n'.join(raw_lines),
                 'source': source(numbers), 'metadata': metadata or {}}
        blocks.append(value); by_key[key] = value; occupied.update(numbers)
        return value

    tables = merge_continuations(extract_tables(lines), page_for_line)
    table_lines = set()
    for table in tables:
        for fragment in table['physical_fragments']:
            table_lines.update(range(fragment['line_start'], fragment['line_end'] + 1))
    for h in headings:
        numbers = set(range(h['markdown_start_line'], h['markdown_content_end_line'] + 1)) - table_lines
        block(h['id'], h['title'], 'section', numbers, h['parent_id'],
              {'heading_level': h['heading_level'], 'navigation_only': h['id'] in children})
    # Cover matter and repeated page titles remain visible, but are not retrieval candidates.
    leftovers = sorted(meaningful - occupied - table_lines); groups = []
    for n in leftovers:
        if groups and page_for_line[groups[-1][-1]] == page_for_line[n] and not any(
                m in occupied or m in table_lines for m in range(groups[-1][-1] + 1, n)):
            groups[-1].append(n)
        else:
            groups.append([n])
    for index, numbers in enumerate(groups, 1):
        block(f'layout-{index}', '封面与版面信息', 'layout', numbers, headings[0]['id'])
    table_by_block = {}; row_by_block = {}; footnote_links = {}
    for table in tables:
        owner = next(h for h in reversed(headings) if h['markdown_start_line'] < table['line_start'])
        numbers = [n for fragment in table['physical_fragments']
                   for n in range(fragment['line_start'], fragment['line_end'] + 1)]
        tb = block(table['key'], table['caption'] or table['key'], 'table', numbers, owner['id'],
                   {'issues': table['issues']})
        table_by_block[tb['block_id']] = table
        notes = []; note_blocks = []
        for note in table['footnotes']:
            fb = block(f"{table['key']}:footnote:{note['line_start']}", f"表注 {note['marker']}", 'footnote',
                       range(note['line_start'], note['line_end'] + 1), table['key'],
                       {'footnote_label': note['marker'], 'relation': note['relation']})
            if note['relation'] != 'unlinked_marker':
                notes.append(fb['block_id'])
                note_blocks.append((note, fb['block_id']))
        for row in table['rows']:
            rb = block(f"{table['key']}:row:{row['row_index']}", f"{table['caption']} · {row['cells'][0]['value']}",
                       'table_row', range(row['line_start'], row['line_end'] + 1), table['key'])
            row_by_block[rb['block_id']] = (table, row)
            row_text = '\n'.join(c.get('raw_html', c['value']) for c in row['cells'])
            row_text += '\n' + '\n'.join(c.get('raw_html', c['value']) for header in table['header_rows'] for c in header['cells'])
            footnote_links[rb['block_id']] = [bid for note, bid in note_blocks if note['relation'] == 'table_note' or
                f"[^{note['marker']}]" in row_text or f"[^{note['marker']}]" in (table['caption'] or '') or
                re.search(r'<sup>\s*' + re.escape(note['marker']) + r'\s*</sup>', row_text)]
        footnote_links[tb['block_id']] = notes
    definitions = {}
    for n, line in enumerate(lines, 1):
        match = re.match(r'^\s*\[\^([^\]]+)\]:', line)
        if match:
            definitions.setdefault(match[1], []).append(n)
    footnote_by_label = {}
    for label, starts in definitions.items():
        if len(starts) != 1:
            continue
        start = starts[0]
        existing = next((b for b in blocks if b['kind'] == 'footnote' and b['source']['line_start'] == start), None)
        if existing is None:
            owner = next(h for h in reversed(headings) if h['markdown_start_line'] < start)
            end = start
            while end < len(lines) and lines[end].startswith(('    ', '\t')):
                end += 1
            existing = block(f'footnote:{label}:{start}', f'脚注 {label}', 'footnote', range(start, end + 1),
                             owner['id'], {'footnote_label': label})
        footnote_by_label[label] = existing['block_id']
    index = {b['block_id']: b for b in blocks}

    def ancestors(b):
        result = []
        while b:
            result.append(b); b = index.get(b['parent_id'])
        return list(reversed(result))

    for b in blocks:
        chain = ancestors(b); title_path = [p['title'] for p in chain]
        issues = list(b['metadata'].get('issues', []))
        is_nav = b['metadata'].get('navigation_only') and len(b['text_raw'].splitlines()) == 1
        retrieve = b['kind'] not in {'layout', 'table', 'footnote'} and b['key'] not in {'document', 'preface'} and not is_nav
        text = b['text_raw']; table_data = None
        if b['kind'] == 'table_row':
            table, row = row_by_block[b['block_id']]
            values = [c['value'] for c in row['cells']]
            table_data = {'caption': table['caption'], 'headers': table['headers'], 'header_paths': table['header_paths'],
                          **row, 'row': values}
            # Field hints are literal source strings; no normalization or rule approval.
            project = values[0]; comparator = re.search(r'[≤≥<>]', project)
            table_data.update(project=project, comparator=comparator[0] if comparator else None,
                              value=values[1] if len(values) > 1 else None,
                              unit='mg/kg' if 'mg/kg' in project else '%' if '%' in project else None,
                              method=values[2] if len(values) > 2 else None)
            text = '\n'.join(f"{' / '.join(path)}：{value}" for path, value in zip(table['header_paths'], values))
            table_data['footnotes'] = [index[n]['text_raw'] for n in footnote_links[b['block_id']]]
            if table_data['footnotes']:
                text += '\n' + '\n'.join(table_data['footnotes'])
            issues.extend(table['issues'])
        elif b['kind'] == 'table':
            table_data = table_by_block[b['block_id']]
        if len(text) > STRATEGY_CONFIG['max_chars_warning']:
            issues.append({'code': 'OVERSIZE_ATOMIC_SECTION', 'message': '完整条款超过建议长度，保留原文并标记待复核。'})
        scope = by_key.get('1')
        retrieval_text = '\n'.join([' > '.join(title_path), text] + ([scope['text_raw']] if scope and retrieve else []))
        content_hash = digest({'raw': b['text_raw'], 'retrieval': retrieval_text, 'table': table_data})
        chunk = {'chunk_id': identity('chunk', parse_id, b['key'], strategy_version, config_hash, content_hash),
                 'anchor': b['key'], 'kind': 'navigation' if is_nav else b['kind'], 'title': b['title'],
                 'heading_path': title_path, 'parent_block_id': b['parent_id'], 'block_ids': [b['block_id']],
                 'raw_markdown': b['text_raw'], 'retrieval_text': retrieval_text, 'source': b['source'],
                 'content_hash': content_hash, 'retrieval_enabled': bool(retrieve), 'executable': False,
                 'quality': {'status': 'context_incomplete' if issues else 'complete_for_retrieval', 'issues': issues}}
        if table_data:
            chunk['table'] = table_data
        chunks.append(chunk)

        def link(target, role):
            if target:
                item = {'chunk_id': chunk['chunk_id'], 'target_block_id': target, 'role': role}
                if item not in links:
                    links.append(item)

        link(b['parent_id'], 'parent')
        if retrieve and scope and b['key'] != '1':
            link(scope['block_id'], 'scope')
        method = next((p for p in reversed(chain) if re.fullmatch(r'A\.\d+(?:\.\d+)?', p['key']) and
                       ('法' in p['title'] or '的测定' in p['title'] or '鉴别试验' in p['title'])), None)
        if method:
            link(method['block_id'], 'method')
            for general in ('A.1', 'A.2'):
                link(by_key.get(general, {}).get('block_id'), 'reference')
        if b['kind'] == 'table_row':
            link(b['parent_id'], 'table')
        for target in footnote_links.get(b['block_id'], []):
            link(target, 'footnote')
        for label in re.findall(r'\[\^([^\]]+)\](?!\s*:)', b['text_raw'] + '\n' + text):
            link(footnote_by_label.get(label), 'footnote')
        references = clause_references(text)
        for key in references:
            if key in by_key and key != b['key']:
                link(by_key[key]['block_id'], 'reference')
    # A method may explicitly call another method (for example water correction).
    # Record that dependency for the originating row/subsection, with cycle deduplication.
    descendants = {}
    for b in blocks:
        for ancestor in ancestors(b):
            descendants.setdefault(ancestor['block_id'], []).append(b)
    for chunk in chunks:
        pending = [x['target_block_id'] for x in links if x['chunk_id'] == chunk['chunk_id'] and
                   x['role'] in {'method', 'submethod', 'reference'}]
        visited = set()
        while pending:
            target = pending.pop()
            if target in visited:
                continue
            visited.add(target)
            keys = []
            if re.fullmatch(r'A\.\d+(?:\.\d+)?', index[target]['key']):
                keys.extend(['A.1', 'A.2'])
            for item in descendants.get(target, []):
                keys.extend(clause_references(item['text_raw']))
            for key in keys:
                if key not in by_key:
                    continue
                block_id = by_key[key]['block_id']
                if block_id not in visited:
                    pending.append(block_id)
                link_value = {'chunk_id': chunk['chunk_id'], 'target_block_id': block_id, 'role': 'reference'}
                if link_value not in links and block_id not in chunk['block_ids']:
                    links.append(link_value)
    bundle = {'format_version': 1, 'run_id': run_id, 'parse_id': parse_id, 'strategy_version': strategy_version,
              'strategy_config': STRATEGY_CONFIG, 'config_hash': config_hash,
              'source': {'asset_id': asset, 'markdown_asset_id': md_asset, 'page_count': manual['page_count'],
                         'markdown_line_count': len(lines), 'markdown_file': mapping['markdown'],
                         'title': headings[0]['title'], 'source_pdf': 'source.pdf',
                         'page_map': mapping['pages'], 'business_review_status': 'not_expert_approved'},
              'blocks': blocks, 'chunks': chunks, 'links': links,
              'coverage': {'expected_lines': sorted(meaningful), 'covered_lines': sorted(occupied)}}
    bundle['validation'] = validate_bundle(bundle)
    for chunk in chunks:
        if chunk['quality']['issues']:
            bundle['validation']['issues'].append({'code': 'CANDIDATE_CONTEXT_INCOMPLETE',
                'chunk_id': chunk['chunk_id'], 'message': 'Source structure has unresolved issues; retain for review.'})
    bundle['validation']['passed'] = not bundle['validation']['issues']
    for issue in bundle['validation']['issues']:
        for chunk in chunks:
            if issue.get('chunk_id') == chunk['chunk_id']:
                chunk['quality']['status'] = 'context_incomplete'
                chunk['quality']['issues'].append(issue)
                chunk['retrieval_enabled'] = False
    return bundle
