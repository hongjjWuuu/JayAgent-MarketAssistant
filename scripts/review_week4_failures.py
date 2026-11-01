"""Turn the latest immutable v2 failures into an auditable adjudication report."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.repositories.factory import get_repository


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="bge-m3-v2")
    parser.add_argument("--reviewed-by", default="week4-quality-review")
    parser.add_argument("--output", default="docs/week4_bge_v2_failure_adjudication.json")
    args = parser.parse_args()
    repository = get_repository()
    run = next((value for value in repository.list_evaluation_runs("tenant_demo")
                if value.status == "completed" and value.config_snapshot.get("retrieval_profile_id") == args.profile), None)
    if run is None:
        raise RuntimeError(f"No completed run for {args.profile}")
    cases = {case.evaluation_case_id: case for case in repository.list_evaluation_cases(
        "tenant_demo", run.dataset, run.dataset_version
    )}
    reviewed = []
    for result in repository.list_evaluation_results(run.evaluation_run_id, "tenant_demo"):
        if result.passed:
            continue
        case = cases.get(result.case_id)
        details = result.details
        diagnostics = details.get("diagnostics") or {}
        expected = case.expected if case else {}
        reviewed.append({
            "case_id": result.case_id,
            "query": details.get("query") or (case.input_data.get("query") if case else None),
            "filters": details.get("filters") or (case.input_data.get("filters") if case else {}),
            "original_expected_decision": details.get("expected_decision"),
            "actual_decision": details.get("actual_decision"),
            "system_error": details.get("actual_decision") != details.get("expected_decision"),
            "annotation_error": False,
            "correct_decision": expected.get("decision"),
            "reason_codes": expected.get("reason_codes", []),
            "required_facts": expected.get("required_facts", diagnostics.get("required_facts", [])),
            "policy_family_or_scope": diagnostics.get("requested_policy_scope"),
            "allowed_citations": expected.get("required_citation_chunk_ids", []),
            "forbidden_citations": expected.get("forbidden_chunk_ids", []),
            "review_reason": "Historical failure retained; Gate v4 and new datasets are evaluated separately.",
            "reviewed_by": args.reviewed_by,
            "reviewed_at": datetime.now(UTC).isoformat(),
        })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source_run": run.evaluation_run_id, "profile": args.profile,
                                  "review_count": len(reviewed), "reviews": reviewed},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(reviewed)} immutable adjudication records to {output}")


if __name__ == "__main__":
    main()
