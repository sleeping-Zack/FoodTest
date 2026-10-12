import subprocess

import pymupdf
import pytest

from food_kb.common import KBError, file_hash
from food_kb.parsing import ocr, pdf
from food_kb.parsing.contracts import validate_document


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / 'SYNTHETIC.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=300)
        page.insert_text((20, 30), 'SYNTHETIC source for engineering tests only')
        page.insert_text((20, 60), 'Value <= 2 mg/kg; not a real food requirement')
        document.new_page(width=300, height=300)
        source = pymupdf.open()
        scan = source.new_page(width=300, height=300)
        scan.insert_text((20, 50), 'SYNTHETIC scan, no hidden text layer')
        image = scan.get_pixmap().tobytes('png')
        third = document.new_page(width=300, height=300)
        third.insert_image(third.rect, stream=image)
        document.set_page_labels([{'startpage': 0, 'prefix': '', 'style': 'r'}])
        document.save(path)
        source.close()
    return path


def parse(path, tmp_path, **kwargs):
    options = {'ocr': 'off'} | kwargs.pop('options', {})
    return pdf.parse_pdf(path, asset_id='test-asset', options=options, work_dir=tmp_path / 'output', **kwargs)


def test_real_pages_labels_units_and_original_unchanged(sample_pdf, tmp_path):
    before = file_hash(sample_pdf)
    result = parse(sample_pdf, tmp_path)
    validate_document(result, 'test-asset')
    assert result['page_count'] == 3
    assert result['pages'][0]['status'] == 'usable'
    assert result['pages'][0]['printed_page'] == 'i'
    assert any('formula_comparison_unit_review' in b['metadata']['review_flags'] for b in result['pages'][0]['blocks'])
    assert result['pages'][1]['status'] == 'needs_ocr'
    assert result['pages'][2]['status'] == 'needs_ocr'
    assert any(b['kind'] == 'image' for b in result['pages'][2]['blocks'])
    assert file_hash(sample_pdf) == before
    image = result['pages'][2]['image']
    assert file_hash(tmp_path / 'output' / image['storage_key']) == image['sha256']


@pytest.mark.parametrize('pages', [[0], [4], [1, 1], [], [True]])
def test_requested_missing_or_repeated_pages_rejected(sample_pdf, tmp_path, pages):
    with pytest.raises(KBError, match='page'):
        parse(sample_pdf, tmp_path, pages=pages)


def test_partial_selection_preserves_actual_page(sample_pdf, tmp_path):
    result = parse(sample_pdf, tmp_path, pages=[3])
    validate_document(result, 'test-asset', [3])
    assert result['page_count'] == 3
    assert result['pages'][0]['page'] == 3
    assert all(b['locator']['pdf_page'] == 3 for b in result['pages'][0]['blocks'])


def test_real_physical_tables_and_parent_order(tmp_path):
    path = tmp_path / 'table.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        for x in (20, 140, 280):
            page.draw_line((x, 40), (x, 130))
        for y in (40, 70, 100, 130):
            page.draw_line((20, y), (280, y))
        for x, y, text in ((25, 60, 'Synthetic item'), (145, 60, 'Value mg/kg'), (25, 90, 'Synthetic A'),
                           (145, 90, '0.05'), (25, 120, 'Synthetic B')):
            page.insert_text((x, y), text)
        doc.save(path)
    result = parse(path, tmp_path)
    validate_document(result, 'test-asset')
    cells = [b for b in result['pages'][0]['blocks'] if b['kind'] == 'cell']
    assert len(cells) == 6
    assert cells[-1]['text_raw'] == ''
    assert cells[-1]['locator']['bbox'] == [140, 100, 280, 130]
    assert cells[-1]['metadata']['review_flags'] == ['table_structure_numeric_review']


def test_merged_slot_is_retained_without_invented_coordinates(tmp_path):
    path = tmp_path / 'merged.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        for x in (20, 280):
            page.draw_line((x, 40), (x, 130))
        page.draw_line((140, 70), (140, 130))
        for y in (40, 70, 100, 130):
            page.draw_line((20, y), (280, y))
        for x, y, text in ((25, 60, 'SYNTHETIC merged header'), (25, 90, 'Synthetic A'),
                           (145, 90, '0.05'), (25, 120, 'Synthetic B'), (145, 120, '0.10')):
            page.insert_text((x, y), text)
        doc.save(path)
    result = parse(path, tmp_path)
    validate_document(result, 'test-asset')
    page = result['pages'][0]
    missing = [b for b in page['blocks'] if b['kind'] == 'cell' and b['locator']['bbox'] is None]
    assert page['status'] == 'partial'
    assert len(missing) == 1
    assert missing[0]['metadata']['geometry_status'] == 'merged_or_unresolved_slot'
    assert missing[0]['text_raw'] == ''


