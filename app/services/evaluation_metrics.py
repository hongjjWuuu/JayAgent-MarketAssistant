from __future__ import annotations

import math
import re
from statistics import mean
from uuid import uuid4

from app.domain.models import EvaluationCase, EvaluationResult, RetrievalSnapshot
from app.services.policy_service import AnswerabilityDecision

QUALITY_THRESHOLDS = {
    "recall_at_1": 0.75,
    "recall_at_3": 0.85,
    "recall_at_5": 0.90,
    "mrr": 0.80,
    "citation_policy_accuracy": 1.0,
    "citation_version_accuracy": 1.0,
    "citation_chunk_accuracy": 0.95,
    "answerability_recall": 0.90,
    "unanswerable_block_rate": 1.0,
    "reason_code_accuracy": 0.90,
}


def _mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def percentile(values: list[int], target: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(target * len(ordered)) - 1)])


def evaluate_case(
    tenant_id: str,
    run_id: str,
    case: EvaluationCase,
    decision: AnswerabilityDecision,
    snapshot: RetrievalSnapshot | None,
) -> EvaluationResult:
    expected = case.expected
    expected_decision = str(expected.get("decision") or "answer")
    expected_reasons = set(expected.get("reason_codes") or [])
    relevant = set(
        expected.get("relevant_policy_version_ids")
        or expected.get("policy_version_ids")
        or []
    )
    required_policy_citations = set(expected.get("required_citation_policy_ids") or [])
    required_version_citations = set(
        expected.get("required_citation_policy_version_ids") or relevant
    )
    relevant_chunks = set(
        expected.get("required_citation_chunk_ids")
        or expected.get("relevant_chunk_ids")
        or []
    )
    forbidden_policies = set(expected.get("forbidden_policy_ids") or [])
    forbidden_versions = set(expected.get("forbidden_policy_version_ids") or [])
    forbidden_chunks = set(expected.get("forbidden_chunk_ids") or [])
    hit_policy_ids = [hit.policy_id for hit in decision.hits]
    hit_ids = [hit.policy_version_id for hit in decision.hits]
    hit_chunk_ids = [hit.chunk_id for hit in decision.hits]
    cited_hits = [hit for hit in decision.hits if hit.chunk_id in decision.evidence_chunk_ids]
    cited_policy_ids = {hit.policy_id for hit in cited_hits}
    cited_version_ids = {hit.policy_version_id for hit in cited_hits}
    cited_chunk_ids = {hit.chunk_id for hit in cited_hits}
    rank = next((index + 1 for index, item in enumerate(hit_ids) if item in relevant), None)
    recall1 = float(not relevant or bool(relevant.intersection(hit_ids[:1])))
    recall3 = float(not relevant or bool(relevant.intersection(hit_ids[:3])))
    recall5 = float(not relevant or bool(relevant.intersection(hit_ids[:5])))
    decision_ok = decision.decision == expected_decision
    reason_ok = not expected_reasons or decision.reason_code in expected_reasons
    citation_policy_ok = expected_decision != "answer" or required_policy_citations.issubset(
        cited_policy_ids
    )
    citation_version_ok = expected_decision != "answer" or required_version_citations.issubset(
        cited_version_ids
    )
    citation_chunk_ok = (
        expected_decision != "answer"
        or not relevant_chunks
        or bool(relevant_chunks.intersection(cited_chunk_ids))
    )
    forbidden_policy_hits = sorted(forbidden_policies.intersection(hit_policy_ids))
    forbidden_version_hits = sorted(forbidden_versions.intersection(hit_ids))
    forbidden_chunk_hits = sorted(forbidden_chunks.intersection(hit_chunk_ids))
    forbidden_hits = forbidden_policy_hits + forbidden_version_hits + forbidden_chunk_hits
    tenant_leak = any(hit.tenant_id != tenant_id for hit in decision.hits)
    filters = case.input_data.get("filters") or {}
    acl_leak = any(
        any(
            values
            and (
                not filters.get(key)
                or str(filters[key]) not in {str(value) for value in values}
            )
            for key, values in (hit.acl or {}).items()
        )
        for hit in decision.hits
    )
    invalid_status_hit = any(hit.policy_status != "published" for hit in decision.hits)
    old_version_hit = bool(forbidden_version_hits) or invalid_status_hit
    false_answer = expected_decision != "answer" and decision.decision == "answer"
    pii_redacted = bool(snapshot) and not re.search(
        r"(?<!\d)1\d{10}(?!\d)|[\w.+-]+@[\w.-]+", snapshot.query_redacted
    )
    checks = {
        "decision_mismatch": decision_ok,
        "reason_code_mismatch": reason_ok,
        "relevant_policy_not_in_top5": expected_decision != "answer" or recall5 == 1.0,
        "citation_policy_mismatch": citation_policy_ok,
        "citation_version_mismatch": citation_version_ok,
        "citation_chunk_mismatch": citation_chunk_ok,
        "forbidden_policy_hit": not forbidden_policy_hits,
        "forbidden_version_hit": not forbidden_version_hits,
        "forbidden_chunk_hit": not forbidden_chunk_hits,
        "invalid_policy_status_hit": not invalid_status_hit,
        "tenant_leakage": not tenant_leak,
        "acl_leakage": not acl_leak,
        "false_answer": not false_answer,
        "pii_not_redacted": pii_redacted,
    }
    failures = [name for name, passed in checks.items() if not passed]
    metrics = {
        "recall_at_1": recall1,
        "recall_at_3": recall3,
        "recall_at_5": recall5,
        "reciprocal_rank": 1 / rank if rank else 0.0,
        "decision_correct": decision_ok,
        "reason_code_correct": reason_ok,
        "citation_policy_correct": citation_policy_ok,
        "citation_version_correct": citation_version_ok,
        "citation_chunk_correct": citation_chunk_ok,
        "forbidden_version_hit_count": len(forbidden_hits),
        "tenant_leakage": tenant_leak,
        "acl_leakage": acl_leak,
        "old_version_hit": old_version_hit,
        "false_answer": false_answer,
        "pii_redacted": pii_redacted,
        "latency_ms": snapshot.latency_ms if snapshot else 0,
        "semantic_only": bool(snapshot and snapshot.diagnostics.get("semantic_only")),
        "top1_top2_margin": float(
            snapshot.diagnostics.get("top1_top2_margin", 0) if snapshot else 0
        ),
        "single_policy_fact_coverage": bool(
            snapshot
            and any(
                item.get("complete")
                for item in snapshot.diagnostics.get(
                    "fact_coverage_by_policy_family", {}
                ).values()
            )
        ) if decision.analysis and decision.analysis.get("required_facts") else True,
        "unsupported_scope_blocked": (
            decision.analysis is not None
            and decision.analysis.get("domain_intent") == "unsupported"
            and decision.decision != "answer"
        ),
        "acl_mismatch_blocked": (
            decision.reason_code == "ACL_CONTEXT_MISMATCH"
            and decision.decision == "handoff"
        ),
    }
    return EvaluationResult(
        evaluation_result_id=f"eval_result_{uuid4().hex}",
        tenant_id=tenant_id,
        run_id=run_id,
        case_id=case.evaluation_case_id,
        passed=not failures,
        metrics=metrics,
        details={
            "case_type": case.case_type,
            "tags": case.tags,
            "expected_decision": expected_decision,
            "actual_decision": decision.decision,
            "expected_reason_codes": sorted(expected_reasons),
            "reason_code": decision.reason_code,
            "snapshot_id": decision.retrieval_snapshot_id,
            "hit_policy_version_ids": hit_ids,
            "hit_policy_ids": hit_policy_ids,
            "hit_chunk_ids": hit_chunk_ids,
            "citation_policy_ids": sorted(cited_policy_ids),
            "citation_policy_version_ids": sorted(cited_version_ids),
            "citation_chunk_ids": sorted(cited_chunk_ids),
            "forbidden_hits": forbidden_hits,
            "forbidden_policy_hits": forbidden_policy_hits,
            "forbidden_version_hits": forbidden_version_hits,
            "forbidden_chunk_hits": forbidden_chunk_hits,
            "failure_reasons": failures,
            "query": case.input_data.get("query", ""),
            "filters": case.input_data.get("filters") or {},
            "diagnostics": snapshot.diagnostics if snapshot else {},
            "candidates": snapshot.candidates[:5] if snapshot else [],
        },
    )


