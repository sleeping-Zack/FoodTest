import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from food_kb.chunking.viewer import write_viewer


def viewer_fixture(tmp_path):
    source = tmp_path / "source"
    (source / "assets").mkdir(parents=True)
    (source / "source.pdf").write_bytes(b"%PDF-SYNTHETIC fixture; source link only")
    (source / "assets/page-01.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH1sAAAAASUVORK5CYII="))
    location = {"pdf_pages": [1], "line_start": 1, "line_end": 5}
    unsafe = '<script>window.PWNED=1</script><img src="https://example.invalid/leak" onerror="window.PWNED=2"><iframe src="https://example.invalid/frame"></iframe>[unsafe](javascript:alert(1))'
    table = '<table><thead><tr><th>项目</th><th>方法</th></tr></thead><tbody><tr><td>甲</td><td rowspan="2">共用</td></tr><tr><td>乙</td></tr></tbody></table>'
    blocks = [
        {"block_id": "scope", "key": "1", "parent_id": None, "title": "适用范围", "text_raw": "仅用于合成界面测试。", "source": location},
        {"block_id": "table", "key": "table", "parent_id": None, "title": "表格", "text_raw": table, "source": location},
        {"block_id": "row", "key": "row", "parent_id": "table", "title": "测试行", "text_raw": "甲 | 2 | 方法", "source": location},
        {"block_id": "formula", "key": "A.1", "parent_id": None, "title": "方法", "text_raw": "$$\nw=\\frac{1}{2}\\times100\\%\\tag{A.1}\n$$\n" + unsafe, "source": location},
    ]
    chunks = []
    for block in blocks:
        kind = {"row": "table_row", "table": "table", "formula": "method"}.get(block["block_id"], "scope")
        chunks.append({"chunk_id": block["block_id"], "anchor": block["key"], "kind": kind,
                       "title": block["title"], "raw_markdown": block["text_raw"], "retrieval_text": block["text_raw"],
                       "block_ids": [block["block_id"]], "heading_path": ["合成测试", block["title"]],
                       "source": location, "retrieval_enabled": kind != "table", "executable": False,
                       "quality": {"status": "complete_for_retrieval", "issues": []}})
    chunks[2]["table"] = {"headers": ["项目", "指标", "方法"], "project": "甲", "comparator": "≤", "value": "2", "unit": "mg/kg", "method": "合成方法",
                          "cells": [{"column": 1, "text_raw": "甲", "value": "甲"}, {"column": 2, "text_raw": "2", "value": "2"},
                                    {"column": 3, "text_raw": "", "value": "共用" + unsafe, "inherited": True}]}
    return source, {"source": {"title": "合成测试 </script><script>window.PWNED=3</script>", "document_id": "SYNTHETIC", "asset_id": "a" * 64, "markdown_asset_id": "b" * 64, "page_count": 1},
                    "blocks": blocks, "chunks": chunks,
                    "links": [{"chunk_id": "row", "target_block_id": "table", "role": "table"}, {"chunk_id": "row", "target_block_id": "scope", "role": "scope"},
                              {"chunk_id": "formula", "target_block_id": "formula", "role": "method"}]}


def test_viewer_embeds_safe_data_and_preserves_input(tmp_path):
    source, bundle = viewer_fixture(tmp_path)
    original = deepcopy(bundle)
    output = tmp_path / "viewer/结果.html"
    write_viewer(bundle, output, source)
    html = output.read_text(encoding="utf-8")
    assert "<script>window.PWNED" not in html
    assert "\\u003cscript\\u003e" in html
    assert "connect-src 'none'" in html
    assert bundle == original
    assert (output.parent / "结果-assets/vendor/markdown-it/LICENSE").is_file()
    assert (output.parent / "结果-assets/vendor/katex/LICENSE").is_file()
    assert (source / "source.pdf").read_bytes() == b"%PDF-SYNTHETIC fixture; source link only"


def test_viewer_refuses_to_write_inside_preserved_source(tmp_path):
    source, bundle = viewer_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_viewer(bundle, source / "overwrite.html", source)
    assert not (source / "overwrite.html").exists()


def test_offline_viewer_browser_blocks_active_input_and_renders_structure(tmp_path):
    source, bundle = viewer_fixture(tmp_path)
    output = tmp_path / "viewer/结果.html"
    write_viewer(bundle, output, source)
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser test runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_chunking_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output.parent / "viewer-checks.json").read_text(encoding="utf-8"))
    assert report["remote_requests"] == [] and report["page_errors"] == []
