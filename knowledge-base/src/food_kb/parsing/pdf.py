"""Physical PDF layout and local OCR; no semantic requirements are inferred."""
import hashlib
import os
from pathlib import Path
import re
import tempfile
import unicodedata

import pymupdf

from ..common import KBError, digest, file_hash
from .ocr import describe_engine, recognize_page, settings


def _issue(code, message):
    return {'code': code, 'message': message}


def _text(block):
    return '\n'.join(''.join(span.get('text', '') for span in line['spans'])
                     for line in block.get('lines', []))


def text_quality(text):
    visible = [character for character in text if not character.isspace()]
    issues = []
    if not visible:
        return [_issue('EMPTY_TEXT', 'No readable text was extracted; this is not evidence of an empty requirement page.')]
    if len(visible) < 20:
        issues.append(_issue('SPARSE_TEXT', 'Fewer than 20 visible characters; inspect the page for missing content.'))
    if any(c == '\ufffd' or unicodedata.category(c) in {'Co', 'Cs', 'Cc'} for c in visible) or '(cid:' in text:
        issues.append(_issue('GARBLED_TEXT', 'Replacement, private-use or undecodable characters require OCR or review.'))
    if re.search(r'(\S)\1{19,}', text):
        issues.append(_issue('SUSPICIOUS_REPETITION', 'Long repeated character sequences may indicate font decoding failure.'))
    punctuation = sum(unicodedata.category(c)[0] in {'P', 'S'} for c in visible)
    if len(visible) >= 40 and punctuation / len(visible) > .30:
        issues.append(_issue('SUSPICIOUS_SYMBOL_RATIO', 'High symbol density may indicate scrambled font mappings; compare against the page image.'))
    return issues


def _review_flags(text, raw=None):
    flags = []
    if re.search(r'[<>=≤≥±×÷∑√^²³₀-₉]|\b(?:mg|kg|g|mL|L|mmol|CFU)\s*[/·]|\d\s*%', text):
        flags.append('formula_comparison_unit_review')
    if raw and any(span.get('flags', 0) & 1 for line in raw.get('lines', []) for span in line['spans']):
        flags.append('superscript_subscript_review')
    return flags


def _block(key, kind, bbox, text, asset_id, number, label, metadata=None, parent=None):
    return {'structural_key': key, 'parent_key': parent, 'kind': kind, 'ordinal': 0,
            'text_raw': text, 'locator': {'asset_id': asset_id, 'pdf_page': number,
                'printed_page': label, 'printed_page_source': 'pdf_page_label' if label else None,
                'bbox': list(bbox) if bbox is not None else None,
                'coordinate_system': 'pdf_points_top_left'},
            'confidence': None, 'metadata': metadata or {}}


def _layout_text(raw_blocks, asset_id, number, label, method):
    result = []
    for index, raw in enumerate(raw_blocks):
        if raw.get('type') != 0:
            continue
        text = _text(raw)
        result.append(_block(f'p{number}/{method}/b{index}', 'text', raw.get('bbox'), text,
                             asset_id, number, label, {'method': method,
                                'review_flags': _review_flags(text, raw),
                                'reading_order': 'geometric_top_to_bottom_left_to_right_candidate'}))
    return result


def _tables(page, asset_id, number, label):
    blocks = []
    for index, table in enumerate(page.find_tables().tables):
        key = f'p{number}/table{index}'
        blocks.append(_block(key, 'table', table.bbox, '', asset_id, number, label,
                             {'row_count': table.row_count, 'column_count': table.col_count,
                              'scope': 'physical_page_fragment', 'review_flags': ['table_structure_numeric_review']}))
        values = table.extract()
        for row_index, row in enumerate(table.rows):
            row_key = f'{key}/r{row_index}'
            blocks.append(_block(row_key, 'row', row.bbox, '', asset_id, number, label,
                                 {'row': row_index}, key))
            for column_index, cell in enumerate(row.cells):
                value = values[row_index][column_index]
                blocks.append(_block(f'{row_key}/c{column_index}', 'cell', cell, value or '',
                                     asset_id, number, label,
                                     {'row': row_index, 'column': column_index,
                                      'geometry_status': 'located' if cell is not None else 'merged_or_unresolved_slot',
                                      'value_status': 'extracted' if value is not None else 'not_extracted',
                                      'review_flags': ['table_structure_numeric_review'] + _review_flags(value or '')}, row_key))
    return blocks


