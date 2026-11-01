"""Emit an auditable JSON failure report for a completed evaluation run."""

from __future__ import annotations

import argparse
import json

from app.repositories.factory import get_repository


def category(result) -> str:
    details = result.details
    diagnostics = details.get("diagnostics") or {}
    if details.get("reason_code") == "ACL_CONTEXT_MISMATCH" or result.metrics.get("acl_leakage"):
        return "ACL_MISMATCH_FALLBACK"
    if diagnostics.get("domain_intent") == "unsupported":
        return "OUT_OF_SCOPE_SEMANTIC_MATCH"
    if details.get("reason_code") == "CONFLICTING_POLICIES":
        return "CONFLICT_NOT_DETECTED"
    if details.get("reason_code") == "LOW_SCORE_MARGIN":
        return "LOW_SCORE_MARGIN"
    if details.get("reason_code") in {"MISSING_FACT", "MISSING_CONTEXT"}:
        return "INSUFFICIENT_FACT_COVERAGE"
    if details.get("hit_policy_ids") and not details.get("citation_policy_ids"):
        return "WRONG_POLICY_FAMILY"
    return "QUERY_ANALYSIS_ERROR"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="bge-m3-v1")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    repository = get_repository()
    runs = repository.list_evaluation_runs("tenant_demo")
    run = next(
        (
            item
            for item in runs
            if (not args.run_id or item.evaluation_run_id == args.run_id)
            and item.config_snapshot.get("retrieval_profile_id") == args.profile
            and item.status == "completed"
        ),
        None,
    )
    if run is None:
        raise RuntimeError(f"No completed evaluation run found for {args.profile}")
    failures = []
    for result in repository.list_evaluation_results(run.evaluation_run_id, run.tenant_id):
        if result.passed:
            continue
        diagnostics = result.details.get("diagnostics") or {}
        candidates = result.details.get("candidates") or []
        failures.append(
            {
                "case_id": result.case_id,
                "case_type": result.details.get("case_type"),
                "query": result.details.get("query"),
                "filters": result.details.get("filters"),
                "expected_decision": result.details.get("expected_decision"),
                "actual_decision": result.details.get("actual_decision"),
                "reason_code": result.details.get("reason_code"),
                "failure_category": category(result),
                "top_5": candidates[:5],
                "top1_top2_margin": diagnostics.get("top1_top2_margin"),
                "required_facts": diagnostics.get("required_facts"),
                "fact_coverage_by_policy_family": diagnostics.get(
                    "fact_coverage_by_policy_family"
                ),
                "explicit_acl_context": diagnostics.get("explicit_acl_context"),
                "gate_rule_id": diagnostics.get("gate_rule_id"),
                "failures": result.details.get("failure_reasons"),
            }
        )
    print(
        json.dumps(
            {
                "run_id": run.evaluation_run_id,
                "profile": args.profile,
                "failure_count": len(failures),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