def summarize(results: list[EvaluationResult], cases: list[EvaluationCase]) -> dict:
    paired = list(zip(results, cases, strict=True))
    answerable = [r for r, c in paired if c.expected.get("decision") == "answer"]
    answered_answerable = [
        result for result in answerable if result.details["actual_decision"] == "answer"
    ]
    non_answerable = [r for r, c in paired if c.expected.get("decision") != "answer"]
    clarifications = [r for r, c in paired if c.expected.get("decision") == "clarify"]
    handoffs = [r for r, c in paired if c.expected.get("decision") == "handoff"]
    true_positive = sum(r.details["actual_decision"] == "answer" for r in answerable)
    false_positive = sum(r.metrics["false_answer"] for r in non_answerable)
    false_negative = len(answerable) - true_positive
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    latencies = [int(r.metrics["latency_ms"]) for r in results]
    confusion: dict[str, dict[str, int]] = {}
    for result in results:
        expected = str(result.details["expected_decision"])
        actual = str(result.details["actual_decision"])
        confusion.setdefault(expected, {}).setdefault(actual, 0)
        confusion[expected][actual] += 1
    by_case_type: dict[str, list[EvaluationResult]] = {}
    for result, case in paired:
        by_case_type.setdefault(case.case_type, []).append(result)
    semantic_only = [r for r in results if r.metrics.get("semantic_only")]
    acl_cases = [r for r, c in paired if "acl" in c.case_type.lower() or "acl" in c.tags]
    unsupported_cases = [
        r for r, c in paired
        if c.case_type in {"out_of_scope", "prompt_injection"}
        or "unsupported" in c.tags
    ]
    return {
        "case_count": len(results),
        "answerable_count": len(answerable),
        "nonanswerable_count": len(non_answerable),
        "passed": sum(result.passed for result in results),
        "recall_at_1": _mean([float(r.metrics["recall_at_1"]) for r in answerable]),
        "recall_at_3": _mean([float(r.metrics["recall_at_3"]) for r in answerable]),
        "recall_at_5": _mean([float(r.metrics["recall_at_5"]) for r in answerable]),
        "mrr": _mean([float(r.metrics["reciprocal_rank"]) for r in answerable]),
        # Citation quality and answerability recall are deliberately independent:
        # safely blocked answers reduce recall, but cannot contain a wrong citation.
        "citation_policy_accuracy": _mean(
            [float(r.metrics["citation_policy_correct"]) for r in answered_answerable]
        ),
        "citation_version_accuracy": _mean(
            [float(r.metrics["citation_version_correct"]) for r in answered_answerable]
        ),
        "citation_chunk_accuracy": _mean(
            [float(r.metrics["citation_chunk_correct"]) for r in answered_answerable]
        ),
        "answerability_precision": true_positive / precision_denominator if precision_denominator else 0.0,
        "answerability_recall": true_positive / recall_denominator if recall_denominator else 0.0,
        "clarify_accuracy": _mean([float(r.metrics["decision_correct"]) for r in clarifications]),
        "handoff_accuracy": _mean([float(r.metrics["decision_correct"]) for r in handoffs]),
        "unanswerable_block_rate": _mean([float(not r.metrics["false_answer"]) for r in non_answerable]),
        "reason_code_accuracy": _mean([float(r.metrics["reason_code_correct"]) for r in results]),
        "false_answer_rate": _mean([float(r.metrics["false_answer"]) for r in non_answerable]),
        "old_version_hit_rate": _mean([float(r.metrics["old_version_hit"]) for r in results]),
        "tenant_leakage_rate": _mean([float(r.metrics["tenant_leakage"]) for r in results]),
        "acl_leakage_rate": _mean([float(r.metrics["acl_leakage"]) for r in results]),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "confusion_matrix": confusion,
        "false_answer_rate_by_case_type": {
            key: _mean([float(item.metrics["false_answer"]) for item in values])
            for key, values in sorted(by_case_type.items())
        },
        "semantic_only_false_answer_rate": _mean(
            [float(item.metrics["false_answer"]) for item in semantic_only]
        ),
        "acl_mismatch_block_rate": _mean(
            [float(item.metrics.get("acl_mismatch_blocked", False)) for item in acl_cases]
        ) if acl_cases else 1.0,
        "single_policy_fact_coverage_rate": _mean(
            [float(item.metrics.get("single_policy_fact_coverage", False)) for item in answerable]
        ),
        "top1_top2_margin_distribution": {
            "p50": percentile(
                [int(float(item.metrics.get("top1_top2_margin", 0)) * 1_000_000) for item in results],
                0.50,
            ) / 1_000_000,
            "p95": percentile(
                [int(float(item.metrics.get("top1_top2_margin", 0)) * 1_000_000) for item in results],
                0.95,
            ) / 1_000_000,
        },
        "unsupported_scope_block_rate": _mean(
            [float(item.details["actual_decision"] != "answer") for item in unsupported_cases]
        ) if unsupported_cases else 1.0,
    }


def quality_failures(summary: dict) -> list[str]:
    retrieval_metrics = {
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "citation_policy_accuracy",
        "citation_version_accuracy",
        "citation_chunk_accuracy",
        "answerability_recall",
    }
    failures = [
        f"{metric}={summary.get(metric, 0):.4f} below {threshold:.4f}"
        for metric, threshold in QUALITY_THRESHOLDS.items()
        if not (
            int(summary.get("answerable_count", 0)) == 0 and metric in retrieval_metrics
        )
        and float(summary.get(metric, 0)) < threshold
    ]
    for metric in (
        "false_answer_rate",
        "old_version_hit_rate",
        "tenant_leakage_rate",
        "acl_leakage_rate",
    ):
        if float(summary.get(metric, 0)) != 0.0:
            failures.append(f"{metric} must be zero")
    if float(summary.get("p95_latency_ms", 0)) >= 500:
        failures.append("p95_latency_ms must be below 500")
    if int(summary.get("case_count", 0)) == 0:
        failures.append("evaluation dataset is empty")
    return failures
