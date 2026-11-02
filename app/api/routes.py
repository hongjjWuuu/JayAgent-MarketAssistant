from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.security import current_auth
from app.repositories.factory import get_repository
from app.runtime.worker import TaskWorker
from app.schemas.api import (
    ActionConfirm,
    ActionPlanCreate,
    ConversationCreate,
    EvaluationCaseImport,
    EvaluationRunRequest,
    HandoffCreate,
    HandoffReply,
    HandoffResolve,
    HandoffTransfer,
    MessageCreate,
    PilotCreate,
    PilotFeedbackCreate,
    PolicyCreate,
    PolicyUpdate,
    ReconciliationResolve,
    RetrievalDebugRequest,
    RetrievalProfileActivate,
    RetrievalProfileCalibration,
    RetrievalProfileCreate,
    RetrievalProfileRollback,
    RetrievalProfileValidation,
    TaskReplay,
)
from app.services.authorization import AuthorizationService
from app.services.market_service import MarketAssistantService
from app.tools.gateway import tool_catalog

router = APIRouter(prefix="/api/v1")
repository = get_repository()
service = MarketAssistantService(repository)


@router.get("/auth/me")
def auth_me():
    auth = current_auth()
    return {
        "subject": auth.subject,
        "tenant_id": auth.tenant_id,
        "role": auth.role,
        "username": auth.username,
        "customer_id": auth.customer_id,
        "capabilities": AuthorizationService.capabilities(auth),
    }


@router.post("/conversations", status_code=201)
def create_conversation(data: ConversationCreate):
    return service.create_conversation(current_auth(), data.customer_id, data.channel)


@router.get("/conversations")
def list_conversations():
    return service.list_conversations(current_auth())


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    return service.get_conversation(current_auth(), conversation_id)


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def send_message(conversation_id: str, data: MessageCreate):
    response = service.enqueue_message(current_auth(), conversation_id, data.content)
    if settings.inline_worker:
        TaskWorker(repository, "inline-worker").run_once()
    return response


@router.get("/conversations/{conversation_id}/events")
def conversation_events(
    conversation_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    auth = current_auth()
    service.get_conversation(auth, conversation_id)

    async def stream():
        seen: set[str] = set()
        if last_event_id:
            existing = repository.list_events(conversation_id, auth.tenant_id)
            cursor_prefix: set[str] = set()
            for event in existing:
                cursor_prefix.add(event.event_id)
                if event.event_id == last_event_id:
                    seen = cursor_prefix
                    break
        idle = 0
        while idle < 120:
            events = repository.list_events(conversation_id, auth.tenant_id)
            fresh = [item for item in events if item.event_id not in seen]
            if fresh:
                idle = 0
                for event in fresh:
                    seen.add(event.event_id)
                    yield f"id: {event.event_id}\nevent: {event.event_type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/handoff", status_code=201)
def create_handoff(conversation_id: str, data: HandoffCreate):
    return service.create_handoff(current_auth(), conversation_id, data.reason)


@router.get("/agent-console/handoffs")
def list_handoffs(status: str | None = Query(default=None)):
    return service.list_handoffs(current_auth(), status)


@router.post("/handoffs/{handoff_id}/claim")
def claim_handoff(handoff_id: str):
    return service.claim_handoff(current_auth(), handoff_id)


@router.post("/handoffs/{handoff_id}/reply", status_code=201)
def reply_handoff(handoff_id: str, data: HandoffReply):
    return service.handoff_reply(current_auth(), handoff_id, data.content)


@router.post("/handoffs/{handoff_id}/release")
def release_handoff(handoff_id: str):
    return service.release_handoff(current_auth(), handoff_id)


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, data: HandoffResolve):
    return service.resolve_handoff(current_auth(), handoff_id, data.resume_ai)


@router.post("/handoffs/{handoff_id}/transfer")
def transfer_handoff(handoff_id: str, data: HandoffTransfer):
    return service.transfer_handoff(current_auth(), handoff_id, data.queue_id, data.reason)


@router.post("/action-plans", status_code=201)
def create_action_plan(data: ActionPlanCreate):
    return service.create_action_plan(current_auth(), data)


@router.post("/action-plans/{action_plan_id}/confirm", status_code=202)
def confirm_action(action_plan_id: str, _: ActionConfirm):
    return service.confirm_action(current_auth(), action_plan_id)


@router.get("/action-executions/{execution_id}")
def get_action_execution(execution_id: str):
    return service.get_execution(current_auth(), execution_id)


@router.post("/action-executions/{execution_id}/cancel")
def cancel_action_execution(execution_id: str):
    return service.cancel_execution(current_auth(), execution_id)


