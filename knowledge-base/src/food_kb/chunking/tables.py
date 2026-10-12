"""Physical Markdown/HTML tables with explicit span and footnote provenance."""
from copy import deepcopy
from html.parser import HTMLParser
import re


def _issue(code, message):
    return {'code': code, 'message': message}


def _split_row(line):
    """Split pipes outside escapes/code spans without losing empty cells."""
    line = line.strip()
    cells, current, ticks = [], [], 0
    index = 0
    while index < len(line):
        char = line[index]
        if char == '\\' and index + 1 < len(line):
            current.extend(line[index:index + 2]); index += 2
            continue
        if char == '`':
            end = index
            while end < len(line) and line[end] == '`':
                end += 1
            count = end - index
            if not ticks:
                ticks = count
            elif count == ticks:
                ticks = 0
            current.extend(line[index:end]); index = end
            continue
        if char == '|' and not ticks:
            cells.append(''.join(current).strip()); current = []
        else:
            current.append(char)
        index += 1
    cells.append(''.join(current).strip())
    if line.startswith('|'):
        cells.pop(0)
    if line.endswith('|') and not ticks:
        # An escaped final pipe belongs to the final cell.
        slashes = len(line[:-1]) - len(line[:-1].rstrip('\\'))
        if not slashes % 2:
            cells.pop()
    return cells


def _is_separator(line):
    cells = _split_row(line)
    return '|' in line and bool(cells) and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells)


class _HTMLTable(HTMLParser):
    def __init__(self, start_line):
        super().__init__(convert_charrefs=True)
        self.source_line_offset = start_line - 1
        self.rows, self.issues = [], []
        self.row, self.cell = None, None
        self.depth, self.in_header = 0, False

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth > 1:
                self.issues.append(_issue('NESTED_TABLE_UNSUPPORTED', 'Nested table markup is preserved but not flattened into the outer table.'))
        if self.depth != 1:
            return
        if tag == 'thead':
            self.in_header = True
        elif tag == 'tr':
            if self.row is not None:
                self.issues.append(_issue('HTML_UNCLOSED_ROW', 'A row began before the preceding row was closed.'))
                self._finish_row()
            self.row = {'header': self.in_header, 'cells': [], 'line_start': self.source_line_offset + self.getpos()[0]}
        elif tag in {'td', 'th'}:
            if self.row is None:
                self.issues.append(_issue('HTML_ORPHAN_CELL', 'A cell has no explicit physical row.'))
                return
            if self.cell is not None:
                self.issues.append(_issue('HTML_UNCLOSED_CELL', 'A cell began before the preceding cell was closed.'))
                self._finish_cell()
            attrs = dict(attrs)
            spans = {}
            for name in ('rowspan', 'colspan'):
                value = attrs.get(name, '1')
                if not re.fullmatch(r'[1-9]\d*', value or '') or int(value) > 1000:
                    self.issues.append(_issue('INVALID_TABLE_SPAN', 'An invalid or excessive table span requires manual review.'))
                    spans[name] = 1
                else:
                    spans[name] = int(value)
            self.cell = {'text_raw': '', 'raw_html': '', 'is_header': tag == 'th',
                         'line_start': self.source_line_offset + self.getpos()[0], **spans}
        elif self.cell is not None:
            self.cell['raw_html'] += self.get_starttag_text()
            if tag == 'br':
                self.cell['text_raw'] += '\n'

    def handle_endtag(self, tag):
        if tag == 'table':
            if self.depth == 1 and self.row is not None:
                self._finish_row()
            self.depth -= 1
            return
        if self.depth != 1:
            return
        if tag in {'td', 'th'}:
            self._finish_cell()
        elif tag == 'tr':
            self._finish_row()
        elif tag == 'thead':
            self.in_header = False
        elif self.cell is not None:
            self.cell['raw_html'] += f'</{tag}>'

    def handle_data(self, text):
        if self.depth == 1 and self.cell is not None:
            self.cell['text_raw'] += text
            self.cell['raw_html'] += text

    def _finish_cell(self):
        if self.cell is not None:
            self.cell['text_raw'] = self.cell['text_raw'].strip()
            self.cell['line_end'] = self.source_line_offset + self.getpos()[0]
            self.row['cells'].append(self.cell)
            self.cell = None

    def _finish_row(self):
        if self.row is not None:
            self._finish_cell()
            self.row['line_end'] = self.source_line_offset + self.getpos()[0]
            self.rows.append(self.row)
            self.row = None


