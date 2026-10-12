import pytest

from food_kb.common import KBError
from food_kb.retrieval.evaluation import evaluate_queries


def case():
    return {"query_id": "source-labeled", "expected_ids": ["a", "b"], "forbidden_ids": ["juice"],
            "label_source": {"kind": "manual_source_selection", "source": "SYNTHETIC fixture"},
            "rationale": "Controlled apple/juice identity boundary; not expert labels.", "synthetic": True,
            "results_by_strategy": {"raw": {"status": "ok", "latency_ms": 2, "hits": [{"record_id": "a"}, {"record_id": "juice"}, {"record_id": "other"}]},
                                    "chinese": {"status": "failed", "reason": "index_fault", "hits": []}}}


def test_evaluation_preserves_unjudged_and_failed_denominators():
    result = evaluate_queries([case()], ["raw", "chinese", "missing"], k=3)
    row = result["queries"][0]
    assert row["recall_at_k"] == .5
    assert row["precision_at_k_lower_bound"] == 1 / 3
    assert row["precision_at_k_upper_bound"] == 2 / 3
    assert row["judged_precision_at_k"] == .5
    assert row["label_coverage"] == 2 / 3
    assert row["unjudged_ids"] == ["other"] and row["incorrect_ids"] == ["juice"]
    assert result["summary"][1]["expected_label_count"] == 2
    assert result["summary"][1]["micro_recall_at_k"] == 0
    assert result["summary"][1]["failed_or_missing_queries"] == 1
    assert result["summary"][2]["micro_recall_at_k"] == 0
    assert result["expert_reviewed"] is False


def test_evaluation_cutoff_and_empty_labels_are_explicit():
    data = case()
    empty = dict(data, query_id="negative", expected_ids=[], results_by_strategy={"raw": {"status": "ok", "hits": []}})
    result = evaluate_queries([data, empty], ["raw"], k=1)
    assert result["queries"][0]["returned_ids"] == ["a"]
    assert result["queries"][0]["missing_expected_ids"] == ["b"]
    assert result["queries"][1]["recall_at_k"] is None
    assert result["summary"][0]["queries"] == 2
    assert result["summary"][0]["queries_with_expected_labels"] == 1


def test_evaluation_database_performance_timing_is_preserved():
    data = case()
    data["results_by_strategy"]["raw"].pop("latency_ms")
    data["results_by_strategy"]["raw"]["performance"] = {"elapsed_ms": 12.25}
    result = evaluate_queries([data], ["raw"])
    assert result["queries"][0]["latency_ms"] == 12.25
    assert result["summary"][0]["mean_latency_ms"] == 12.25


def test_evaluation_failed_stale_hits_do_not_improve_recall():
    data = case()
    data["results_by_strategy"]["chinese"]["hits"] = [{"record_id": "a"}, {"record_id": "b"}]
    result = evaluate_queries([data], ["chinese"])
    row = result["queries"][0]
    assert row["returned_ids"] == [] and row["relevant_ids"] == []
    assert row["stale_hits_discarded_ids"] == ["a", "b"]
    assert row["recall_at_k"] == 0 and row["missing_expected_ids"] == ["a", "b"]
    assert result["summary"][0]["expected_label_count"] == 2
    assert result["summary"][0]["micro_recall_at_k"] == 0


@pytest.mark.parametrize("k", [True, 0, -1, 1.5])
def test_evaluation_invalid_cutoff_is_rejected(k):
    with pytest.raises(KBError, match="positive integer"):
        evaluate_queries([case()], ["raw"], k=k)


def test_evaluation_rejects_unattributed_conflicting_or_duplicate_labels():
    for data in (dict(case(), label_source=None), dict(case(), rationale=""), dict(case(), forbidden_ids=["a"])):
        with pytest.raises(KBError, match="Labels"):
            evaluate_queries([data], ["raw"])
    with pytest.raises(KBError, match="unique query_id"):
        evaluate_queries([case(), case()], ["raw"])
    data = case()
    data["results_by_strategy"]["raw"]["hits"].append({"record_id": "a"})
    with pytest.raises(KBError, match="unique record IDs"):
        evaluate_queries([data], ["raw"])