@router.get("/tickets")
def list_tickets():
    return service.list_tickets(current_auth())


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    return service.get_ticket(current_auth(), ticket_id)


@router.post("/admin/policies", status_code=201)
def create_policy(data: PolicyCreate):
    return service.create_policy(current_auth(), data)


@router.get("/admin/policies")
def list_policies(
    scope: str | None = Query(default=None), status: str | None = Query(default=None)
):
    return service.list_policies(current_auth(), scope, status)


@router.get("/admin/policies/{policy_id}")
def get_policy(policy_id: str):
    return service.get_policy(current_auth(), policy_id)


@router.patch("/admin/policies/{policy_id}")
def update_policy(policy_id: str, data: PolicyUpdate):
    return service.update_policy(current_auth(), policy_id, data)


@router.post("/admin/policies/{policy_id}/publish")
def publish_policy(policy_id: str):
    return service.publish_policy(current_auth(), policy_id)


@router.post("/admin/policies/{policy_id}/reindex", status_code=202)
def reindex_policy(policy_id: str):
    return service.reindex_policy(current_auth(), policy_id)


@router.post("/admin/retrieval/debug")
def debug_retrieval(data: RetrievalDebugRequest):
    return service.debug_retrieval(current_auth(), data)


@router.post("/admin/retrieval-profiles", status_code=201)
def create_retrieval_profile(data: RetrievalProfileCreate):
    return service.create_retrieval_profile(current_auth(), data)


@router.get("/admin/retrieval-profiles")
def list_retrieval_profiles():
    return service.list_retrieval_profiles(current_auth())


@router.post("/admin/retrieval-profiles/{profile_id}/reindex", status_code=202)
def reindex_retrieval_profile(profile_id: str):
    return service.reindex_retrieval_profile(current_auth(), profile_id)


@router.post("/admin/retrieval-profiles/{profile_id}/evaluate", status_code=202)
def evaluate_retrieval_profile(profile_id: str):
    return service.evaluate_retrieval_profile(current_auth(), profile_id)


@router.post("/admin/retrieval-profiles/{profile_id}/calibrate")
def calibrate_retrieval_profile(profile_id: str, data: RetrievalProfileCalibration):
    return service.calibrate_retrieval_profile(current_auth(), profile_id, data)


@router.post("/admin/retrieval-profiles/{profile_id}/validate")
def validate_retrieval_profile(profile_id: str, data: RetrievalProfileValidation):
    return service.validate_retrieval_profile(current_auth(), profile_id, data)


@router.post("/admin/retrieval-profiles/{profile_id}/activate")
def activate_retrieval_profile(profile_id: str, data: RetrievalProfileActivate):
    return service.activate_retrieval_profile(current_auth(), profile_id, data)


@router.post("/admin/retrieval-profiles/{profile_id}/rollback")
def rollback_hash_retrieval_profile(profile_id: str, data: RetrievalProfileRollback):
    return service.rollback_hash_retrieval_profile(current_auth(), profile_id, data.reason)


@router.get("/admin/retrieval-profiles/{profile_id}/activation-readiness")
def retrieval_profile_activation_readiness(profile_id: str):
    return service.retrieval_profile_activation_readiness(current_auth(), profile_id)


@router.get("/admin/llm-traces")
def list_llm_traces():
    return service.list_llm_traces(current_auth())


@router.get("/admin/llm-traces/{trace_id}")
def get_llm_trace(trace_id: str):
    return service.get_llm_trace(current_auth(), trace_id)


@router.get("/admin/agent-runs")
def list_agent_runs():
    return service.list_agent_runs(current_auth())


@router.get("/admin/agent-runs/{run_id}")
def get_agent_run(run_id: str):
    return service.get_agent_run(current_auth(), run_id)


@router.get("/admin/pilot-metrics")
def pilot_metrics():
    return service.pilot_metrics(current_auth())


@router.post("/admin/pilots", status_code=201)
def create_pilot(data: PilotCreate):
    return service.create_pilot(current_auth(), data)


@router.get("/admin/pilots/{pilot_id}")
def get_pilot(pilot_id: str):
    return service.get_pilot(current_auth(), pilot_id)


@router.post("/admin/pilots/{pilot_id}/freeze")
def freeze_pilot(pilot_id: str):
    return service.freeze_pilot(current_auth(), pilot_id)


@router.post("/admin/pilots/{pilot_id}/close")
def close_pilot(pilot_id: str):
    return service.close_pilot(current_auth(), pilot_id)


