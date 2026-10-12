"""Reuse only page-text caches bound to the exact preserved PDF bytes."""

from pathlib import Path
import hashlib
import json
import re

import pymupdf

from ..common import KBError, file_hash


LEGACY_VERSION = "legacy-page-cache-1"


def _text_issue(text):
    if not text.strip():
        return {"code": "empty_text", "message": "Cached page has no readable text; OCR is required."}
    if ("\ufffd" in text or "\x00" in text or
            len(re.findall(r"/G[0-9A-Fa-f]{2,}", text)) >= 3 or
            any(0xE000 <= ord(char) <= 0xF8FF for char in text)):
        return {"code": "suspect_text_encoding", "message": "Cached text contains undecoded glyphs; OCR or manual verification is required."}
    return None


def parse_cache(path, *, original_path, asset_id, pages=None):
    """Adapt the two existing hash-bound PDF JSON formats without inventing boxes."""
    if file_hash(original_path) != asset_id:
        raise KBError("HASH_MISMATCH", "Original bytes differ from the registered asset.")
    try:
        cache_bytes = Path(path).read_bytes()
        cached = json.loads(cache_bytes.decode("utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise KBError("CACHE_INVALID", "Page cache cannot be read as JSON.") from exc
    if not isinstance(cached, dict) or not isinstance(cached.get("sha256"), str):
        raise KBError("CACHE_UNBOUND", "Cache has no original SHA-256 binding; reuse is refused.")
    if cached["sha256"].lower() != asset_id:
        raise KBError("CACHE_HASH_MISMATCH", "Cache belongs to different original bytes.")
    try:
        document = pymupdf.open(original_path)
    except Exception as exc:
        raise KBError("CACHE_SOURCE_UNREADABLE", "Cannot verify the cache against its original PDF.") from exc
    with document:
        if not document.is_pdf or document.needs_pass:
            raise KBError("CACHE_FORMAT_UNSUPPORTED", "Page-cache reuse requires an accessible PDF original.")
        count = len(document)
        if "page_count" in cached and (type(cached["page_count"]) is not int or cached["page_count"] != count):
            raise KBError("CACHE_PAGE_COUNT_MISMATCH", "Cache page count differs from the actual PDF.")
        entries = cached.get("pages")
        if not isinstance(entries, list):
            raise KBError("CACHE_INVALID", "Page cache must contain a pages array.")
        indexed = {}
        for entry in entries:
            page = entry.get("pdf_page") if isinstance(entry, dict) else None
            if type(page) is not int or not 1 <= page <= count or page in indexed:
                raise KBError("CACHE_PAGE_INDEX_INVALID", "Cache page indices must be unique one-based PDF pages.")
            indexed[page] = entry
        selected = list(range(1, count + 1)) if pages is None else list(pages)
        if not selected or any(type(page) is not int or not 1 <= page <= count for page in selected) or len(set(selected)) != len(selected):
            raise KBError("PAGE_SELECTION_INVALID", "Requested PDF pages must be unique and within the original.")
        cache_hash = hashlib.sha256(cache_bytes).hexdigest()
        result = []
        for page_number in selected:
            rect = document[page_number - 1].rect
            unit = {"page": page_number, "width": float(rect.width), "height": float(rect.height),
                    "printed_page": None, "status": "partial", "method": "legacy_cache", "blocks": [],
                    "issues": [], "image": None}
            entry = indexed.get(page_number)
            if entry is None or not isinstance(entry.get("text"), str):
                unit["status"] = "failed"
                unit["issues"].append({"code": "cache_page_missing" if entry is None else "cache_text_invalid",
                                       "message": "Requested page is absent or has no string text in the cache."})
            else:
                text = entry["text"]
                unit["blocks"].append({"structural_key": f"p{page_number}/b0", "parent_key": None,
                    "kind": "text", "ordinal": 0, "text_raw": text,
                    "locator": {"asset_id": asset_id, "pdf_page": page_number, "printed_page": None, "bbox": None},
                    "confidence": None, "metadata": {"cache_sha256": cache_hash}})
                unit["issues"].append({"code": "locator_degraded", "message": "Legacy cache has page text only; block coordinates and layout are unavailable."})
                issue = _text_issue(text)
                if issue:
                    unit["status"] = "needs_ocr"
                    unit["issues"].append(issue)
                if cached.get("error"):
                    unit["issues"].append({"code": "legacy_parser_diagnostic", "message": "Cache records a previous extraction error and fallback; original diagnostic remains in the cache."})
            result.append(unit)
        return {"page_count": count, "pages": result}
