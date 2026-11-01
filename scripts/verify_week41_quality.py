"""Run the frozen Week 4.1 dataset and fail when its quality gate fails."""

import json
from uuid import uuid4

from app.core.config import settings
from app.domain.models import EvaluationRun
from app.repositories.factory import get_repository
from app.services.evaluation_service import EvaluationService
from app.services.policy_service import PolicyService


def main() -> None:
    repository = get_repository()
    dataset = repository.get_evaluation_dataset("tenant_demo", "week4_gold", "v2")
    if dataset is None or dataset.status != "frozen" or dataset.case_count != 100:
        raise RuntimeError("week4_gold/v2 must be frozen with exactly 100 cases")
    policies = PolicyService(repository, settings.answerability_threshold)
    run = EvaluationRun(
        evaluation_run_id=f"eval_verify_{uuid4().hex}",
        tenant_id="tenant_demo",
        dataset="week4_gold",
        dataset_version="v2",
        config_snapshot={
            "retriever": policies.retriever_version,
            "gate": policies.gate_version,
            "threshold": settings.answerability_threshold,
            "embedding_model": policies.embeddings.model_id,
            "embedding_dimension": policies.embeddings.dimensions,
            "normalization": "query-normalizer-v2",
        },
        metric_schema_version="v2",
    )
    repository.save_evaluation_run(run)
    EvaluationService(repository, settings.answerability_threshold).run(
        run.tenant_id, run.evaluation_run_id
    )
    completed = repository.get_evaluation_run(run.evaluation_run_id, run.tenant_id)
    if completed is None:
        raise RuntimeError("evaluation run disappeared")
    print(json.dumps(completed.summary, ensure_ascii=False, indent=2))
    if completed.quality_status != "passed":
        raise RuntimeError("Week 4.1 quality gate failed: " + "; ".join(completed.quality_failures))
    print("Week 4.1 quality gate passed")


if __name__ == "__main__":
    main()
