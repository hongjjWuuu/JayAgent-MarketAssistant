"""Create the immutable Week 4.2 metric-v3 dataset from the frozen v2 inputs."""

from __future__ import annotations

from uuid import uuid4

from app.domain.models import EvaluationCase
from app.repositories.factory import get_repository


def main() -> None:
    repository = get_repository()
    tenant_id = "tenant_demo"
    existing = repository.get_evaluation_dataset(tenant_id, "week4_gold", "v3")
    if existing and existing.status == "frozen":
        print(f"week4_gold/v3 already frozen with {existing.case_count} cases")
        return
    source = repository.list_evaluation_cases(tenant_id, "week4_gold", "v2")
    if len(source) != 100:
        raise RuntimeError(f"week4_gold/v2 must contain 100 cases, found {len(source)}")
    values: list[EvaluationCase] = []
    for case in source:
        expected = dict(case.expected)
        relevant_versions = list(expected.get("relevant_policy_version_ids") or [])
        required_versions = list(
            expected.get("required_citation_policy_version_ids") or relevant_versions
        )
        forbidden_versions = list(expected.get("forbidden_policy_version_ids") or [])

        def families(version_ids: list[str]) -> list[str]:
            result = []
            for version_id in version_ids:
                policy = repository.get_policy(version_id, tenant_id)
                if policy and policy.policy_id:
                    result.append(policy.policy_id)
            return sorted(set(result))

        expected.update(
            relevant_policy_ids=families(relevant_versions),
            required_citation_policy_ids=families(required_versions),
            required_citation_chunk_ids=list(expected.get("relevant_chunk_ids") or []),
            forbidden_policy_ids=families(forbidden_versions),
            forbidden_chunk_ids=list(expected.get("forbidden_chunk_ids") or []),
        )
        values.append(
            EvaluationCase(
                evaluation_case_id=f"eval_case_{uuid4().hex}",
                tenant_id=tenant_id,
                dataset="week4_gold",
                dataset_version="v3",
                name=case.name,
                case_type=case.case_type,
                input_data=case.input_data,
                expected=expected,
                tags=sorted(set(case.tags + ["metric-v3"])),
            )
        )
    repository.import_evaluation_cases(values)
    frozen = repository.freeze_evaluation_dataset(tenant_id, "week4_gold", "v3")
    if frozen is None or frozen.case_count != 100:
        raise RuntimeError("Failed to freeze complete week4_gold/v3")
    print("week4_gold/v3 frozen with 100 cases")


if __name__ == "__main__":
    main()