def _caption(lines, index):
    for previous in reversed(lines[:index]):
        text = previous.strip()
        if not text or text.startswith('<!--'):
            continue
        text = re.sub(r'^#{1,6}\s*', '', text).strip('* ')
        match = re.match(r'^表\s*([A-Za-z]?\s*\.?\s*\d+(?:\.\d+)*)\s*(.*)', text)
        if match:
            return text, re.sub(r'\s+', '', match.group(1)), bool(re.search(r'[（(]\s*续\s*[）)]', match.group(2)))
        break
    return None, None, False


def _grid(raw_rows, issues):
    """Only explicit HTML spans create inherited values; blanks stay blank."""
    grid, active = [], {}
    for row_number, raw_row in enumerate(raw_rows, 1):
        slots = {}
        for column, (last_row, origin) in list(active.items()):
            if row_number <= last_row:
                slots[column] = _inherited(origin, column)
            else:
                del active[column]
        column = 1
        for raw in raw_row['cells']:
            while column in slots:
                column += 1
            origin = {'column': column, 'text_raw': raw['text_raw'],
                      'value': raw['text_raw'].replace('\\|', '|'),
                      'source_cell': {'row': row_number, 'column': column},
                      'rowspan': raw.get('rowspan', 1), 'colspan': raw.get('colspan', 1),
                      'inherited': False, 'line_start': raw.get('line_start', raw_row['line_start']),
                      'line_end': raw.get('line_end', raw_row['line_end'])}
            if 'raw_html' in raw:
                origin['raw_html'] = raw['raw_html']
            for covered in range(column, column + origin['colspan']):
                if covered in slots:
                    issues.append(_issue('OVERLAPPING_TABLE_SPAN', 'Explicit cell spans overlap; inspect the original table.'))
                    continue
                slots[covered] = origin if covered == column else _inherited(origin, covered)
                if origin['rowspan'] > 1:
                    active[covered] = (row_number + origin['rowspan'] - 1, origin)
            column += origin['colspan']
        grid.append((raw_row, slots))
    if any(last > len(raw_rows) for last, _ in active.values()):
        issues.append(_issue('SPAN_BEYOND_TABLE', 'A rowspan extends beyond the available physical rows.'))
    width = max((max(slots, default=0) for _, slots in grid), default=0)
    rows = []
    for row_number, (raw, slots) in enumerate(grid, 1):
        for column in range(1, width + 1):
            if column not in slots:
                issues.append(_issue('MISSING_PHYSICAL_CELL', 'A short physical row has an unresolved cell; its value was not inherited.'))
                slots[column] = {'column': column, 'text_raw': '', 'value': '',
                    'source_cell': {'row': row_number, 'column': column}, 'rowspan': 1, 'colspan': 1,
                    'inherited': False, 'missing': True, 'line_start': raw['line_start'], 'line_end': raw['line_end']}
        rows.append({'physical_row_index': row_number, 'cells': [slots[c] for c in sorted(slots)],
                     'line_start': raw['line_start'], 'line_end': raw['line_end']})
    return rows, width


def _inherited(origin, column):
    cell = {**origin, 'column': column, 'text_raw': '', 'inherited': True,
            'source_cell': dict(origin['source_cell']), 'from_cell': dict(origin['source_cell'])}
    cell.pop('raw_html', None)
    return cell


_FOOTNOTE = re.compile(r'^\s*\[\^([^\]]+)\]:\s*(.*)$')
_ADJACENT_NOTE = re.compile(r'^\s*(?:\*\*)?(注(?:\s*\d+)?|[a-zA-Z]|[①-⑳])(?:\*\*)?[：:.\s]\s*(.*)$')


