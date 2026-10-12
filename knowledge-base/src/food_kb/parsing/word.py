"""WordprocessingML structure with real XML anchors, never inferred pagination."""

from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile, is_zipfile

from ..common import KBError, file_hash


WORD_VERSION = "docx-structure-2"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _text(element):
    values = []
    for node in element.iter():
        if node.tag == W + "t":
            values.append(node.text or "")
        elif node.tag == W + "tab":
            values.append("\t")
        elif node.tag in (W + "br", W + "cr"):
            values.append("\n")
    return "".join(values)


def parse_word(path, *, asset_id):
    if file_hash(path) != asset_id:
        raise KBError("HASH_MISMATCH", "Word bytes differ from the registered asset.")
    with open(path, "rb") as original:
        ole_container = original.read(8) == bytes.fromhex("d0cf11e0a1b11ae1")
    if ole_container or not is_zipfile(path):
        raise KBError("UNSUPPORTED_WORD_FORMAT", "Binary .doc requires an explicitly configured converter; no conversion was performed.")
    unit = {"page": None, "width": None, "height": None, "printed_page": None, "status": "usable",
            "method": "docx", "blocks": [], "issues": [], "image": None}
    result = {"page_count": None, "pages": [unit]}

    def issue(code, message):
        if not any(item["code"] == code for item in unit["issues"]):
            unit["issues"].append({"code": code, "message": message})
        unit["status"] = "partial"

    try:
        with ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            extras = [name for name in archive.namelist() if name.startswith(("word/header", "word/footer", "word/footnotes", "word/endnotes", "word/comments")) and name.endswith(".xml")]
    except (BadZipFile, KeyError, ET.ParseError, RuntimeError):
        unit["status"] = "failed"
        unit["issues"] = [{"code": "docx_invalid", "message": "Word document XML is missing, encrypted or malformed."}]
        return result
    body = root.find(W + "body")
    if body is None:
        unit["status"] = "failed"
        unit["issues"] = [{"code": "docx_invalid", "message": "Supported Word document body is missing."}]
        return result
    if extras:
        issue("auxiliary_parts_unparsed", "Headers, footers, notes or comments exist outside the parsed main body: " + ", ".join(sorted(extras)))

    def add(key, parent, kind, text, locator, metadata=None):
        block = {"structural_key": key, "parent_key": parent, "kind": kind,
                 "ordinal": len(unit["blocks"]), "text_raw": text,
                 "locator": {"asset_id": asset_id, "part": "word/document.xml", "bbox": None, **locator},
                 "confidence": None, "metadata": metadata or {}}
        unit["blocks"].append(block)
        return block

    def paragraph(node, key, parent, locator):
        metadata = {}
        if any(element.tag in (W + "instrText", W + "fldChar", W + "fldSimple") for element in node.iter()):
            metadata["contains_field"] = True
            issue("field_result_unverified", "Field display text is retained without recalculating or evaluating Word fields.")
        if any(element.tag in (W + "del", W + "ins", W + "moveFrom", W + "moveTo") for element in node.iter()):
            metadata["contains_revision"] = True
            metadata["deleted_text"] = [part.text or "" for part in node.iter(W + "delText")]
            issue("revisions_unresolved", "Tracked revisions are present; extracted text is not a resolved approved document version.")
        add(key, parent, "text", _text(node), locator, metadata)
        images = [element for element in node.iter() if element.tag in (W + "drawing", W + "pict", W + "object")]
        for number, image in enumerate(images, 1):
            relationships = sorted({value for element in image.iter() for attr, value in element.attrib.items() if attr in (R + "embed", R + "id", R + "link")})
            add(f"{key}/image{number}", key, "image", "", locator | {"image": number}, {"relationship_ids": relationships})
            issue("embedded_visual_unread", "Embedded images or objects are located structurally but have not been visually interpreted.")
        if any(element.tag.endswith("}oMath") for element in node.iter()):
            issue("equation_unparsed", "A Word equation requires dedicated extraction or rendered verification.")

    def walk(container, key, parent, xpath, inherited):
        counters = {}
        for node in container:
            tag = node.tag.removeprefix(W)
            counters[tag] = counters.get(tag, 0) + 1
            index = counters[tag]
            node_path = f"{xpath}/w:{tag}[{index}]"
            if tag == "p":
                paragraph(node, f"{key}/p{index}", parent, inherited | {"paragraph": index, "xml_path": node_path})
            elif tag == "tbl":
                table(node, f"{key}/t{index}", parent, node_path, inherited | {"table": index})
            elif tag not in ("sectPr", "tcPr", "tblPr", "tblGrid", "bookmarkStart", "bookmarkEnd", "proofErr", "commentRangeStart", "commentRangeEnd"):
                issue("body_structure_unparsed", "An unsupported body wrapper or element is retained in the original and needs separate interpretation.")

    def table(node, key, parent, xpath, inherited):
        add(key, parent, "table", "", inherited | {"xml_path": xpath})
        if any(child.tag not in (W + "tr", W + "tblPr", W + "tblGrid") for child in node):
            issue("table_structure_unparsed", "An unsupported table wrapper or element requires separate interpretation.")
        for row_number, row in enumerate(node.findall(W + "tr"), 1):
            row_key = f"{key}/r{row_number}"
            row_path = f"{xpath}/w:tr[{row_number}]"
            row_locator = inherited | {"row": row_number, "xml_path": row_path}
            add(row_key, key, "row", "", row_locator)
            if any(child.tag not in (W + "tc", W + "trPr") for child in row):
                issue("table_structure_unparsed", "An unsupported table wrapper or element requires separate interpretation.")
            before = row.find(f"{W}trPr/{W}gridBefore")
            column = int(before.get(W + "val", "0")) + 1 if before is not None else 1
            for cell_number, cell in enumerate(row.findall(W + "tc"), 1):
                span_node = cell.find(f"{W}tcPr/{W}gridSpan")
                span = int(span_node.get(W + "val", "1")) if span_node is not None else 1
                if span < 1 or column < 1:
                    raise KBError("DOCX_TABLE_INVALID", "Word table contains invalid grid positions.")
                merge_node = cell.find(f"{W}tcPr/{W}vMerge")
                merge = merge_node.get(W + "val", "continue") if merge_node is not None else None
                horizontal = cell.find(f"{W}tcPr/{W}hMerge")
                cell_key = f"{row_key}/c{cell_number}"
                cell_path = f"{row_path}/w:tc[{cell_number}]"
                locator = inherited | {"row": row_number, "column": column, "cell": cell_number, "xml_path": cell_path}
                metadata = {"column_span": span, "vertical_merge": merge}
                if horizontal is not None:
                    metadata["horizontal_merge"] = horizontal.get(W + "val", "continue")
                add(cell_key, row_key, "cell", "", locator, metadata)
                walk(cell, cell_key, cell_key, cell_path, locator)
                column += span

    try:
        walk(body, "document", None, "/w:document/w:body", {})
    except (ValueError, KBError):
        unit["status"] = "failed"
        unit["issues"].append({"code": "docx_structure_invalid", "message": "Word structure contains invalid table indices; partial extraction is retained for diagnosis."})
    if not any(block["text_raw"].strip() or block["kind"] == "image" for block in unit["blocks"]) and unit["status"] == "usable":
        unit["status"] = "unreadable"
        unit["issues"].append({"code": "empty_word_body", "message": "Document has no extractable body content."})
    return result