@router.get("/admin/pilots/{pilot_id}/tasks")
def list_pilot_tasks(pilot_id: str):
    return service.list_pilot_tasks(current_auth(), pilot_id)


@router.get("/admin/pilots/{pilot_id}/metrics")
def pilot_release_metrics(
    pilot_id: str,
    scenario: str | None = Query(default=None),
    participant_code: str | None = Query(default=None),
):
    return service.pilot_release_metrics(
        current_auth(), pilot_id, scenario, participant_code
    )


@router.post("/admin/pilots/{pilot_id}/snapshots", status_code=201)
def snapshot_pilot_metrics(pilot_id: str):
    return service.snapshot_pilot_metrics(current_auth(), pilot_id)


@router.get("/admin/pilots/{pilot_id}/report")
def pilot_report(pilot_id: str):
    return service.pilot_report(current_auth(), pilot_id)


@router.post("/conversations/{conversation_id}/pilot-feedback", status_code=201)
def submit_pilot_feedback(
    conversation_id: str, data: PilotFeedbackCreate
):
    return service.submit_pilot_feedback(current_auth(), conversation_id, data)


@router.get("/pilot-tasks")
def list_my_pilot_tasks():
    return service.list_my_pilot_tasks(current_auth())


@router.post("/admin/evaluation-cases/import", status_code=201)
def import_evaluation_cases(data: EvaluationCaseImport):
    return service.import_evaluation_cases(current_auth(), data)


@router.get("/admin/evaluation-cases")
def list_evaluation_cases(
    dataset: str = Query(default="week4_gold"),
    dataset_version: str = Query(default="v1"),
):
    return service.list_evaluation_cases(current_auth(), dataset, dataset_version)


@router.get("/admin/evaluation-datasets")
def list_evaluation_datasets():
    return service.list_evaluation_datasets(current_auth())


@router.post("/admin/evaluation-datasets/{dataset}/{version}/freeze")
def freeze_evaluation_dataset(dataset: str, version: str):
    return service.freeze_evaluation_dataset(current_auth(), dataset, version)


@router.post("/admin/evaluations/run", status_code=202)
def run_evaluation(data: EvaluationRunRequest | None = None):
    return service.run_evaluation(current_auth(), data)


@router.get("/admin/evaluations")
def list_evaluations(
    dataset: str | None = Query(default=None),
    dataset_version: str | None = Query(default=None),
):
    return service.list_evaluations(current_auth(), dataset, dataset_version)


@router.get("/admin/evaluations/compare")
def compare_evaluations(left: str = Query(), right: str = Query()):
    return service.compare_evaluations(current_auth(), left, right)


@router.get("/admin/evaluations/{run_id}/failures")
def evaluation_failures(run_id: str):
    return service.evaluation_failures(current_auth(), run_id)


@router.get("/admin/evaluations/{run_id}/diagnostics")
def evaluation_diagnostics(run_id: str):
    return service.evaluation_diagnostics(current_auth(), run_id)


@router.get("/admin/evaluations/{run_id}")
def get_evaluation(run_id: str):
    return service.get_evaluation(current_auth(), run_id)


@router.get("/admin/tool-catalog")
def get_tool_catalog():
    auth = current_auth()
    if auth.role != "tenant_admin":
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="Administrator role required")
    return tool_catalog()


@router.get("/admin/runtime-tasks")
def list_runtime_tasks(
    status: str | None = Query(default=None), task_type: str | None = Query(default=None)
):
    return service.list_runtime_tasks(current_auth(), status, task_type)


@router.get("/admin/runtime-tasks/{task_id}")
def get_runtime_task(task_id: str):
    return service.get_runtime_task(current_auth(), task_id)


@router.post("/admin/runtime-tasks/{task_id}/replay", status_code=201)
def replay_runtime_task(task_id: str, data: TaskReplay):
    return service.replay_runtime_task(current_auth(), task_id, data.reason)


@router.post("/admin/runtime-tasks/{task_id}/cancel")
def cancel_runtime_task(task_id: str):
    return service.cancel_runtime_task(current_auth(), task_id)


@router.get("/admin/workers")
def list_workers():
    return service.list_workers(current_auth())


@router.get("/admin/runtime-summary")
def runtime_summary():
    return service.runtime_summary(current_auth())


@router.get("/admin/reconciliations")
def list_reconciliations():
    return service.list_reconciliations(current_auth())


@router.post("/admin/action-executions/{execution_id}/resolve-reconciliation")
def resolve_reconciliation(execution_id: str, data: ReconciliationResolve):
    return service.resolve_reconciliation(current_auth(), execution_id, data)
