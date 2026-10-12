"""Evaluate frozen retrieval runs against explicitly attributed engineering labels."""
from food_kb.common import KBError

FAILED_STATES = {"failure", "failed", "error", "unavailable", "missing"}


def evaluate_queries(cases, strategies, *, k=10, environment=None):
    """Keep unjudged hits and failed runs visible instead of inventing relevance."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise KBError("EVALUATION_K", "Evaluation k must be a positive integer.")
    strategies = list(strategies)
    if not strategies or len(strategies) != len(set(strategies)):
        raise KBError("EVALUATION_STRATEGIES", "Strategy names must be nonempty and unique.")
    rows = []
    seen = set()
    for case in cases:
        query_id = case.get("query_id")
        if not query_id or query_id in seen:
            raise KBError("EVALUATION_QUERY", "Each labeled query requires a unique query_id.")
        seen.add(query_id)
        expected = set(case.get("expected_ids", []))
        forbidden = set(case.get("forbidden_ids", []))
        if expected & forbidden or not case.get("label_source") or not case.get("rationale"):
            raise KBError("EVALUATION_LABELS", "Labels must be disjoint, attributed, and explained.")
        for strategy in strategies:
            result = case.get("results_by_strategy", {}).get(strategy)
            # A missing or failed result contributes zero recall; it is never dropped.
            status = result.get("status", "unknown") if result is not None else "missing"
            hits = result.get("hits", []) if result is not None else []
            stale_ids = []
            if status in FAILED_STATES:
                stale_ids = [hit.get("record_id", hit.get("id")) for hit in hits]
                hits = []
            ids = [hit.get("record_id", hit.get("id")) for hit in hits[:k]]
            if any(value is None for value in ids) or len(ids) != len(set(ids)):
                raise KBError("EVALUATION_HITS", "Ranked hits require nonempty unique record IDs.")
            returned = set(ids)
            relevant = returned & expected
            incorrect = returned & forbidden
            unjudged = returned - expected - forbidden
            rows.append({
                "query_id": query_id, "strategy": strategy, "k": k, "status": status,
                "synthetic": bool(case.get("synthetic", False)),
                "label_source": case["label_source"], "rationale": case["rationale"],
                "expected_ids": sorted(expected), "forbidden_ids": sorted(forbidden),
                "returned_ids": ids, "relevant_ids": sorted(relevant),
                "stale_hits_discarded_ids": stale_ids,
                "incorrect_ids": sorted(incorrect), "unjudged_ids": sorted(unjudged),
                "missing_expected_ids": sorted(expected - returned),
                "recall_at_k": len(relevant) / len(expected) if expected else None,
                "precision_at_k_lower_bound": len(relevant) / len(returned) if returned else None,
                "precision_at_k_upper_bound": (len(relevant) + len(unjudged)) / len(returned) if returned else None,
                "judged_precision_at_k": len(relevant) / (len(relevant) + len(incorrect)) if relevant or incorrect else None,
                "label_coverage": (len(relevant) + len(incorrect)) / len(returned) if returned else None,
                "latency_ms": result.get("latency_ms", result.get("elapsed_ms", result.get("performance", {}).get("elapsed_ms"))) if result else None,
                "empty_reason": result.get("empty_reason", result.get("reason")) if result else "run_missing",
            })
    summaries = []
    for strategy in strategies:
        subset = [row for row in rows if row["strategy"] == strategy]
        expected_count = sum(len(row["expected_ids"]) for row in subset)
        found_count = sum(len(row["relevant_ids"]) for row in subset)
        incorrect_count = sum(len(row["incorrect_ids"]) for row in subset)
        returned_count = sum(len(row["returned_ids"]) for row in subset)
        unjudged_count = sum(len(row["unjudged_ids"]) for row in subset)
        times = sorted(row["latency_ms"] for row in subset if isinstance(row["latency_ms"], (int, float)))
        summaries.append({
            "strategy": strategy, "queries": len(subset),
            "queries_with_expected_labels": sum(bool(row["expected_ids"]) for row in subset),
            "expected_label_count": expected_count, "relevant_hit_count": found_count,
            "incorrect_hit_count": incorrect_count, "unjudged_hit_count": unjudged_count,
            "returned_hit_count": returned_count,
            "micro_recall_at_k": found_count / expected_count if expected_count else None,
            "precision_at_k_lower_bound": found_count / returned_count if returned_count else None,
            "precision_at_k_upper_bound": (found_count + unjudged_count) / returned_count if returned_count else None,
            "judged_precision_at_k": found_count / (found_count + incorrect_count) if found_count or incorrect_count else None,
            "label_coverage": (found_count + incorrect_count) / returned_count if returned_count else None,
            "failed_or_missing_queries": sum(row["status"] in FAILED_STATES for row in subset),
            "degraded_queries": sum(row["status"] == "degraded" for row in subset),
            "latency_samples": len(times), "mean_latency_ms": sum(times) / len(times) if times else None,
            "max_latency_ms": max(times) if times else None,
        })
    return {"schema_version": "retrieval-evaluation-1", "k": k, "summary": summaries, "queries": rows,
            "environment": environment or {}, "label_kind": "engineering_source_labels",
            "expert_reviewed": False,
            "limitations": ["These engineering labels are explicitly selected source records, not expert food relevance judgments.",
                            "Precision uses the actual returned top-k denominator; lower/upper bounds keep unjudged hits visible.",
                            "Recall denominators include failed and missing runs. Empty-label queries have no recall denominator.",
                            "A hit does not establish applicability, complete requirements, or overall food accuracy."]}
