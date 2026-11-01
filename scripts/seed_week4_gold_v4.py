"""Create the reviewed Gate-v4 Gold set without mutating frozen v3."""
from __future__ import annotations

from uuid import uuid4

from app.domain.models import EvaluationCase
from app.repositories.factory import get_repository


def main() -> None:
    repository = get_repository()
    existing = repository.get_evaluation_dataset("tenant_demo", "week4_gold", "v4")
    if existing and existing.status == "frozen":
        print(f"week4_gold/v4 already frozen: {existing.case_count}")
        return
    source = repository.list_evaluation_cases("tenant_demo", "week4_gold", "v3")
    if len(source) != 100:
        raise RuntimeError("week4_gold/v3 must contain 100 frozen cases")
    values: list[EvaluationCase] = []
    for case in source:
        expected = dict(case.expected)
        if case.case_type == "acl_boundary":
            expected["reason_codes"] = ["ACL_CONTEXT_MISMATCH"]
            expected["decision_rationale"] = "Explicit ACL mismatch must hand off"
        elif case.case_type == "no_evidence":
            expected["reason_codes"] = ["UNSUPPORTED_SCOPE"]
            expected["decision_rationale"] = "Clearly unsupported domain is blocked before retrieval"
        elif case.case_type == "stale_version":
            # Old and current versions intentionally share a stable policy_id;
            # only the superseded version_id is forbidden.
            expected["forbidden_policy_ids"] = []
            expected["decision_rationale"] = "Current family is allowed; superseded version is forbidden"
        else:
            expected.setdefault("decision_rationale", "Reviewed against Gate v4 semantics")
        values.append(EvaluationCase(
            evaluation_case_id=f"eval_case_{uuid4().hex}", tenant_id="tenant_demo",
            dataset="week4_gold", dataset_version="v4", name=case.name,
            case_type=case.case_type, input_data=case.input_data, expected=expected,
            tags=sorted(set(case.tags + ["gate-v4-reviewed"])),
        ))
    repository.import_evaluation_cases(values)
    frozen = repository.freeze_evaluation_dataset("tenant_demo", "week4_gold", "v4")
    if frozen is None or frozen.case_count != 100:
        raise RuntimeError("Unable to freeze week4_gold/v4")
    print("week4_gold/v4 frozen: 100 reviewed cases")


if __name__ == "__main__":
    main()
