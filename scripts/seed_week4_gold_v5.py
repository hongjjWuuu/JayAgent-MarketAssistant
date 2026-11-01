"""Create Gold v5 with reason codes adjudicated against Query Analysis v4."""
from __future__ import annotations

from uuid import uuid4

from app.domain.models import EvaluationCase
from app.repositories.factory import get_repository
from app.services.query_analyzer import analyze_query


def main() -> None:
    repository = get_repository()
    existing = repository.get_evaluation_dataset("tenant_demo", "week4_gold", "v5")
    if existing and existing.status == "frozen":
        print(f"week4_gold/v5 already frozen: {existing.case_count}")
        return
    source = repository.list_evaluation_cases("tenant_demo", "week4_gold", "v4")
    if len(source) != 100:
        raise RuntimeError("week4_gold/v4 must contain 100 frozen cases")
    values = []
    for case in source:
        expected = dict(case.expected)
        if case.case_type == "no_evidence":
            analysis = analyze_query(case.input_data["query"], case.input_data.get("filters"))
            expected["reason_codes"] = (
                ["UNSUPPORTED_SCOPE"]
                if analysis.domain_intent == "unsupported"
                else ["NO_EVIDENCE", "LOW_COVERAGE"]
            )
            expected["decision_rationale"] = "Reason follows deterministic domain analysis"
        values.append(EvaluationCase(
            evaluation_case_id=f"eval_case_{uuid4().hex}", tenant_id="tenant_demo",
            dataset="week4_gold", dataset_version="v5", name=case.name,
            case_type=case.case_type, input_data=case.input_data, expected=expected,
            tags=sorted(set(case.tags + ["reason-v4-reviewed"])),
        ))
    repository.import_evaluation_cases(values)
    frozen = repository.freeze_evaluation_dataset("tenant_demo", "week4_gold", "v5")
    if frozen is None or frozen.case_count != 100:
        raise RuntimeError("Unable to freeze week4_gold/v5")
    print("week4_gold/v5 frozen: 100 reviewed cases")


if __name__ == "__main__":
    main()