def _footnotes(lines, table, start_line):
    reference_text = (table['caption'] or '') + '\n' + table['raw_markdown']
    references = list(dict.fromkeys(re.findall(r'\[\^([^\]]+)\]', reference_text) +
                                    re.findall(r'<sup>\s*([a-zA-Z0-9]+)\s*</sup>', reference_text)))
    definitions = {}
    for index, line in enumerate(lines):
        match = _FOOTNOTE.match(line)
        if not match:
            continue
        marker, text = match.groups()
        end = index
        while end + 1 < len(lines) and (lines[end + 1].startswith('    ') or lines[end + 1].startswith('\t')):
            end += 1; text += '\n' + lines[end].strip()
        definitions.setdefault(marker, []).append({'marker': marker, 'text': text,
            'line_start': start_line + index, 'line_end': start_line + end, 'relation': 'explicit_reference'})
    footnotes = []
    for marker in references:
        matches = definitions.get(marker, [])
        if len(matches) == 1:
            footnotes.extend(matches)
        else:
            table['issues'].append(_issue('MISSING_FOOTNOTE' if not matches else 'AMBIGUOUS_FOOTNOTE',
                f'Footnote {marker} has no unique definition; the table context is incomplete.'))
    index = table['line_end'] - start_line + 1
    while index < len(lines):
        text = lines[index].strip()
        if not text or text.startswith('<!--'):
            index += 1
            continue
        match = _FOOTNOTE.match(text) or _ADJACENT_NOTE.match(text)
        if not match:
            break
        marker, body = match.groups()
        if not any(note['line_start'] == start_line + index for note in footnotes):
            relation = 'explicit_reference' if marker in references else ('table_note' if marker.startswith('注') else 'unlinked_marker')
            footnotes.append({'marker': marker, 'text': body, 'line_start': start_line + index,
                              'line_end': start_line + index, 'relation': relation})
            if relation == 'unlinked_marker':
                table['issues'].append(_issue('UNLINKED_FOOTNOTE', f'Adjacent marker {marker} has no explicit cell reference; do not infer its applicability.'))
        index += 1
    # Adjacent letter notes are valid definitions for explicit superscript markers.
    resolved = {note['marker'] for note in footnotes if note['relation'] == 'explicit_reference'}
    table['issues'] = [issue for issue in table['issues'] if not (issue['code'] == 'MISSING_FOOTNOTE' and
                      any(issue['message'].startswith(f'Footnote {marker} ') for marker in resolved))]
    table['footnote_references'] = references
    return footnotes


def extract_tables(lines, start_line=1):
    """Return tables, source line bounds, physical spans, headers and notes."""
    if type(start_line) is not int or start_line < 1:
        raise ValueError('start_line must be a positive one-based line offset')
    tables, index, fence = [], 0, None
    while index < len(lines):
        line = lines[index]
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            token = match.group(1)
            fence = None if fence and token[0] == fence[0] and len(token) >= len(fence) else token if fence is None else fence
            index += 1; continue
        if fence:
            index += 1; continue
        begin, issues = index, []
        if re.search(r'<table(?:\s|>)', line, re.I):
            end = index
            while end < len(lines) and not re.search(r'</table\s*>', lines[end], re.I):
                end += 1
            if end == len(lines):
                end -= 1
                issues.append(_issue('HTML_UNCLOSED_TABLE', 'The table has no closing tag; retain the fragment for review.'))
            parser = _HTMLTable(start_line + begin)
            parser.feed('\n'.join(lines[begin:end + 1])); parser.close()
            if parser.row is not None:
                parser._finish_row()
            raw_rows = parser.rows; issues.extend(parser.issues)
            header_count = 0
            for row in raw_rows:
                if row['header'] or row['cells'] and all(cell['is_header'] for cell in row['cells']):
                    header_count += 1
                else:
                    break
            format_name = 'html'
        elif index + 1 < len(lines) and '|' in line and _is_separator(lines[index + 1]):
            end = index + 1
            raw_rows = [{'header': True, 'cells': [{'text_raw': cell} for cell in _split_row(line)],
                         'line_start': start_line + index, 'line_end': start_line + index}]
            while end + 1 < len(lines) and '|' in lines[end + 1] and lines[end + 1].strip() and not lines[end + 1].lstrip().startswith(('<!--', '#')):
                end += 1
                raw_rows.append({'header': False, 'cells': [{'text_raw': cell} for cell in _split_row(lines[end])],
                                 'line_start': start_line + end, 'line_end': start_line + end})
            header_count, format_name = 1, 'markdown'
        else:
            index += 1; continue
        rows, width = _grid(raw_rows, issues)
        if not header_count:
            issues.append(_issue('MISSING_TABLE_HEADER', 'No explicit table header was found; do not invent column meanings.'))
        header_paths = []
        for column in range(width):
            path, seen = [], set()
            for row in rows[:header_count]:
                cell = row['cells'][column]
                origin = tuple(cell['source_cell'].values())
                if origin not in seen and cell['value']:
                    path.append(cell['value']); seen.add(origin)
            header_paths.append(path)
        caption, number, continuation = _caption(lines, begin)
        data_rows = rows[header_count:]
        for row_index, row in enumerate(data_rows, 1):
            row['row_index'] = row_index
        table = {'key': f'table-{len(tables) + 1}', 'caption': caption, 'table_number': number,
                 'line_start': start_line + begin, 'line_end': start_line + end,
                 'headers': [path[-1] if path else '' for path in header_paths], 'header_paths': header_paths,
                 'header_rows': rows[:header_count], 'rows': data_rows,
                 'raw_markdown': '\n'.join(lines[begin:end + 1]), 'issues': issues,
                 'fragment': {'format': format_name, 'explicit_continuation': continuation}}
        table['footnotes'] = _footnotes(lines, table, start_line)
        tables.append(table)
        index = end + 1
    return tables


