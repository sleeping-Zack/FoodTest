from zipfile import ZipFile, is_zipfile
from xml.etree import ElementTree as ET

import pymupdf
import pytest

from food_kb.common import KBError, file_hash, write_json
from food_kb.parsing.contracts import validate_document
from food_kb.parsing.legacy import parse_cache
from food_kb.parsing.word import parse_word


@pytest.fixture
def cached_pdf(tmp_path):
    original = tmp_path / "original.pdf"
    with pymupdf.open() as pdf:
        for text in ("SYNTHETIC first page", "SYNTHETIC second page", "SYNTHETIC third page"):
            pdf.new_page(width=300, height=400).insert_text((30, 30), text)
        pdf.save(original)
    cache = tmp_path / "cache.json"
    value = {"sha256": file_hash(original), "page_count": 3,
             "pages": [{"pdf_page": number, "text": f"SYNTHETIC page {number}\n"} for number in range(1, 4)]}
    write_json(cache, value)
    return original, cache, value


def test_bound_cache_preserves_exact_text_with_explicit_coordinate_gap(cached_pdf):
    original, cache, value = cached_pdf
    result = parse_cache(cache, original_path=original, asset_id=value["sha256"])
    assert result["page_count"] == 3
    assert [page["page"] for page in result["pages"]] == [1, 2, 3]
    for index, page in enumerate(result["pages"]):
        block = page["blocks"][0]
        assert block["text_raw"] == value["pages"][index]["text"]
        assert block["locator"]["pdf_page"] == index + 1
        assert block["locator"]["bbox"] is None
        assert "coordinate_system" not in block["locator"]
        assert page["printed_page"] is None and page["status"] == "partial"
        assert page["width"] == 300 and page["height"] == 400
        assert page["issues"][0]["code"] == "locator_degraded"


@pytest.mark.parametrize("change,code", [
    (lambda value: value.pop("sha256"), "CACHE_UNBOUND"),
    (lambda value: value.update(sha256="0" * 64), "CACHE_HASH_MISMATCH"),
    (lambda value: value.update(page_count=2), "CACHE_PAGE_COUNT_MISMATCH"),
    (lambda value: value["pages"].append(value["pages"][0]), "CACHE_PAGE_INDEX_INVALID"),
    (lambda value: value["pages"][0].update(pdf_page=0), "CACHE_PAGE_INDEX_INVALID"),
    (lambda value: value["pages"][0].update(pdf_page=True), "CACHE_PAGE_INDEX_INVALID"),
])
def test_cache_refuses_unbound_or_inconsistent_original(cached_pdf, change, code):
    original, cache, value = cached_pdf
    asset_id = value["sha256"]
    change(value)
    write_json(cache, value)
    with pytest.raises(KBError) as error:
        parse_cache(cache, original_path=original, asset_id=asset_id)
    assert error.value.code == code


def test_guide_cache_without_declared_count_and_page_retry(cached_pdf):
    original, cache, value = cached_pdf
    value.pop("page_count")
    value["pages"] = [value["pages"][2], value["pages"][0]]
    write_json(cache, value)
    result = parse_cache(cache, original_path=original, asset_id=value["sha256"])
    assert result["pages"][1]["status"] == "failed"
    assert result["pages"][1]["issues"][0]["code"] == "cache_page_missing"
    retry = parse_cache(cache, original_path=original, asset_id=value["sha256"], pages=[3])
    assert retry["page_count"] == 3 and retry["pages"][0]["page"] == 3
    assert len(retry["pages"]) == 1


@pytest.mark.parametrize("text", ["", "\ufffd\ufffd", "/G31/G32/G33", "bad\ue011encoding"])
def test_unusable_cache_text_requests_ocr(cached_pdf, text):
    original, cache, value = cached_pdf
    value["pages"][1]["text"] = text
    write_json(cache, value)
    result = parse_cache(cache, original_path=original, asset_id=value["sha256"])
    assert result["pages"][0]["status"] == "partial"
    assert result["pages"][1]["status"] == "needs_ocr"
    assert result["pages"][1]["blocks"][0]["text_raw"] == text


def test_cache_selection_and_changed_original_are_rejected(cached_pdf):
    original, cache, value = cached_pdf
    for selected in ([], [0], [4], [1, 1], [True]):
        with pytest.raises(KBError) as error:
            parse_cache(cache, original_path=original, asset_id=value["sha256"], pages=selected)
        assert error.value.code == "PAGE_SELECTION_INVALID"
    original.write_bytes(b"SYNTHETIC replacement, not a PDF")
    with pytest.raises(KBError) as error:
        parse_cache(cache, original_path=original, asset_id=value["sha256"])
    assert error.value.code == "HASH_MISMATCH"


def make_docx(path, body, extras=None):
    xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
           f'<w:body>{body}</w:body></w:document>')
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
        for name, text in (extras or {}).items():
            archive.writestr(name, text)
    return path


