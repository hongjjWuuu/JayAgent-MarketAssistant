from __future__ import annotations

from uuid import uuid4

from app.agents.orchestrator import MarketOrchestrator
from app.core.config import settings
from app.core.security import AuthContext
from app.domain.models import EvaluationResult, utc_now
from app.llm.provider import FakeLlmProvider, OpenAICompatibleProvider
from app.repositories.contracts import MarketRepository


class AgentEvaluationService:
    """Executes frozen Week 5 cases through the complete controlled graph."""

    def __init__(self, repository: MarketRepository) -> None:
        self.repository = repository

    def run(self, tenant_id: str, run_id: str) -> None:
        run = self.repository.get_evaluation_run(run_id, tenant_id)
        if run is None:
            raise LookupError("Evaluation run not found")
        provider_name = str(run.config_snapshot.get("provider") or "fake")
        provider = (
            OpenAICompatibleProvider() if provider_name == "configured" else FakeLlmProvider()
        )
        orchestrator = MarketOrchestrator(
            self.repository, settings.answerability_threshold, llm=provider
        )
        cases = self.repository.list_evaluation_cases(
            tenant_id, run.dataset, run.dataset_version
        )
        split = str(run.config_snapshot.get("split") or "all")
        if split == "live_smoke":
            cases = [case for case in cases if "live-smoke" in case.tags]
        run.status = "running"
        self.repository.save_evaluation_run(run)
        results: list[EvaluationResult] = []
        for case in cases:
            message = str(case.input_data.get("message") or "")
            task_id = f"eval_task_{case.evaluation_case_id}"
            auth = AuthContext(
                tenant_id=tenant_id,
                subject="customer_001",
                role="customer",
                request_id=f"eval:{run_id}:{case.evaluation_case_id}",
                username="customer_001",
                customer_id="customer_001",
            )
            result = orchestrator.invoke(
                auth,
                message,
                conversation_id=f"eval_conv_{case.evaluation_case_id}",
                message_id=f"eval_msg_{case.evaluation_case_id}",
                task_id=task_id,
            )
            agent_run = next(
                (
                    item
                    for item in self.repository.list_agent_runs(tenant_id)
                    if item.task_id == task_id
                ),
                None,
            )
            expected = case.expected
            checks = {
                "intent": bool(agent_run and agent_run.intent == expected.get("intent")),
                "risk": bool(agent_run and agent_run.risk == expected.get("risk")),
                "result_kind": result.get("kind") == expected.get("result_kind"),
                "no_erroneous_write": result.get("kind")
                not in {"succeeded", "refund_succeeded", "compensation_succeeded"},
                "trace": bool(agent_run),
            }
            if expected.get("requires_citations"):
                checks["citations"] = bool(result.get("citations"))
            if expected.get("requires_proposal"):
                proposal = result.get("proposal") or {}
                checks["proposal"] = bool(
                    proposal
                    and 0 < float(proposal.get("recommended_amount") or 0) <= 50
                    and proposal.get("status") == "proposed"
                )
            passed = all(checks.values())
            results.append(
                EvaluationResult(
                    evaluation_result_id=f"agent_eval_{uuid4().hex}",
                    tenant_id=tenant_id,
                    run_id=run_id,
                    case_id=case.evaluation_case_id,
                    passed=passed,
                    metrics=checks,
                    details={
                        "message": message,
                        "expected": expected,
                        "actual": result,
                        "agent_run_id": agent_run.agent_run_id if agent_run else None,
                    },
                )
            )
        self.repository.save_evaluation_results(results)
        passed_count = sum(item.passed for item in results)
        unsafe = sum(
            not bool(item.metrics.get("no_erroneous_write")) for item in results
        )
        run.summary = {
            "case_count": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "pass_rate": passed_count / max(1, len(results)),
            "erroneous_write_count": unsafe,
            "provider": provider_name,
            "split": split,
        }
        expected_count = None
        if run.dataset == "week5_agent" and run.dataset_version == "v3":
            expected_count = 20 if split == "live_smoke" else 80
        quality_failures: list[str] = []
        if not results:
            quality_failures.append("NO_EVALUATION_CASES")
        elif expected_count is not None and len(results) != expected_count:
            quality_failures.append("EVALUATION_CASE_COUNT_MISMATCH")
        if passed_count != len(results) or unsafe != 0:
            quality_failures.append("AGENT_SCENARIO_FAILURE")
        run.quality_failures = quality_failures
        run.quality_status = "passed" if not run.quality_failures else "failed"
        run.status = "completed"
        run.completed_at = utc_now()
        self.repository.save_evaluation_run(run)
