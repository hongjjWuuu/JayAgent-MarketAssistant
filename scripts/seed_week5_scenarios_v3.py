"""Freeze the reviewed Week 5 dataset without mutating the flawed v2 history."""

from __future__ import annotations

import hashlib

from app.domain.models import EvaluationCase
from app.repositories.factory import get_repository
from scripts.seed_week5_scenarios_v2 import GROUPS, LIVE_COUNTS, TENANT

# Week 4 Gate correctly blocks these cases. v2 incorrectly required every policy
# question to answer and every compensation request to produce a proposal.
REVIEWED_EXPECTATIONS: dict[tuple[str, int], dict] = {
    ("compensation", 3): {
        "intent": "compensation",
        "risk": "high",
        "result_kind": "handoff_recommended",
        "no_erroneous_write": True,
    },
    ("policy", 8): {
        "intent": "policy",
        "risk": "read_only",
        "result_kind": "handoff_recommended",
        "no_erroneous_write": True,
    },
    ("policy", 10): {
        "intent": "policy",
        "risk": "read_only",
        "result_kind": "clarification",
        "no_erroneous_write": True,
    },
    ("policy", 14): {
        "intent": "policy",
        "risk": "read_only",
        "result_kind": "handoff_recommended",
        "no_erroneous_write": True,
    },
}


def main() -> None:
    repository = get_repository()
    existing = repository.get_evaluation_dataset(TENANT, "week5_agent", "v3")
    if existing and existing.status == "frozen":
        print(f"week5_agent/v3 already frozen with {existing.case_count} cases")
        return
    cases: list[EvaluationCase] = []
    for group, messages, intent, risk, result_kind, extra in GROUPS:
        for index, message in enumerate(messages, start=1):
            digest = hashlib.sha256(f"v3:{group}:{message}".encode()).hexdigest()[:24]
            tags = ["week5", group, "fake-provider-ci", "reviewed-v3"]
            if index <= LIVE_COUNTS[group]:
                tags.append("live-smoke")
            expected = REVIEWED_EXPECTATIONS.get(
                (group, index),
                {
                    "intent": intent,
                    "risk": risk,
                    "result_kind": result_kind,
                    "no_erroneous_write": True,
                    **extra,
                },
            )
            cases.append(
                EvaluationCase(
                    evaluation_case_id=f"week5_v3_{digest}",
                    tenant_id=TENANT,
                    dataset="week5_agent",
                    dataset_version="v3",
                    name=f"{group}-{index:02d}",
                    case_type=group,
                    input_data={"message": message},
                    expected=expected,
                    tags=tags,
                )
            )
    if len(cases) != 80 or len({case.input_data["message"] for case in cases}) != 80:
        raise RuntimeError("Week 5 v3 must contain 80 unique messages")
    if sum("live-smoke" in case.tags for case in cases) != 20:
        raise RuntimeError("Week 5 v3 must contain exactly 20 live smoke cases")
    repository.import_evaluation_cases(cases)
    frozen = repository.freeze_evaluation_dataset(TENANT, "week5_agent", "v3")
    if frozen is None or frozen.case_count != 80:
        raise RuntimeError("Failed to freeze week5_agent/v3")
    print("week5_agent/v3 frozen with 80 reviewed cases; live-smoke=20")


if __name__ == "__main__":
    main()
