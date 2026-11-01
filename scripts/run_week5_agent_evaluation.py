from __future__ import annotations

import argparse
import json

from app.core.security import AuthContext
from app.repositories.factory import get_repository
from app.runtime.worker import TaskWorker
from app.schemas.api import EvaluationRunRequest
from app.services.market_service import MarketAssistantService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=("fake", "configured"), default="fake")
    parser.add_argument("--split", choices=("all", "live_smoke"), default="all")
    parser.add_argument("--dataset-version", default="v3")
    args = parser.parse_args()
    repository = get_repository()
    service = MarketAssistantService(repository)
    auth = AuthContext(
        "tenant_demo", "admin_001", "tenant_admin", "week5-eval", "admin_001"
    )
    run = service.run_evaluation(
        auth,
        EvaluationRunRequest(
            dataset="week5_agent",
            dataset_version=args.dataset_version,
            evaluator="agent",
            split=args.split,
            provider=args.provider,
        ),
    )
    worker = TaskWorker(repository, f"week5-{args.provider}-{args.split}")
    for _ in range(200):
        current = repository.get_evaluation_run(run.evaluation_run_id, "tenant_demo")
        if current and current.status == "completed":
            break
        if not worker.run_once():
            raise RuntimeError("Evaluation task was not available")
    completed = repository.get_evaluation_run(run.evaluation_run_id, "tenant_demo")
    if completed is None or completed.status != "completed":
        raise RuntimeError("Week 5 evaluation did not complete")
    failures = [
        {
            "case_id": item.case_id,
            "metrics": item.metrics,
            "expected": item.details.get("expected"),
            "actual_kind": (item.details.get("actual") or {}).get("kind"),
            "error_code": (item.details.get("actual") or {}).get("error_code"),
        }
        for item in repository.list_evaluation_results(
            completed.evaluation_run_id, "tenant_demo"
        )
        if not item.passed
    ]
    print(
        json.dumps(
            {
                "run_id": completed.evaluation_run_id,
                "quality_status": completed.quality_status,
                "summary": completed.summary,
                "failure_count": len(failures),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if completed.quality_status != "passed":
        raise RuntimeError("Week 5 agent evaluation failed")


if __name__ == "__main__":
    main()
