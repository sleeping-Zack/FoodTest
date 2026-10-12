from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from food_kb.common import file_hash
from food_kb.versions.viewer import write_viewer


def viewer_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "合成依据.pdf").write_bytes(b"%PDF-1.4 SYNTHETIC viewer source fixture")
    asset = file_hash(source / "合成依据.pdf")
    unsafe = '<script>window.PWNED=1</script><img src="https://example.invalid/leak" onerror="window.PWNED=2"><iframe src="https://example.invalid/frame"></iframe>'
    evidence = {"source_key": "合成依据.pdf", "source_asset_id": asset, "json_pointer": "/synthetic/0",
                "quote": "合成工程依据：不代表实际标准。" + unsafe,
                "locator": {"pdf_page": 1, "location_status": "paragraph_span"}, "locator_quality": "page_claim"}
    versions = [{"version_id": "synthetic-" + str(year), "document_id": "synthetic-family", "designation": "SYNTHETIC " + str(year),
                 "title": "合成标准" + unsafe, "family": "SYNTHETIC", "identity_state": "unverified", "review_state": "review_pending",
                 "is_amendment": year == 2022, "body_assets": [] if year == 2022 else [{"asset_id": asset, "source_key": "合成依据.pdf", "role": "original"}],
                 "date_claims": [{"role": "implementation", "value": str(year) + "-01-01", "raw_value": str(year) + "年1月1日", "verification_state": "unverified", "evidence": [evidence]}],
                 "status_claims": [], "policies": [], "evidence": [evidence]} for year in (2021, 2022)]
    policy = {"policy_id": "synthetic-policy", "date_role": "production", "status": "unknown", "date_result": {"status": "unknown"},
              "bounds": {"from": {"value": "2021-01-01", "inclusive": True}, "until": None}, "evidence": [evidence],
              "issues": [{"code": "BUSINESS_DATE_UNRESOLVED", "message": "缺业务日期角色依据"}], "condition_result": {"status": "unknown", "missing_facts": ["process"]}}
    resolution = {"schema_version": "version-resolution-1", "algorithm_version": "synthetic", "snapshot_id": "SYNTHETIC", "status": "unknown",
                  "time_context": {"dates": [{"role": "production", "value": "2021-05-01", "raw_value": "2021年5月1日", "source_ref": "synthetic:controlled", "verification_state": "verified", "evidence": [evidence]}, {"role": "export", "value": "2026-10-12"}]},
                  "candidates": [dict(v, status="unknown", policies_checked=[policy], gaps=[{"code": "APPLICABILITY_POLICY_MISSING", "message": "制度依据待补"}], date_claim_conflicts=[]) for v in versions],
                  "selected_version_id": None, "executable": False, "scope_complete": False}
    ref = {"reference_id": "synthetic-reference", "from_version_id": "synthetic-2021", "target": {"designation": "SYNTHETIC 2022", "version_ids": ["synthetic-2022"]},
           "nature": {"value": "normative", "evidence": [evidence]}, "scope": {"value": "clause", "refs": ["2"], "evidence": [evidence]},
           "binding": {"datedness": "dated", "designation": "SYNTHETIC 2022", "version_ids": ["synthetic-2022"], "evidence": [evidence]},
           "condition": {"state": "unresolved", "evidence": [evidence]}, "evidence": [evidence]}
    expansion = {"schema_version": "reference-expansion-1", "snapshot_id": "SYNTHETIC", "status": "incomplete", "visited_version_ids": ["synthetic-2021"],
                 "trace": [{"status": "unknown", "reference": ref, "from_version_id": "synthetic-2021", "target_version_id": "synthetic-2022", "issues": [{"code": "REFERENCE_CONDITION_UNKNOWN", "message": "条件未知，不展开"}]}],
                 "cycles": [{"path": ["synthetic-2021", "synthetic-2022", "synthetic-2021"]}], "unexpanded": [{"code": "REFERENCE_BUDGET", "reference_id": ref["reference_id"]}],
                 "gaps": [{"kind": "reference_unresolved", "reason": "合成重复缺口，来源不同但理由相同", "gap_id": "synthetic-" + str(i)} for i in range(20)],
                 "budget": {"max_nodes": 2}, "scope_complete": False, "executable": False}
    impact = {"schema_version": "version-impact-1", "old_snapshot_id": "SYNTHETIC-old", "new_snapshot_id": "SYNTHETIC-new", "history_unchanged": True,
              "changes": [{"kind": "version", "id": "synthetic-2022", "action": "changed"}],
              "affected": [{"kind": "classification", "id": "synthetic-category", "action": "recheck"}, {"kind": "requirement", "id": "synthetic-requirement", "action": "recheck"}],
              "trace": [{"dependent": {"kind": "requirement", "id": "synthetic-requirement"}, "depends_on": {"kind": "version", "id": "synthetic-2022"}, "evidence": [evidence]}],
              "scope_complete": False, "executable": False}
    snapshot = {"schema_version": "version-reference-1", "processor_version": "synthetic", "snapshot_id": "SYNTHETIC", "versions": versions,
                "references": [ref], "events": [], "dependencies": [], "summary": {"reference_count": 1},
                "sources": [{"source_key": "合成依据.pdf", "asset_id": asset, "sha256": asset, "status": "hash_verified", "role": "original"}],
                "gaps": [{"gap_id": "synthetic-gap", "kind": "body_missing", "target": {"kind": "version", "id": "synthetic-2022"}, "reason": "只有修改单，主体缺失", "evidence": [evidence]}],
                "scope_complete": False, "executable": False, "review_state": "review_pending"}
    examples = [{"id": "synthetic-date", "title": "2021日期与2026导出", "kind": "resolution", "synthetic": True, "result": resolution},
                {"id": "synthetic-loop", "title": "引用循环与预算", "kind": "references", "synthetic": True, "result": expansion},
                {"id": "synthetic-impact", "title": "增量影响旧历史保留", "kind": "impact", "synthetic": True, "result": impact}]
    return source, snapshot, examples