def _save_image(page, work_dir, asset_id, config_id, number, dpi):
    key = f'parsing/images/{asset_id}/{config_id}/{number}.png'
    target = work_dir / key
    if not target.resolve().is_relative_to(work_dir.resolve()):
        raise KBError('OUTPUT_BOUNDARY', 'Page image output escapes its configured directory.')
    if page.rect.width * page.rect.height * (dpi / 72) ** 2 > 40_000_000:
        raise KBError('PDF_RENDER_BUDGET', 'Page exceeds the 40-million-pixel local rendering budget; use a lower dpi.')
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    data = pixmap.tobytes('png')
    sha = hashlib.sha256(data).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.page-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if file_hash(target) != sha:
                raise KBError('STORAGE_CORRUPT', 'Existing page image changed; it was not overwritten.')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'storage_key': key, 'sha256': sha, 'width_px': pixmap.width, 'height_px': pixmap.height,
            'coordinate_system': 'pdf_points_top_left', 'dpi': dpi}


def parse_pdf(path, *, asset_id, options, work_dir, pages=None):
    path, work_dir = Path(path), Path(work_dir)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', asset_id):
        raise KBError('ASSET_ID', 'Asset identity is not a safe storage identifier.')
    config = settings(options)
    config_id = digest(describe_engine(options))
    results = []
    try:
        document = pymupdf.open(path)
    except Exception as exc:
        raise KBError('PDF_OPEN_FAILED', 'PDF cannot be opened; preserve the source for review.') from exc
    with document:
        if not document.is_pdf or document.needs_pass:
            raise KBError('PDF_UNREADABLE', 'The source must be an unencrypted readable PDF.')
        page_count = document.page_count
        selected = list(range(1, page_count + 1)) if pages is None else list(pages)
        if not selected or any(type(number) is not int or not 1 <= number <= page_count for number in selected):
            raise KBError('PDF_PAGE_RANGE', 'Requested actual page number is missing or out of range.')
        if len(set(selected)) != len(selected):
            raise KBError('PDF_PAGE_RANGE', 'A selected page may occur only once.')
        for number in sorted(selected):
            result = {'page': number, 'width': None, 'height': None, 'printed_page': None,
                      'status': 'failed', 'method': 'pdf_text', 'blocks': [], 'issues': [], 'image': None}
            try:
                page = document[number - 1]
                rotation = page.rotation
                page.set_rotation(0)  # in-memory only; original file is never saved
                label = page.get_label() or None
                result.update(width=page.rect.width, height=page.rect.height, printed_page=label,
                              original_rotation=rotation)
                raw = page.get_text('dict', sort=True, flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
                                   & ~pymupdf.TEXT_USE_CID_FOR_UNKNOWN_UNICODE)
                blocks = _layout_text(raw['blocks'], asset_id, number, label, 'pdf_text')
                result['blocks'] = blocks
                text = '\n'.join(block['text_raw'] for block in blocks)
                quality = text_quality(text)
                for index, image in enumerate(page.get_image_info()):
                    bbox = image['bbox']
                    blocks.append(_block(f'p{number}/image{index}', 'image', bbox, '', asset_id, number, label,
                                         {'width_px': image['width'], 'height_px': image['height']}))
                    area = pymupdf.Rect(bbox).get_area()
                    if area >= page.rect.get_area() * .04 and image['width'] >= 100 and image['height'] >= 40:
                        quality.append(_issue('IMAGE_CONTENT_UNVERIFIED', 'A substantial image region may contain text absent from the text layer.'))
                # Multiple images should not repeat the same page-level warning.
                quality = list({issue['code']: issue for issue in quality}.values())
                result['issues'].extend(quality)
                needs_ocr = bool(quality) or config['ocr'] == 'force'
                if needs_ocr:
                    result['image'] = _save_image(page, work_dir, asset_id, config_id, number, config['dpi'])
                    if config['ocr'] == 'off':
                        result['status'] = 'needs_ocr'
                    else:
                        try:
                            recognized = recognize_page(path, number, options)
                            recovered = _layout_text(recognized['blocks'], asset_id, number, label, 'ocr')
                            ocr_text = '\n'.join(block['text_raw'] for block in recovered)
                            remaining = text_quality(ocr_text)
                            result['method'] = 'ocr'
                            # Keep the original decoded layer for comparisons, but do
                            # not mix it into the OCR reading sequence as duplicate text.
                            result['original_text_layer'] = blocks.copy()
                            blocks = [block for block in blocks if block['kind'] == 'image'] + recovered
                            result['blocks'] = blocks
                            result['issues'].extend(_issue('OCR_' + issue['code'], issue['message']) for issue in remaining)
                            result['issues'].append(_issue('OCR_REVIEW_REQUIRED', 'OCR text and numerical symbols remain candidates requiring source review.'))
                            result['issues'].append(_issue('OCR_LAYOUT_UNVERIFIED', 'Raster table structure is not reconstructed; review the physical page image.'))
                            result['status'] = 'unreadable' if not ocr_text.strip() else 'partial'
                        except KBError as exc:
                            result['status'] = 'needs_ocr' if exc.code == 'OCR_UNAVAILABLE' else 'failed'
                            result['issues'].append(_issue(exc.code, str(exc)))
                else:
                    result['status'] = 'usable'
                if result['method'] == 'pdf_text':
                    try:
                        blocks.extend(_tables(page, asset_id, number, label))
                    except Exception:
                        result['issues'].append(_issue('TABLE_EXTRACTION_FAILED', 'Physical table extraction failed; text remains available for review.'))
                        if result['status'] == 'usable':
                            result['status'] = 'partial'
                for block in blocks:
                    if '\x00' in block['text_raw']:
                        result['rejected_blocks'] = blocks
                        result['blocks'] = []
                        raise KBError('PDF_NUL_TEXT', 'Extracted text contains NUL; raw extraction is preserved in the diagnostic report.')
                    box = block['locator']['bbox']
                    if box is not None:
                        intersection = pymupdf.Rect(box) & page.rect
                        clipped = list(intersection) if not intersection.is_empty else None
                        if clipped != box:
                            block['metadata']['unclipped_bbox'] = box
                            block['locator']['bbox'] = clipped
                            block['metadata']['bbox_clipped_to_visible_page'] = True
                    if block['locator']['bbox'] is None:
                        if result['status'] == 'usable':
                            result['status'] = 'partial'
                        if not any(issue['code'] == 'LOCATOR_DEGRADED' for issue in result['issues']):
                            result['issues'].append(_issue('LOCATOR_DEGRADED', 'A structural slot has no independent rectangle; no coordinates were invented.'))
                # Roots are spatially ordered; table parents always precede children.
                roots = [block for block in blocks if block['parent_key'] is None]
                roots.sort(key=lambda b: (b['locator']['bbox'][1], b['locator']['bbox'][0]) if b['locator']['bbox'] else (float('inf'), float('inf')))
                children = {}
                for block in blocks:
                    children.setdefault(block['parent_key'], []).append(block)
                ordered = []
                def append_block(block):
                    block['ordinal'] = len(ordered)
                    ordered.append(block)
                    for child in children.get(block['structural_key'], []):
                        append_block(child)
                for block in roots:
                    append_block(block)
                result['blocks'] = ordered
            except KBError as exc:
                result['issues'].append(_issue(exc.code, str(exc)))
                result['status'] = 'failed'
            except Exception:
                result['issues'].append(_issue('PDF_PAGE_FAILED', 'Page extraction failed; other pages remain independently retryable.'))
                result['status'] = 'failed'
            results.append(result)
    return {'page_count': page_count, 'pages': results}
