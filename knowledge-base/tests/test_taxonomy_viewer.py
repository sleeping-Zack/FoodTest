from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from food_kb.common import file_hash
from food_kb.taxonomy.viewer import write_viewer


def viewer_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "合成原件.pdf").write_bytes(b"%PDF-1.4 SYNTHETIC source-link fixture")
    asset = file_hash(source / "合成原件.pdf")
    unsafe = '<script>window.PWNED=1</script><img src="https://example.invalid/leak" onerror="window.PWNED=2"><iframe src="https://example.invalid/frame"></iframe>'
    evidence = {"source_key": "合成原件.pdf", "source_asset_id": asset, "json_pointer": "/synthetic/0",
                "quote": "合成依据：仅用于界面工程验证。" + unsafe,
                "locator": {"pdf_page": 1, "location_status": "paragraph_span"}, "locator_quality": "page_claim"}
    condition = {"status": "unknown", "raw": "合成条件：需要工艺。", "missing_facts": ["process"], "evidence": [evidence]}
    requirement = {"id": "synthetic-requirement", "semantic": {"project": "合成检验项目", "basis_raw": "SYNTHETIC 依据", "method_raw": "SYNTHETIC 方法"},
                   "status": "unknown", "source_path": [{"kind": "node", "category_id": "shared-id", "label": "合成品种", "evidence": [evidence]},
                        {"kind": "relation", "relation_kind": "manual_bridge", "evidence": [evidence]}],
                   "condition_result": condition, "exclusions_checked": [], "evidence": [evidence],
                   "review_state": "review_pending", "executable": False, "mandatory_for_task": False}
    namespaces = [{"namespace": name, "standard": name + "合成标准", "standard_version_id": name + "-version",
                   "coverage_state": "partial", "nodes": [{"id": "shared-id", "name": name + "类别",
                        "definition": {"raw": name + "独立定义" + unsafe}, "evidence": [evidence]}]} for name in ["体系甲", "体系乙"]]
    snapshot = {"snapshot_id": "SYNTHETIC", "schema_version": "taxonomy-1", "processor_version": "synthetic",
                "coverage_state": "partial", "review_state": "review_pending", "namespaces": namespaces,
                "sources": [{"source_key": "合成原件.pdf", "asset_id": asset, "sha256": asset, "status": "hash_verified", "role": "original"}]}
    examples = []
    for index, name in enumerate(["年糕", "苹果", "苹果汁", "萝卜", "萝卜叶", "牛奶", "牛奶"]):
        facts = {"food_name": {"raw_value": name, "normalized_value": name, "source_ref": "synthetic:controlled",
                               "role": "sample_fact", "verification_state": "verified"}}
        if index == 6:
            facts["process"] = {"raw_value": "灭菌", "normalized_value": "sterilized", "source_ref": "synthetic:controlled",
                                "role": "sample_fact", "verification_state": "verified"}
        result_namespaces = []
        for ns in namespaces:
            branch = {"category_id": "shared-id", "label": ns["namespace"] + "类别", "status": "unknown",
                      "condition_result": condition, "path": [{"category_id": "shared-id", "label": name}, {"category_id": "parent", "label": "上级"}],
                      "supporting_evidence": [evidence], "exclusions_checked": [], "unresolved_conditions": [condition],
                      "missing_facts": ["process"], "requirements": [requirement] if name == "年糕" else [],
                      "association_count": 1 if name == "年糕" else None,
                      "inheritance_paths": [requirement["source_path"]] if name == "年糕" else [], "review_state": "review_pending"}
            result_namespaces.append({"namespace": ns["namespace"], "standard_version_id": ns["standard_version_id"], "status": "unknown",
                      "coverage_state": "partial", "branches": [branch], "common_requirements": [], "common_associations": [],
                      "branch_specific_requirements": [], "comparison_unknown": [], "scope_complete": False})
        result = {"schema_version": "classification-1", "snapshot_id": "SYNTHETIC", "food_name": name, "facts": facts,
                  "namespaces": result_namespaces, "missing_facts": ["process"], "scope_complete": False,
                  "executable": False, "mandatory_for_task": False}
        examples.append({"id": "synthetic-" + str(index), "title": name + ("：工艺已提供" if index == 6 else "：工艺待补"),
                         "food_name": name, "scenario_label": "工艺已提供" if index == 6 else "工艺待补", "note": "受控工程输入，不代表真实样品已核验。", "result": result})
    return source, snapshot, examples


def test_taxonomy_viewer_safe_data_preserves_frozen_inputs(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    original = deepcopy((snapshot, examples))
    output = tmp_path / "viewer/分类与适用结果.html"
    write_viewer(snapshot, examples, output, source)
    html = output.read_text(encoding="utf-8")
    assert "<script>window.PWNED" not in html and "\\u003cscript\\u003e" in html
    assert "connect-src 'none'" in html
    payload = json.loads(html.split('<script id="viewer-data" type="application/json">', 1)[1].split('</script>', 1)[0])
    assert payload["snapshot"] == snapshot and payload["examples"] == examples
    assert payload["source_files"]["合成原件.pdf"]["status"] == "hash_verified"
    assert "source_files" not in snapshot
    assert (snapshot, examples) == original


def test_taxonomy_viewer_refuses_source_overwrite(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_viewer(snapshot, examples, source / "overwrite.html", source)
    assert not (source / "overwrite.html").exists()


def test_taxonomy_viewer_untrusted_missing_and_changed_source_links_are_blocked(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    (tmp_path / "outside.json").write_text("outside", encoding="utf-8")
    snapshot["sources"] += [{"source_key": "../outside.json", "asset_id": file_hash(tmp_path / "outside.json"), "status": "hash_verified"},
                            {"source_key": "missing.pdf", "asset_id": "b" * 64, "status": "hash_verified"}]
    snapshot["sources"][0]["sha256"] = "a" * 64
    output = tmp_path / "viewer/分类与适用结果.html"
    write_viewer(snapshot, examples, output, source)
    payload = json.loads(output.read_text(encoding="utf-8").split('<script id="viewer-data" type="application/json">', 1)[1].split('</script>', 1)[0])
    assert payload["source_files"]["../outside.json"] == {"status": "path_rejected", "url": None}
    assert payload["source_files"]["missing.pdf"] == {"status": "missing", "url": None}
    assert payload["source_files"]["合成原件.pdf"] == {"status": "hash_mismatch", "url": None}


def test_taxonomy_viewer_browser_queries_sources_downloads_and_mobile(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    output = tmp_path / "viewer/分类与适用结果.html"
    write_viewer(snapshot, examples, output, source)
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_taxonomy_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = json.loads((output.parent / "taxonomy-viewer-checks.json").read_text(encoding="utf-8"))
    assert checks["remote_requests"] == [] and checks["page_errors"] == []
    assert checks["examples_checked"] == len(examples)
    assert checks["mobile_width"] == 390 and checks["passed"] is True