def merge_continuations(tables, page_for_line=None):
    """Merge only a uniquely identified numbered continuation with equal headers."""
    def page(line):
        return page_for_line(line) if callable(page_for_line) else page_for_line.get(line) if page_for_line else None

    result = []
    for original in tables:
        table = deepcopy(original)
        fragment = {'key': table['key'], 'caption': table['caption'], 'line_start': table['line_start'],
                    'line_end': table['line_end'], 'page_start': page(table['line_start']),
                    'page_end': page(table['line_end']), 'header_rows': deepcopy(table['header_rows']),
                    'raw_markdown': table['raw_markdown'], **table['fragment']}
        table['physical_fragments'] = [fragment]
        for row in table['rows']:
            row['fragment_key'] = table['key']; row['pdf_page'] = page(row['line_start'])
            for cell in row['cells']:
                cell['source_cell']['fragment_key'] = table['key']
                if cell.get('from_cell'):
                    cell['from_cell']['fragment_key'] = table['key']
        if not table['fragment']['explicit_continuation']:
            result.append(table); continue
        matches = [candidate for candidate in result if table['table_number'] is not None and
                   candidate['table_number'] == table['table_number']]
        if len(matches) != 1 or matches[0]['fragment']['explicit_continuation']:
            table['issues'].append(_issue('AMBIGUOUS_CONTINUATION', 'A numbered continuation has no unique preceding base table; fragments remain separate.'))
            result.append(table); continue
        target = matches[0]
        normalize = lambda paths: [[re.sub(r'\s+', ' ', value).strip() for value in path] for path in paths]
        if not any(target['header_paths']) or normalize(target['header_paths']) != normalize(table['header_paths']):
            table['issues'].append(_issue('CONTINUATION_HEADER_MISMATCH', 'Continuation headers differ or are missing; adjacent food rows were not merged.'))
            result.append(table); continue
        target['physical_fragments'].append(fragment)
        for row in table['rows']:
            if row['row_index'] == 1 and [cell['value'] for cell in row['cells']] == table['headers']:
                target.setdefault('repeated_header_rows', []).append(row)
                continue
            row['row_index'] = len(target['rows']) + 1
            target['rows'].append(row)
        target['line_end'] = table['line_end']
        target['raw_markdown'] += '\n\n' + table['raw_markdown']
        target['issues'].extend(table['issues'])
        target['footnotes'].extend(note for note in table['footnotes'] if note not in target['footnotes'])
        target['footnote_references'] = list(dict.fromkeys(target['footnote_references'] + table['footnote_references']))
        unique_notes = {}
        for note in target['footnotes']:
            if note['marker'] in target['footnote_references']:
                note['relation'] = 'explicit_reference'
            unique_notes[note['marker'], note['text'], note['line_start']] = note
        target['footnotes'] = list(unique_notes.values())
        resolved = {note['marker'] for note in target['footnotes'] if note['relation'] == 'explicit_reference'}
        target['issues'] = [issue for issue in target['issues'] if not (
            issue['code'] in {'MISSING_FOOTNOTE', 'UNLINKED_FOOTNOTE'} and any(
                issue['message'].startswith((f'Footnote {marker} ', f'Adjacent marker {marker} ')) for marker in resolved))]
    return result
