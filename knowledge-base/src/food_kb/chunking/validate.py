"""Source and context checks for candidate chunks; never grant execution approval."""
import re

from .context import expand_context


def validate_bundle(bundle):
    issues = []

    def issue(code, message, *, chunk_id=None, block_id=None):
        item = {'code': code, 'message': message}
        if chunk_id is not None:
            item['chunk_id'] = chunk_id
        if block_id is not None:
            item['block_id'] = block_id
        if item not in issues:
            issues.append(item)

    source = bundle.get('source', {})
    page_count = source.get('page_count'); line_count = source.get('markdown_line_count')
    bounds_valid = all(type(n) is int and n > 0 for n in (page_count, line_count))
    if not bounds_valid:
        issue('SOURCE_BOUNDS', 'Source page and Markdown line counts must be positive integers.')
    parse_id = bundle.get('parse_id')
    if not parse_id:
        issue('PARSE_ID_MISSING', 'A source parse identity is required.')

    def index(items, id_field, code):
        result = {}
        for item in items:
            identifier = item.get(id_field)
            if not identifier or identifier in result:
                issue(code, 'Source identities must be present and unique.')
            else:
                result[identifier] = item
        return result

    blocks = index(bundle.get('blocks', []), 'block_id', 'BLOCK_ID_INVALID')
    chunks = index(bundle.get('chunks', []), 'chunk_id', 'CHUNK_ID_INVALID')
    links = bundle.get('links', [])

    def location(entity, **identity):
        loc = entity.get('source', {})
        for origin in (entity, loc, entity.get('metadata', {})):
            if origin.get('parse_id') is not None and origin['parse_id'] != parse_id:
                issue('CROSS_PARSE', 'An entity refers to a different source parse.', **identity)
        for name in ('asset_id', 'markdown_asset_id'):
            if loc.get(name) is not None and loc[name] != source.get(name):
                issue('CROSS_SOURCE', 'A location refers to a different source asset.', **identity)
        if not bounds_valid:
            return set()
        start = loc.get('line_start'); end = loc.get('line_end')
        ranges = loc.get('line_ranges'); pages = loc.get('pdf_pages')
        valid = type(start) is int and type(end) is int and 1 <= start <= end <= line_count
        if not isinstance(ranges, list) or not ranges:
            valid = False
            ranges = []
        covered = set()
        for pair in ranges:
            if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and
                    all(type(n) is int for n in pair) and 1 <= pair[0] <= pair[1] <= line_count):
                valid = False
            else:
                covered.update(range(pair[0], pair[1] + 1))
        if covered and (min(covered) != start or max(covered) != end):
            valid = False
        if not (isinstance(pages, list) and pages and
                all(type(n) is int and 1 <= n <= page_count for n in pages) and len(set(pages)) == len(pages)):
            valid = False
        if not valid:
            issue('LOCATION_INVALID', 'Source line ranges or actual PDF pages are missing or out of bounds.', **identity)
        return covered

    for block_id, block in blocks.items():
        location(block, block_id=block_id)
        parent = block.get('parent_id')
        if parent is not None and parent not in blocks:
            issue('PARENT_MISSING', 'Structural parent does not exist.', block_id=block_id)
        path = set(); current = block_id
        while current in blocks:
            if current in path:
                issue('PARENT_CYCLE', 'Structural parent chain contains a cycle.', block_id=block_id)
                break
            path.add(current); current = blocks[current].get('parent_id')
    for link in links:
        chunk_id = link.get('chunk_id'); target = link.get('target_block_id')
        if chunk_id not in chunks or target not in blocks:
            issue('CONTEXT_ORPHAN', 'Context link has a missing source chunk or target block.',
                  chunk_id=chunk_id, block_id=target)
        if link.get('parse_id') is not None and link['parse_id'] != parse_id:
            issue('CROSS_PARSE', 'Context link belongs to a different parse.', chunk_id=chunk_id)
    actual_covered = set()
    for chunk_id, chunk in chunks.items():
        actual_covered.update(location(chunk, chunk_id=chunk_id))
        if chunk.get('executable') is not False:
            issue('UNREVIEWED_EXECUTABLE', 'KB-06 chunks must remain non-executable candidates.', chunk_id=chunk_id)
        quality = chunk.get('quality', {})
        if quality.get('status') in {'approved', 'verified', 'executable'}:
            issue('UNREVIEWED_APPROVAL', 'Chunking cannot grant knowledge approval.', chunk_id=chunk_id)
        block_ids = chunk.get('block_ids', [])
        if not block_ids or any(block_id not in blocks for block_id in block_ids):
            issue('CHUNK_SOURCE_MISSING', 'Chunk must reference existing source blocks.', chunk_id=chunk_id)
        parent = chunk.get('parent_block_id')
        if parent is not None and parent not in blocks:
            issue('PARENT_MISSING', 'Chunk structural parent does not exist.', chunk_id=chunk_id)
        text = chunk.get('raw_markdown', '')
        if not isinstance(text, str):
            issue('CHUNK_TEXT_INVALID', 'Original Markdown must be a string.', chunk_id=chunk_id)
            continue
        table = chunk.get('table') or {}
        if chunk.get('kind') in {'table_row', 'row'} or 'row' in table:
            headers = table.get('headers', [])
            if not isinstance(headers, list) or not headers or not any(str(x).strip() for x in headers):
                issue('TABLE_HEADER_MISSING', 'Table row is missing its original header context.', chunk_id=chunk_id)
            row = table.get('row')
            cells = row.get('cells', []) if isinstance(row, dict) else row if isinstance(row, list) else []
            values = [str(cell.get('value', cell.get('text_raw', '')) if isinstance(cell, dict) else cell).strip()
                      for cell in cells]
            if values and headers and values == [str(x).strip() for x in headers]:
                issue('DUPLICATE_HEADER_ROW', 'A repeated table header cannot be a data row.', chunk_id=chunk_id)
            for index, value in enumerate(values):
                if re.search(r'(?:<=|>=|[≤≥≦≧<>＜＞])\s*$', value):
                    following = values[index + 1] if index + 1 < len(values) else ''
                    if not re.match(r'\s*[-+−]?\s*(?:\d|\.\d)', following):
                        issue('COMPARISON_VALUE_MISSING', 'Table comparison lost its adjacent numerical value.', chunk_id=chunk_id)
        footnotes = set(re.findall(r'\[\^([^\]]+)\](?!\s*:)', text))
        available = set()
        for link in links:
            if link.get('chunk_id') != chunk_id or link.get('role') != 'footnote':
                continue
            block = blocks.get(link.get('target_block_id'), {})
            available.update(re.findall(r'\[\^([^\]]+)\]\s*:', block.get('text_raw', '')))
            label = block.get('metadata', {}).get('footnote_label')
            if label is not None:
                available.add(str(label))
        for label in sorted(footnotes - available):
            issue('FOOTNOTE_MISSING', f'Footnote [{label}] lacks a matching source context link.', chunk_id=chunk_id)
        if text.count('$$') % 2:
            issue('FORMULA_UNBALANCED', 'Display formula delimiters are unbalanced.', chunk_id=chunk_id)
        if re.search(r'\\tag\s*\{[^}]+\}', text):
            context = expand_context(bundle, chunk_id, max_blocks=len(blocks),
                                     max_chars=sum(len(b.get('text_raw', '')) for b in blocks.values()))
            explained = text + '\n' + '\n'.join(b['text_raw'] for b in context['blocks'])
            if '式中' not in explained:
                issue('FORMULA_VARIABLES_MISSING', 'Numbered formula has lost its variable definitions.', chunk_id=chunk_id)
        if re.fullmatch(r'[\s$|:：<>≤≥=≦≧]+', text) and re.search(r'[<>≤≥=≦≧]', text):
            issue('COMPARISON_VALUE_MISSING', 'A comparison symbol has no retained numerical value.', chunk_id=chunk_id)
    coverage = bundle.get('coverage', {})
    expected = coverage.get('expected_lines', []); declared = coverage.get('covered_lines', [])
    if not bounds_valid or any(type(n) is not int or not 1 <= n <= line_count for n in expected + declared):
        issue('COVERAGE_INVALID', 'Coverage ledger contains invalid Markdown line numbers.')
    else:
        missing = set(expected) - (set(declared) & actual_covered)
        if missing:
            issue('COVERAGE_MISSING', 'Source lines have no retained chunk coverage: ' + ', '.join(map(str, sorted(missing))))
        if set(declared) - actual_covered:
            issue('COVERAGE_UNSUPPORTED', 'Declared coverage includes lines absent from chunk source ranges.')
    return {'passed': not issues, 'issues': issues,
            'counts': {'blocks': len(blocks), 'chunks': len(chunks), 'links': len(links),
                       'expected_lines': len(set(expected)), 'covered_lines': len(actual_covered)}}
