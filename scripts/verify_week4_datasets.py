from __future__ import annotations

from app.repositories.factory import get_repository
from app.services.query_analyzer import normalize_query


def main() -> None:
    repository = get_repository()
    calibration = repository.list_evaluation_cases("tenant_demo", "week4_gate_calibration", "v1")
    holdout = repository.list_evaluation_cases("tenant_demo", "week4_hard_negative", "v2")
    assert len(calibration) == 80 and len(holdout) == 60
    assert {case.expected["decision"] for case in calibration} >= {"answer", "clarify", "handoff"}
    assert all(case.expected.get("decision_rationale") for case in calibration + holdout)
    assert all(case.expected.get("required_citation_chunk_ids")
               or case.expected.get("required_citation_policy_version_ids")
               or case.expected.get("required_citation_policy_ids")
               for case in calibration if case.expected["decision"] == "answer")
    calibration_queries = {normalize_query(case.input_data["query"]) for case in calibration}
    holdout_queries = {normalize_query(case.input_data["query"]) for case in holdout}
    assert calibration_queries.isdisjoint(holdout_queries)
    assert repository.get_evaluation_dataset("tenant_demo", "week4_gate_calibration", "v1").status == "frozen"
    assert repository.get_evaluation_dataset("tenant_demo", "week4_hard_negative", "v2").status == "frozen"
    print("Week 4 Gate-v4 datasets verified: calibration=80, holdout=60, overlap=0")


if __name__ == "__main__":
    main()