@pytest.mark.parametrize('text,code', [('decoded \ufffd value enough text to exceed sparse threshold', 'GARBLED_TEXT'),
                                     ('font \ue123 decoding enough text to exceed sparse threshold', 'GARBLED_TEXT'),
                                     ('!#$%&*()*' * 10, 'SUSPICIOUS_SYMBOL_RATIO')])
def test_corrupt_text_is_not_normal_empty(text, code):
    assert code in {issue['code'] for issue in pdf.text_quality(text)}


def test_normal_english_is_not_rejected_as_scrambled_chinese():
    assert pdf.text_quality('SYNTHETIC English method: measure 0.5 mg/kg, record values and verify the source.') == []


def test_timeout_is_page_failure_with_image_and_other_pages_remain(sample_pdf, tmp_path, monkeypatch):
    def timeout(*args):
        raise KBError('OCR_TIMEOUT', 'SYNTHETIC timeout')
    monkeypatch.setattr(pdf, 'recognize_page', timeout)
    result = parse(sample_pdf, tmp_path, options={'ocr': 'auto'})
    validate_document(result, 'test-asset')
    assert result['pages'][0]['status'] == 'usable'
    assert result['pages'][2]['status'] == 'failed'
    assert result['pages'][2]['image'] is not None
    assert result['pages'][2]['issues'][-1]['code'] == 'OCR_TIMEOUT'


def test_worker_timeout_translates_to_safe_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, 'language_data', lambda options: (tmp_path, {}))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('worker', 0.01)
    monkeypatch.setattr(ocr.subprocess, 'run', timeout)
    with pytest.raises(KBError) as caught:
        ocr.recognize_page(tmp_path / 'source.pdf', 1, {'ocr': 'auto', 'timeout_seconds': .01})
    assert caught.value.code == 'OCR_TIMEOUT'


def test_ocr_without_coordinates_declares_degraded_locator(sample_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, 'recognize_page', lambda *args: {'blocks': [
        {'type': 0, 'lines': [{'spans': [{'text': 'SYNTHETIC OCR text with missing region coordinates'}]}]}]})
    result = parse(sample_pdf, tmp_path, pages=[3], options={'ocr': 'auto'})
    validate_document(result, 'test-asset', [3])
    page = result['pages'][0]
    assert page['status'] == 'partial'
    assert next(b for b in page['blocks'] if b['kind'] == 'text')['locator']['bbox'] is None
    assert all(b['confidence'] is None for b in page['blocks'])
    assert any(issue['code'] == 'LOCATOR_DEGRADED' for issue in page['issues'])


def test_substantial_image_on_digital_page_triggers_ocr(tmp_path, monkeypatch):
    path = tmp_path / 'mixed.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((20, 20), 'SYNTHETIC readable heading with raster table below')
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 200), False)
        pixmap.clear_with(255)
        page.insert_image(pymupdf.Rect(20, 60, 280, 260), stream=pixmap.tobytes('png'))
        doc.save(path)
    calls = []
    monkeypatch.setattr(pdf, 'recognize_page', lambda *args: calls.append(args) or {'blocks': []})
    result = parse(path, tmp_path, options={'ocr': 'auto'})
    assert len(calls) == 1
    assert result['pages'][0]['status'] == 'unreadable'
    assert any(i['code'] == 'IMAGE_CONTENT_UNVERIFIED' for i in result['pages'][0]['issues'])


def test_rotation_uses_consistent_unrotated_coordinates(tmp_path):
    path = tmp_path / 'rotation.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=300, height=200)
        page.insert_text((20, 30), 'SYNTHETIC rotation coordinate check of original text')
        page.set_rotation(90)
        doc.save(path)
    result = parse(path, tmp_path)
    validate_document(result, 'test-asset')
    page = result['pages'][0]
    assert (page['width'], page['height'], page['original_rotation']) == (300, 200, 90)
    with pymupdf.open(path) as original:
        assert original[0].rotation == 90


def test_language_identity_uses_bytes_not_install_path(tmp_path):
    for directory in ('one', 'two'):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / 'eng.traineddata').write_bytes(b'SYNTHETIC not used for recognition')
    first = ocr.describe_engine({'language': 'eng', 'tessdata': tmp_path / 'one'})
    second = ocr.describe_engine({'language': 'eng', 'tessdata': tmp_path / 'two'})
    assert first == second
    (tmp_path / 'two' / 'eng.traineddata').write_bytes(b'SYNTHETIC changed')
    assert first != ocr.describe_engine({'language': 'eng', 'tessdata': tmp_path / 'two'})


def test_page_image_tampering_is_failure_not_overwrite(sample_pdf, tmp_path):
    result = parse(sample_pdf, tmp_path, pages=[3])
    image = tmp_path / 'output' / result['pages'][0]['image']['storage_key']
    image.write_bytes(b'tampered')
    again = parse(sample_pdf, tmp_path, pages=[3])
    assert again['pages'][0]['status'] == 'failed'
    assert any(i['code'] == 'STORAGE_CORRUPT' for i in again['pages'][0]['issues'])
    assert image.read_bytes() == b'tampered'