def test_word_preserves_paragraphs_cells_empty_values_merges_and_structural_order(tmp_path):
    path = make_docx(tmp_path / "test.docx", '''
      <w:p><w:r><w:t xml:space="preserve"> SYNTHETIC </w:t><w:tab/><w:t>text</w:t><w:br/><w:t>line</w:t></w:r></w:p>
      <w:p/>
      <w:tbl>
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>merged</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>third</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
          <w:tc><w:p><w:r><w:t>last</w:t></w:r></w:p></w:tc></w:tr>
      </w:tbl><w:p><w:r><w:t>after</w:t></w:r></w:p>''')
    before = file_hash(path)
    result = parse_word(path, asset_id=before)
    unit = result["pages"][0]
    assert result["page_count"] is None and unit["page"] is None
    assert unit["status"] == "usable"
    blocks = unit["blocks"]
    assert [block["ordinal"] for block in blocks] == list(range(len(blocks)))
    assert blocks[0]["text_raw"] == " SYNTHETIC \ttext\nline"
    assert blocks[1]["text_raw"] == ""
    assert blocks[-1]["structural_key"] == "document/p3"
    cells = [block for block in blocks if block["kind"] == "cell"]
    assert [cell["locator"]["column"] for cell in cells] == [1, 3, 1, 3]
    assert cells[0]["metadata"] == {"column_span": 2, "vertical_merge": "restart"}
    assert cells[2]["metadata"]["vertical_merge"] == "continue"
    assert cells[0]["locator"]["xml_path"] == "/w:document/w:body/w:tbl[1]/w:tr[1]/w:tc[1]"
    assert all("pdf_page" not in block["locator"] for block in blocks)
    assert all(block["locator"]["bbox"] is None for block in blocks)
    assert all(block["confidence"] is None for block in blocks)
    assert file_hash(path) == before


def test_word_images_fields_revisions_and_unparsed_parts_are_explicit(tmp_path):
    path = make_docx(tmp_path / "test.docx", '''
      <w:p><w:r><w:instrText> PAGE </w:instrText><w:t>1</w:t></w:r><w:r><w:drawing r:embed="rId4"/></w:r>
      <w:del><w:r><w:delText>deleted</w:delText></w:r></w:del><w:ins><w:r><w:t>inserted</w:t></w:r></w:ins></w:p>''',
      {"word/footnotes.xml": "<notes/>"})
    unit = parse_word(path, asset_id=file_hash(path))["pages"][0]
    assert unit["status"] == "partial"
    assert {issue["code"] for issue in unit["issues"]} == {
        "field_result_unverified", "embedded_visual_unread", "revisions_unresolved", "auxiliary_parts_unparsed"}
    assert unit["blocks"][0]["text_raw"] == "1inserted"
    assert unit["blocks"][0]["metadata"]["deleted_text"] == ["deleted"]
    assert unit["blocks"][1]["kind"] == "image"
    assert unit["blocks"][1]["metadata"]["relationship_ids"] == ["rId4"]


def test_binary_word_is_unsupported_and_bad_docx_is_failed(tmp_path):
    path = tmp_path / "old.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1SYNTHETIC binary Word")
    with pytest.raises(KBError) as error:
        parse_word(path, asset_id=file_hash(path))
    assert error.value.code == "UNSUPPORTED_WORD_FORMAT"
    path = make_docx(tmp_path / "bad.docx", "<broken>")
    assert parse_word(path, asset_id=file_hash(path))["pages"][0]["status"] == "failed"


def test_word_hash_and_invalid_grid_are_not_silently_accepted(tmp_path):
    path = make_docx(tmp_path / "bad.docx", '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="0"/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>')
    with pytest.raises(KBError) as error:
        parse_word(path, asset_id="0" * 64)
    assert error.value.code == "HASH_MISMATCH"
    unit = parse_word(path, asset_id=file_hash(path))["pages"][0]
    assert unit["status"] == "failed" and unit["issues"][0]["code"] == "docx_structure_invalid"


def test_word_empty_body_and_unsupported_table_content_are_reported(tmp_path):
    path = make_docx(tmp_path / "empty.docx", "<w:p/>")
    unit = parse_word(path, asset_id=file_hash(path))["pages"][0]
    assert unit["status"] == "unreadable"
    assert unit["blocks"][0]["text_raw"] == ""
    path = make_docx(tmp_path / "wrapped.docx", "<w:tbl><w:sdt><w:sdtContent><w:tr><w:tc><w:p/></w:tc></w:tr></w:sdtContent></w:sdt></w:tbl>")
    unit = parse_word(path, asset_id=file_hash(path))["pages"][0]
    assert unit["status"] == "partial"
    assert unit["issues"][0]["code"] == "table_structure_unparsed"


def test_nested_word_table_anchors_resolve_to_real_xml(tmp_path):
    path = make_docx(tmp_path / "nested.docx", '''
      <w:tbl><w:tr><w:trPr><w:gridBefore w:val="1"/></w:trPr><w:tc>
        <w:tcPr><w:hMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>outer</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>inner</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:tc></w:tr></w:tbl>''')
    asset = file_hash(path)
    result = validate_document(parse_word(path, asset_id=asset), asset)
    with ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for block in result["pages"][0]["blocks"]:
        relative = block["locator"]["xml_path"].removeprefix("/w:document/")
        assert root.find(relative, namespace) is not None
    cells = [block for block in result["pages"][0]["blocks"] if block["kind"] == "cell"]
    assert cells[0]["locator"]["column"] == 2
    assert cells[0]["metadata"]["horizontal_merge"] == "restart"
    assert cells[1]["locator"]["column"] == 1
    assert cells[0]["structural_key"] in cells[1]["structural_key"]


def test_word_failure_unit_accepts_none_page_without_fake_pdf_position(tmp_path):
    path = make_docx(tmp_path / "invalid.docx", "<broken>")
    asset = file_hash(path)
    result = validate_document(parse_word(path, asset_id=asset), asset)
    assert result["pages"][0]["page"] is None
    assert result["pages"][0]["status"] == "failed"


def test_ole_container_is_not_docx_even_with_readable_zip_tail(tmp_path):
    path = tmp_path / "embedded.doc"
    path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"SYNTHETIC OLE container")
    with ZipFile(path, "a") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>embedded ZIP content is not the original Word body</w:t></w:r></w:p></w:body></w:document>')
    assert is_zipfile(path)
    original = file_hash(path)
    with pytest.raises(KBError) as error:
        parse_word(path, asset_id=original)
    assert error.value.code == "UNSUPPORTED_WORD_FORMAT"
    assert file_hash(path) == original
