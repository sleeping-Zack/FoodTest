import base64
from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from food_kb.knowledge.viewer import write_viewer


def viewer_fixture(tmp_path):
    source = tmp_path / "source"
    (source / "assets").mkdir(parents=True)
    (source / "source.pdf").write_bytes(b"%PDF-SYNTHETIC fixture; source link only")
    (source / "assets/page-01.png").write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jH1sAAAAASUVORK5CYII="))
    location = {"pdf_pages": [1], "line_start": 1, "line_end": 5}
    unsafe = '<script>window.PWNED=1</script><img src="https://example.invalid/leak" onerror="window.PWNED=2"><iframe src="https://example.invalid/frame"></iframe>[unsafe](javascript:alert(1))'
    table = '<table><thead><tr><th>项目</th><th>方法</th></tr></thead><tbody><tr><td>甲</td><td rowspan="2">共用</td></tr><tr><td>乙</td></tr></tbody></table>'
    formula = "$$\nw=\\frac{1}{2}\\times100\\%\\tag{A.1}\n$$\n式中：w为合成变量。\n" + unsafe
    blocks = [{"block_id": "table", "key": "table", "title": "合成表", "text_raw": table, "source": location},
              {"block_id": "formula", "key": "formula", "title": "合成公式", "text_raw": formula, "source": location}]
    chunks = [{"chunk_id": "chunk-" + block["block_id"], "anchor": block["key"], "title": block["title"],
               "raw_markdown": block["text_raw"], "source": location} for block in blocks]
    def field(raw, value, block_id, quote):
        return {"raw": raw, "value": value, "normalization_reason": "合成测试：去掉小数内排版空格。", "evidence": [{"block_id": block_id, "quote": quote, "source": location}]}
    def record(record_id, record_type, fields, unresolved=None):
        return {"record_id": record_id, "revision": 1, "review_state": "review_pending",
                "candidate": {"schema_version": "synthetic-1", "logical_key": record_type + ":SYNTHETIC:" + record_id, "record_type": record_type,
                              "standard_type": "synthetic", "title": "=合成测试 " + record_id, "fields": fields,
                              "condition": {"raw": "仅用于合成界面测试。", "ast": {"op": "true"}, "unresolved": unresolved or [], "required_facts": []},
                              "references": [{"raw": "SYNTHETIC 外部标准", "status": "unresolved", "reason": "合成测试，未进行标准版本匹配。"}],
                              "relations": [], "source_chunk_ids": ["chunk-table" if record_type == "requirement" else "chunk-formula"], "executable": False},
                "validation": {"passed": True, "issues": [], "publication_blockers": ["BUSINESS_REVIEW_REQUIRED"]}}
    records = [record("synthetic-requirement", "requirement", {"project": field("甲", "甲", "table", "甲" + unsafe),
                 "limit_value": field("0.003 5", "0.0035", "table", "0.003 5"), "unit": field("%", "%", "table", "%")}),
               record("synthetic-method", "method", {"formula": field(formula, formula, "formula", formula)}),
               record("synthetic-scope", "scope", {"scope": field("合成范围", "合成范围", "table", "合成范围")}, ["UNRESOLVED_CONDITION"])]
    return source, {"extraction_id": "synthetic-extraction", "chunk_run_id": "synthetic-chunk-run",
                    "source": {"title": "合成测试 </script><script>window.PWNED=3</script>", "document_id": "SYNTHETIC", "asset_id": "a" * 64, "markdown_asset_id": "b" * 64, "page_count": 1},
                    "records": records, "blocks": blocks, "chunks": chunks,
                    "coverage": [{"chunk_id": "chunk-table", "anchor": "table", "status": "extracted", "record_ids": ["synthetic-requirement"], "reason": "合成覆盖"},
                                 {"chunk_id": "chunk-formula", "anchor": "formula", "status": "extracted", "record_ids": ["synthetic-method"], "reason": "合成覆盖"}], "summary": {}}


def test_knowledge_viewer_embeds_safe_data_and_preserves_input(tmp_path):
    source, report = viewer_fixture(tmp_path)
    original = deepcopy(report)
    output = tmp_path / "viewer/候选知识.html"
    write_viewer(report, output, source)
    html = output.read_text(encoding="utf-8")
    assert "<script>window.PWNED" not in html
    assert "\\u003cscript\\u003e" in html
    assert "connect-src 'none'" in html
    assert report == original
    payload = json.loads(html.split('<script id="viewer-data" type="application/json">', 1)[1].split('</script>', 1)[0])
    assert payload["report"] == report
    assert len(payload["fact_examples"]) == 5
    assert "fact_examples" not in report
    assert (output.parent / "候选知识-assets/vendor/markdown-it/LICENSE").is_file()
    assert (output.parent / "候选知识-assets/vendor/katex/LICENSE").is_file()
    assert (source / "source.pdf").read_bytes() == b"%PDF-SYNTHETIC fixture; source link only"


def test_knowledge_viewer_refuses_to_write_inside_preserved_source(tmp_path):
    source, report = viewer_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_viewer(report, source / "overwrite.html", source)
    assert not (source / "overwrite.html").exists()


def test_knowledge_viewer_browser_safely_renders_evidence_and_downloads(tmp_path):
    source, report = viewer_fixture(tmp_path)
    output = tmp_path / "viewer/候选知识.html"
    write_viewer(report, output, source)
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser test runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_knowledge_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = json.loads((output.parent / "knowledge-viewer-checks.json").read_text(encoding="utf-8"))
    assert checks["remote_requests"] == [] and checks["page_errors"] == []


def test_knowledge_viewer_lean_method_references_and_semantic_parameters(tmp_path):
    source, report = viewer_fixture(tmp_path)
    method = report["records"][1]["candidate"]
    method["logical_key"] = "method:SYNTHETIC:A.6"
    method["semantic_coverage"] = {"state": "partial", "source_only": ["experimental_conditions", "formula_variables"],
                                   "unsupported_checks": ["formula_recalculation"], "note": "合成方法：只验证已整理事实和原文引用的展示。"}
    for anchor, name in [("A.6.1", "合成子方法一"), ("A.6.2", "合成子方法二")]:
        report["blocks"].append({"block_id": anchor, "key": anchor, "title": name,
                                 "text_raw": "### " + name + "\n\n完整操作保留在来源。", "source": report["blocks"][0]["source"]})
    evidence = method["fields"]["formula"]["evidence"]
    method["fields"] = {
        "method_name": {"raw": "合成氯化物方法", "value": "合成氯化物方法", "evidence": evidence},
        "submethods": {"raw": "", "value": [{"block_id": block["block_id"], "anchor": block["key"], "title": block["title"]}
                                                 for block in report["blocks"][-2:]], "evidence": evidence},
        "parameters": {"raw": "10 g", "value": [{"role": "sample_mass", "label": "试样质量", "comparator": "≤", "value": "10", "unit": "g",
                                                       "raw": "称取 10 g", "source_block_id": "formula"}], "evidence": evidence},
        "formulas": {"raw": "", "value": [{"block_id": "formula", "anchor": "formula", "title": "合成公式"}], "evidence": evidence},
        "principle": {"raw": "", "value": [{"block_id": "formula", "anchor": "wrong-anchor", "title": "合成公式"}], "evidence": evidence}}
    output = tmp_path / "viewer/候选知识.html"
    original = deepcopy(report)
    write_viewer(report, output, source)
    assert report == original
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser test runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_knowledge_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
