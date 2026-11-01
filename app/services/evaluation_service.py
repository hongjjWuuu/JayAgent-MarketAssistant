from __future__ import annotations

from app.domain.models import utc_now
from app.repositories.contracts import MarketRepository
from app.services.embeddings import embedding_provider_for
from app.services.evaluation_metrics import evaluate_case, quality_failures, summarize
from app.services.policy_service import PolicyService


class EvaluationService:
    def __init__(self, repository: MarketRepository, threshold: float) -> None:
        self.repository = repository
        self.threshold = threshold

    def run(self, tenant_id: str, run_id: str) -> None:
        run = self.repository.get_evaluation_run(run_id, tenant_id)
        if run is None:
            raise LookupError("Evaluation run not found")
        profile_id = run.config_snapshot.get("retrieval_profile_id")
        profile = (
            self.repository.get_retrieval_profile(str(profile_id), tenant_id)
            if profile_id
            else None
        )
        policies = PolicyService(
            self.repository,
            self.threshold,
            embeddings=(
                embedding_provider_for(
                    profile.provider,
                    profile.embedding_model,
                    profile.embedding_dimension,
                )
                if profile
                else None
            ),
            retrieval_profile_id=profile.retrieval_profile_id if profile else None,
        )
        run.status = "running"
        self.repository.save_evaluation_run(run)
        cases = self.repository.list_evaluation_cases(
            tenant_id, run.dataset, run.dataset_version
        )
        split = str(run.config_snapshot.get("split") or "all")
        if split in {"calibration", "holdout"}:
            cases = [case for case in cases if split in case.tags]
        results = []
        for case in cases:
            query = str(case.input_data.get("query") or "")
            filters = dict(case.input_data.get("filters") or {})
            decision = policies.answerability_gate(tenant_id, query, filters=filters)
            snapshot = self.repository.get_retrieval_snapshot(
                decision.retrieval_snapshot_id, tenant_id
            )
            results.append(evaluate_case(tenant_id, run_id, case, decision, snapshot))
        self.repository.save_evaluation_results(results)
        run.summary = summarize(results, cases)
        run.quality_failures = quality_failures(run.summary)
        run.quality_status = "passed" if not run.quality_failures else "failed"
        run.status = "completed"
        run.completed_at = utc_now()
        self.repository.save_evaluation_run(run)