def read_payload(output):
    return json.loads(output.read_text(encoding="utf-8").split('<script id="viewer-data" type="application/json">', 1)[1].split('</script>', 1)[0])


def test_versions_viewer_freezes_results_without_duplicating_global_graph(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    original = deepcopy((snapshot, examples))
    output = tmp_path / "viewer/版本引用与缺口.html"
    write_viewer(snapshot, examples, output, source)
    html = output.read_text(encoding="utf-8")
    assert "<script>window.PWNED" not in html and "\\u003cscript\\u003e" in html
    assert "connect-src 'none'" in html
    payload = read_payload(output)
    assert payload["examples"] == examples and payload["snapshot"]["versions"] == snapshot["versions"]
    assert "references" not in payload["snapshot"] and "references" in payload["omitted_global_fields"]
    assert payload["source_files"]["合成依据.pdf"]["status"] == "hash_verified"
    assert (snapshot, examples) == original


def test_versions_viewer_rejects_overwrite_and_untrusted_source_links(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_viewer(snapshot, examples, source / "overwrite.html", source)
    assert not (source / "overwrite.html").exists()
    snapshot["sources"] += [{"source_key": "../outside.json", "asset_id": "a" * 64, "status": "hash_verified"},
                            {"source_key": "missing.pdf", "asset_id": "b" * 64, "status": "hash_verified"},
                            {"source_key": "untrusted.pdf", "asset_id": "b" * 64, "status": "unverified"}]
    (source / "untrusted.pdf").write_text("SYNTHETIC", encoding="utf-8")
    snapshot["sources"][0]["sha256"] = "c" * 64
    output = tmp_path / "viewer/版本引用与缺口.html"
    write_viewer(snapshot, examples, output, source)
    payload = read_payload(output)
    assert payload["source_files"]["../outside.json"] == {"status": "path_rejected", "url": None}
    assert payload["source_files"]["missing.pdf"] == {"status": "missing", "url": None}
    assert payload["source_files"]["untrusted.pdf"] == {"status": "unverified", "url": None}
    assert payload["source_files"]["合成依据.pdf"] == {"status": "hash_mismatch", "url": None}


def test_versions_viewer_real_browser_frozen_scenes_sources_download_and_mobile(tmp_path):
    source, snapshot, examples = viewer_fixture(tmp_path)
    output = tmp_path / "viewer/版本引用与缺口.html"
    write_viewer(snapshot, examples, output, source)
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_versions_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = json.loads((output.parent / "versions-viewer-checks.json").read_text(encoding="utf-8"))
    assert checks["remote_requests"] == [] and checks["page_errors"] == []
    assert checks["examples_checked"] == len(examples)
    assert checks["mobile_width"] == 390 and checks["passed"] is True
