"""Read the explicit Markdown/page-map contract without OCR or PDF extraction."""
from pathlib import Path
import re

import pymupdf

from ..common import KBError, file_hash, read_json


def load_manual(directory):
    directory = Path(directory).resolve()
    hashes = {}

    def local(name):
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            raise KBError('MANUAL_SOURCE_MISSING', 'A source package file is missing or outside its directory.')
        hashes.setdefault(path.relative_to(directory).as_posix(), file_hash(path))
        return path

    mapping = read_json(local('source-map.json'))
    source = read_json(local('source.json'))
    md = local(mapping['markdown']); pdf = local(source['preserved_source'])
    if file_hash(md) != mapping['markdown_sha256'] or file_hash(pdf) != mapping['source_sha256'] or source['sha256'] != mapping['source_sha256']:
        raise KBError('MANUAL_HASH_MISMATCH', 'Markdown or PDF differs from its explicit source map.')
    lines = md.read_text(encoding='utf-8-sig').splitlines()
    with pymupdf.open(pdf) as document:
        page_count = len(document)
    if page_count != source['page_count'] or len(mapping['pages']) != page_count:
        raise KBError('MANUAL_PAGE_MISMATCH', 'PDF and source-map page counts differ.')
    pages = {}; files = [md, pdf, local('source-map.json'), local('source.json')]
    last_end = 0
    for number, page in enumerate(mapping['pages'], 1):
        start, end = page['markdown_start_line'], page['markdown_end_line']
        if (page['pdf_page'] != number or not 1 <= start <= end <= len(lines) or
                number > 1 and start != last_end + 1 or
                not re.search(rf'pdf_page={number}(?:;|\s)', lines[start - 1])):
            raise KBError('MANUAL_PAGE_MISMATCH', 'Page markers and one-based source ranges must agree.')
        for line in range(start, end + 1):
            pages[line] = number
        files.append(local(page['image'])); last_end = end
    if last_end != len(lines):
        raise KBError('MANUAL_PAGE_MISMATCH', 'The source map does not reach the end of the Markdown.')
    headings = mapping['headings']; seen = set(); starts = []
    for h in headings:
        start, end = h['markdown_start_line'], h['markdown_content_end_line']
        if (h['id'] in seen or h['parent_id'] is not None and h['parent_id'] not in seen or
                not 1 <= start <= end <= h['markdown_section_end_line'] <= len(lines) or
                lines[start - 1] != '#' * h['heading_level'] + ' ' + h['title']):
            raise KBError('MANUAL_HEADING_MISMATCH', 'Heading text, hierarchy or source ranges differ from the map.')
        seen.add(h['id']); starts.append(start)
    actual = [n for n, line in enumerate(lines, 1) if n in pages and re.match(r'^#{1,6} ', line)]
    if starts != actual:
        raise KBError('MANUAL_HEADING_MISMATCH', 'Every Markdown heading must occur exactly once in the map.')
    layout_lines = {n for item in mapping.get('layout_titles', []) for n in
                    range(item['markdown_start_line'], item['markdown_end_line'] + 1)}
    stack = []
    for i, h in enumerate(headings):
        while stack and stack[-1]['heading_level'] >= h['heading_level']:
            stack.pop()
        expected_parent = stack[-1]['id'] if stack else None
        direct_limit = headings[i + 1]['markdown_start_line'] - 1 if i + 1 < len(headings) else len(lines)
        section_limit = next((x['markdown_start_line'] - 1 for x in headings[i + 1:]
                              if x['heading_level'] <= h['heading_level']), len(lines))
        def content(end):
            return [n for n in range(h['markdown_start_line'], end + 1)
                    if lines[n - 1].strip() and not lines[n - 1].lstrip().startswith('<!--') and n not in layout_lines]
        if (h['parent_id'] != expected_parent or
                not max(content(direct_limit)) <= h['markdown_content_end_line'] <= direct_limit or
                not max(content(section_limit)) <= h['markdown_section_end_line'] <= section_limit or
                h['pdf_pages'] != sorted({pages[n] for n in content(h['markdown_section_end_line'])})):
            raise KBError('MANUAL_HEADING_MISMATCH', 'Heading parent, direct content, subtree or page coverage disagrees with Markdown.')
        stack.append(h)
    for item in mapping.get('image_assets', []):
        files.append(local(item['file']))
    if (directory / 'checksums.json').is_file():
        checksums = read_json(local('checksums.json'))
        for path in files:
            name = path.relative_to(directory).as_posix()
            if name not in checksums or checksums[name] != hashes[name]:
                raise KBError('MANUAL_HASH_MISMATCH', 'A source package asset differs from its checksum manifest.')
        files.append(directory / 'checksums.json')
    if any(file_hash(directory / name) != sha for name, sha in hashes.items()):
        raise KBError('MANUAL_SOURCE_CHANGED', 'A source package file changed during loading.')
    return {'directory': directory, 'mapping': mapping, 'source_metadata': source, 'lines': lines,
            'page_for_line': pages, 'files': list(dict.fromkeys(files)), 'markdown_path': md,
            'pdf_path': pdf, 'page_count': page_count, 'file_hashes': hashes}
