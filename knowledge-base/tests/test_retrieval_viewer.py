from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from food_kb.common import file_hash
from food_kb.retrieval.viewer import write_viewer


def viewer_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "合成来源.txt").write_text("SYNTHETIC source, not an actual food standard.", encoding="utf-8")
    asset = file_hash(source / "合成来源.txt")
    unsafe = '<script>window.PWNED=1</script><img src="https://example.invalid/leak" onerror="window.PWNED=2"><iframe src="https://example.invalid/frame"></iframe>'
    evidence = {"asset_id": asset, "quote": "合成原文" + unsafe, "locator": {"pdf_pages": [1], "line_ranges": [[2, 4]]},
                "locator_quality": "synthetic_page_and_line"}
    hit = {"record_id": "SYNTHETIC-apple", "record_type": "classification", "rank": 1, "score": 5,
           "exact": {"name": "苹果" + unsafe, "designation": "SYNTHETIC 1-2020", "year": 2020,
                     "method_standards": ["SYNTHETIC METHOD"], "judgment_standards": ["SYNTHETIC LIMIT"]},
           "review_state": "review_pending", "applicability": "unknown", "executable": False,
           "conditions": {"op": "unresolved", "text": "生产工艺未知"},
           "condition_checks": [{"status": "unknown", "reason": "缺工艺"}],
           "gap_ids": ["SYNTHETIC-gap"], "gaps": [{"kind": "review_pending", "reason": "未获专家审核"}],
           "sources": [evidence], "matches": [{"field": "name", "term": "苹果", "raw_term": "苹果", "span": [0, 2],
                                                    "matched_text": "苹果" + unsafe, "match_kind": "exact_name", "span_precision": "exact"}]}
    result = {"schema_version": "retrieval-result-1", "algorithm_version": "retrieval-engine-1", "generation_id": "SYNTHETIC",
              "channel": "lexical", "status": "success", "query": {"raw": "苹果", "normalized": "苹果", "mode": "zh_ngram_phrase", "tokens": ["苹果"]},
              "hits": [hit], "total_count": 1, "offset": 0, "limit": 10, "next_offset": None,
              "scope_complete": False, "unknown_conditions": ["process"], "trace": {"mode": "fixture"}, "empty_reason": None}
    juice = deepcopy(result)
    juice["hits"][0]["record_id"] = "SYNTHETIC-juice"
    juice["hits"][0]["exact"]["name"] = "苹果汁"
    juice["hits"][0]["matches"][0].update(match_kind="related_name", matched_text="苹果汁")
    exact = deepcopy(result)
    exact.update(channel="exact", query={"raw": "ＳＹＮＴＨＥＴＩＣ　１－２０２０", "normalized": "SYNTHETIC 1-2020", "filters": {"designation": "SYNTHETIC 1-2020"}})
    empty = dict(result, status="empty", hits=[], total_count=0, empty_reason="filter_mismatch")
    failed = dict(result, status="failed", hits=[], total_count=None, errors=[{"code": "INDEX_FAULT", "message": "合成索引故障"}], empty_reason=None)
    degraded = dict(result, status="degraded", index_trace={"fallback": "literal_raw", "index_status": "failed"})
    cases = [{"id": "synthetic-exact", "title": "全角精确查询", "kind": "exact", "synthetic": True, "input": {"standard": "ＳＹＮＴＨＥＴＩＣ　１－２０２０"}, "result": exact},
             {"id": "synthetic-apple", "title": "苹果与苹果汁边界", "kind": "lexical", "synthetic": True, "result": juice},
             {"id": "synthetic-comparison", "title": "三方案对照", "kind": "comparison", "synthetic": True,
              "result": {"results_by_strategy": {"literal_raw": empty, "normalized_phrase": result, "zh_ngram_phrase": juice}}},
             {"id": "synthetic-failed", "title": "索引故障", "kind": "failure", "synthetic": True, "result": failed},
             {"id": "synthetic-degraded", "title": "索引降级", "kind": "failure", "synthetic": True, "result": degraded}]
    snapshot = {"generation_id": "SYNTHETIC", "processor_version": "synthetic-source", "summary": {"record_count": 1},
                "records": [hit], "gap_lookup": {"SYNTHETIC-gap": hit["gaps"][0]}, "postings": {"苹果": ["SYNTHETIC-apple"]},
                "sources": [{"source_key": "合成来源.txt", "asset_id": asset, "sha256": asset}], "scope_complete": False, "executable": False}
    evaluation = {"schema_version": "retrieval-evaluation-1", "summary": [], "environment": {"fixture": "SYNTHETIC"}, "expert_reviewed": False}
    return source, snapshot, cases, evaluation


def read_payload(output):
    return json.loads(output.read_text(encoding="utf-8").split('<script id="viewer-data" type="application/json">', 1)[1].split('</script>', 1)[0])


def test_retrieval_viewer_freezes_queries_without_embedding_corpus(tmp_path):
    source, snapshot, cases, evaluation = viewer_fixture(tmp_path)
    original = deepcopy((snapshot, cases, evaluation))
    output = tmp_path / "viewer/检索结果.html"
    write_viewer(snapshot, cases, output, source, evaluation)
    text = output.read_text(encoding="utf-8")
    assert "<script>window.PWNED" not in text and "\\u003cscript\\u003e" in text
    assert "connect-src 'none'" in text
    payload = read_payload(output)
    assert payload["cases"] == cases and payload["evaluation"] == evaluation
    assert "records" not in payload["snapshot"] and "gap_lookup" not in payload["snapshot"]
    assert set(payload["omitted_global_fields"]) >= {"records", "gap_lookup", "postings"}
    assert payload["source_files"]["合成来源.txt"]["status"] == "hash_verified"
    assert (snapshot, cases, evaluation) == original


def test_retrieval_viewer_rejects_overwrite_paths_and_hash_mismatch(tmp_path):
    source, snapshot, cases, evaluation = viewer_fixture(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        write_viewer(snapshot, cases, source / "overwrite.html", source)
    snapshot["sources"] += [{"source_key": "../outside.json", "sha256": "a" * 64},
                            {"source_key": "missing.json", "sha256": "a" * 64}]
    snapshot["sources"][0]["sha256"] = "b" * 64
    output = tmp_path / "viewer/检索结果.html"
    write_viewer(snapshot, cases, output, source, evaluation)
    files = read_payload(output)["source_files"]
    assert files["../outside.json"] == {"status": "path_rejected", "url": None}
    assert files["missing.json"] == {"status": "missing", "url": None}
    assert files["合成来源.txt"] == {"status": "hash_mismatch", "url": None}


def test_retrieval_viewer_real_edge_queries_failures_download_and_mobile(tmp_path):
    source, snapshot, cases, evaluation = viewer_fixture(tmp_path)
    output = tmp_path / "viewer/检索结果.html"
    write_viewer(snapshot, cases, output, source, evaluation)
    node = Path("D:/nodejs/node.exe")
    edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
    if not node.is_file() or not edge.is_file():
        pytest.skip("The documented local Edge/Node browser runtime is unavailable.")
    result = subprocess.run([str(node), str(Path(__file__).with_name("check_retrieval_viewer.cjs")), str(output), str(output.parent)],
                            capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    checks = json.loads((output.parent / "retrieval-viewer-checks.json").read_text(encoding="utf-8"))
    assert checks["passed"] is True and checks["cases_checked"] == len(cases)
    assert checks["mobile_width"] == 390
    assert checks["page_errors"] == [] and checks["remote_requests"] == []
