from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import httpx
from prometheus_client import start_http_server

from app.agents.orchestrator import MarketOrchestrator
from app.connectors.business import BusinessConnector, build_business_connector
from app.core.config import settings
from app.core.security import AuthContext
from app.domain.models import (
    ActionStatus,
    ConversationEvent,
    ConversationStatus,
    Message,
    RuntimeTask,
    RuntimeTaskStatus,
    Ticket,
    utc_now,
)
from app.domain.transitions import (
    LeaseLostError,
    ensure_action_transition,
    ensure_conversation_transition,
)
from app.observability.metrics import WORKER_HEARTBEAT, WORKER_TASKS_PROCESSED
from app.repositories.contracts import MarketRepository
from app.repositories.factory import get_repository
from app.runtime.reconciliation import (
    ConnectorReconciliationProvider,
    ReconciliationProvider,
    SimulatedReconciliationProvider,
)
from app.schemas.api import ActionPlanCreate
from app.services.agent_evaluation_service import AgentEvaluationService
from app.services.evaluation_service import EvaluationService
from app.services.market_service import MarketAssistantService

logger = logging.getLogger("market_assistant.worker")


@dataclass
class LeaseGuard:
    task_id: str
    worker_id: str
    lost: threading.Event = field(default_factory=threading.Event)

    def ensure_owned(self) -> None:
        if self.lost.is_set():
            raise LeaseLostError(f"Worker {self.worker_id} lost lease for task {self.task_id}")


TaskHandler = Callable[[RuntimeTask, LeaseGuard], None]


class ReconciliationPending(RuntimeError):
    pass


class TaskWorker:
    def __init__(
        self,
        repository: MarketRepository,
        worker_id: str | None = None,
        handlers: Mapping[str, TaskHandler] | None = None,
        reconciler: ReconciliationProvider | None = None,
        connector: BusinessConnector | None = None,
    ) -> None:
        self.repository = repository
        self.hostname = socket.gethostname()
        self.process_id = os.getpid()
        configured = worker_id or settings.worker_id
        self.worker_id = configured or f"{self.hostname}:{self.process_id}:{uuid4().hex[:8]}"
        self.service = MarketAssistantService(repository)
        self.connector = connector or build_business_connector(repository)
        self.orchestrator = MarketOrchestrator(repository, settings.answerability_threshold)
        self.reconciler = reconciler or (
            ConnectorReconciliationProvider(self.connector)
            if settings.business_connector_mode == "http"
            else SimulatedReconciliationProvider()
        )
        self.handlers: dict[str, TaskHandler] = {
            "agent_message": self._process_message,
            "execute_action": self._execute_action,
            "reconcile_execution": self._reconcile_execution,
            "reindex_policy": self._reindex_policy,
            "reindex_retrieval_profile": self._reindex_retrieval_profile,
            "evaluation_run": self._evaluation_run,
        }
        if handlers:
            self.handlers.update(handlers)
        self._worker_heartbeat(None)

    def _worker_heartbeat(self, current_task_id: str | None) -> None:
        self.repository.heartbeat_worker(
            self.worker_id,
            self.hostname,
            self.process_id,
            current_task_id,
            {"version": "0.6.0-rc1", "task_types": sorted(self.handlers)},
        )
        WORKER_HEARTBEAT.labels(worker_id=self.worker_id).set(time.time())

    def run_once(self) -> bool:
        self._worker_heartbeat(None)
        task = self.repository.claim_task(self.worker_id, settings.task_lease_seconds)
        if task is None:
            return False
        guard = LeaseGuard(task.task_id, self.worker_id)
        self._worker_heartbeat(task.task_id)
        try:
            handler = self.handlers.get(task.task_type)
            if handler is None:
                raise ValueError(f"Unsupported task type: {task.task_type}")
            with self._lease_heartbeat(task.task_id, guard):
                handler(task, guard)
                guard.ensure_owned()
            self.repository.complete_task(task, self.worker_id)
            WORKER_TASKS_PROCESSED.labels(outcome="completed").inc()
            if task.conversation_id:
                self.repository.append_event(
                    ConversationEvent(
                        event_id=f"evt_{uuid4().hex}",
                        tenant_id=task.tenant_id,
                        conversation_id=task.conversation_id,
                        event_type="task.completed",
                        data={"task_id": task.task_id},
                    )
                )
        except LeaseLostError:
            WORKER_TASKS_PROCESSED.labels(outcome="lease_lost").inc()
            return True
        except Exception as error:  # noqa: BLE001 - task boundary persists failures
            try:
                failed = self.repository.fail_task(task, str(error), self.worker_id)
            except LeaseLostError:
                return True
            if task.task_type == "execute_action":
                self._mark_action_failure(task, failed.status)
            WORKER_TASKS_PROCESSED.labels(outcome=str(failed.status.value)).inc()
            if task.conversation_id:
                self.repository.append_event(
                    ConversationEvent(
                        event_id=f"evt_{uuid4().hex}",
                        tenant_id=task.tenant_id,
                        conversation_id=task.conversation_id,
                        event_type="task.failed",
                        data={
                            "task_id": task.task_id,
                            "status": failed.status,
                            "message": "任务处理失败，稍后重试或转人工。",
                        },
                    )
                )
        finally:
            self._worker_heartbeat(None)
        return True

    @contextmanager
    def _lease_heartbeat(self, task_id: str, guard: LeaseGuard):
        stop = threading.Event()
        interval = min(5.0, max(settings.task_lease_seconds / 3, 0.2))

        def beat() -> None:
            while not stop.wait(interval):
                self._worker_heartbeat(task_id)
                if not self.repository.heartbeat_task(
                    task_id, self.worker_id, settings.task_lease_seconds
                ):
                    guard.lost.set()
                    return

        thread = threading.Thread(target=beat, name=f"heartbeat-{task_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)

    def _auth(self, task: RuntimeTask) -> AuthContext:
        return AuthContext(
            tenant_id=task.tenant_id,
            subject=str(task.payload.get("subject") or task.payload.get("actor_id") or task.payload.get("customer_id")),
            role=str(task.payload.get("role") or "customer"),
            request_id=str(task.payload.get("request_id") or f"worker:{task.task_id}"),
            username=str(task.payload.get("username") or task.payload.get("actor_id") or "worker"),
            customer_id=task.payload.get("customer_id"),
        )

    def _process_message(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        guard.ensure_owned()
        auth = self._auth(task)
        conversation = self.service.get_conversation(auth, str(task.conversation_id))
        if any(
            message.task_id == task.task_id and message.sender_type == "assistant"
            for message in conversation.messages
        ):
            return
        result = self.orchestrator.invoke(
            auth,
            str(task.payload["content"]),
            conversation_id=conversation.conversation_id,
            message_id=str(task.payload.get("message_id") or ""),
            task_id=task.task_id,
        )
        guard.ensure_owned()
        if result["kind"] == "handoff_requested":
            handoff = self.service.create_handoff(
                auth, conversation.conversation_id, "user_requested_human"
            )
            result["handoff"] = handoff.model_dump(mode="json")
        elif result["kind"] == "ticket_requested":
            plan = self.service.create_action_plan(
                auth,
                ActionPlanCreate(
                    conversation_id=conversation.conversation_id,
                    action_type="create_ticket",
                    payload={"title": "商品问题", "description": task.payload["content"]},
                    idempotency_key=f"ticket:{conversation.conversation_id}:{task.payload['message_id']}",
                ),
            )
            result["action_plan"] = plan.model_dump(mode="json")
        elif result["kind"] == "compensation_requested":
            proposal = result["proposal"]
            plan = self.service.create_action_plan(
                auth,
                ActionPlanCreate(
                    conversation_id=conversation.conversation_id,
                    action_type="submit_compensation",
                    payload={"proposal_id": proposal["proposal_id"]},
                    idempotency_key=(
                        f"compensation:{conversation.conversation_id}:"
                        f"{proposal['proposal_id']}"
                    ),
                ),
            )
            result["action_plan"] = plan.model_dump(mode="json")
        elif result["kind"] == "clarification":
            ensure_conversation_transition(conversation.status, ConversationStatus.WAITING_USER)
            conversation.status = ConversationStatus.WAITING_USER
        agent_run_id = result.get("agent_run_id")
        if agent_run_id:
            agent_run = self.repository.get_agent_run(str(agent_run_id), task.tenant_id)
            if agent_run:
                agent_run.action_plan_id = (result.get("action_plan") or {}).get(
                    "action_plan_id"
                )
                agent_run.handoff_id = (result.get("handoff") or {}).get("handoff_id")
                self.repository.save_agent_run(agent_run)
        message = Message(
            message_id=f"msg_{uuid4().hex}",
            sender_type="assistant",
            content=result["reply"],
            facts=result,
            task_id=task.task_id,
        )
        conversation.messages.append(message)
        conversation.updated_at = utc_now()
        self.repository.save_conversation(conversation)
        self.repository.append_event(
            ConversationEvent(
                event_id=f"evt_{uuid4().hex}",
                tenant_id=task.tenant_id,
                conversation_id=conversation.conversation_id,
                event_type="assistant.message",
                data={"message": message.model_dump(mode="json"), "result": result},
            )
        )

    def _reindex_policy(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        guard.ensure_owned()

    def _reindex_retrieval_profile(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        guard.ensure_owned()
        profile = self.repository.reindex_retrieval_profile(
            str(task.payload["retrieval_profile_id"]), task.tenant_id
        )
        if profile is None:
            raise LookupError("Retrieval profile not found")
        guard.ensure_owned()

    def _evaluation_run(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        guard.ensure_owned()
        run_id = str(task.payload["evaluation_run_id"])
        run = self.repository.get_evaluation_run(run_id, task.tenant_id)
        if run is None:
            raise LookupError("Evaluation run not found")
        if run.config_snapshot.get("evaluator") == "agent":
            AgentEvaluationService(self.repository).run(task.tenant_id, run_id)
        else:
            EvaluationService(self.repository, settings.answerability_threshold).run(
                task.tenant_id, run_id
            )
        guard.ensure_owned()

    def _execute_action(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        plan = self.repository.get_action_plan(str(task.payload["action_plan_id"]), task.tenant_id)
        if not plan:
            raise LookupError("Action plan disappeared")
        execution = self.repository.execution_for_key(task.tenant_id, plan.idempotency_key)
        if not execution or execution.status == ActionStatus.CANCELLED:
            return
        if execution.status == ActionStatus.RETRYING:
            ensure_action_transition(execution.status, ActionStatus.QUEUED)
            execution.status = ActionStatus.QUEUED
            execution.updated_at = utc_now()
            self.repository.save_execution(execution)
        if plan.status == ActionStatus.RETRYING:
            ensure_action_transition(plan.status, ActionStatus.QUEUED)
            plan.status = ActionStatus.QUEUED
            self.repository.save_action_plan(plan)
        if execution.status == ActionStatus.QUEUED:
            ensure_action_transition(execution.status, ActionStatus.EXECUTING)
            execution.status = ActionStatus.EXECUTING
            execution.updated_at = utc_now()
            self.repository.save_execution(execution)
        if plan.status == ActionStatus.QUEUED:
            ensure_action_transition(plan.status, ActionStatus.EXECUTING)
            plan.status = ActionStatus.EXECUTING
            self.repository.save_action_plan(plan)

        guard.ensure_owned()
        if plan.payload.get("simulate_timeout"):
            ensure_action_transition(execution.status, ActionStatus.UNKNOWN_NEEDS_RECONCILIATION)
            execution.status = ActionStatus.UNKNOWN_NEEDS_RECONCILIATION
            execution.error_code = "EXTERNAL_WRITE_TIMEOUT"
            execution.updated_at = utc_now()
            execution.receipt = {
                "reconciliation": "required",
                "instruction": "Query external status by idempotency key before any retry",
            }
            self.repository.save_execution(execution)
            ensure_action_transition(plan.status, ActionStatus.UNKNOWN_NEEDS_RECONCILIATION)
            plan.status = ActionStatus.UNKNOWN_NEEDS_RECONCILIATION
            self.repository.save_action_plan(plan)
            self.repository.enqueue_task(
                RuntimeTask(
                    task_id=f"task_{uuid4().hex}",
                    tenant_id=task.tenant_id,
                    conversation_id=task.conversation_id,
                    task_type="reconcile_execution",
                    payload={"execution_id": execution.execution_id},
                    available_at=utc_now()
                    + timedelta(seconds=settings.reconciliation_delay_seconds),
                    max_attempts=settings.reconciliation_max_attempts,
                )
            )
            return
        if plan.action_type == "create_ticket":
            ticket = Ticket(
                ticket_id=self._ticket_id(plan.idempotency_key),
                tenant_id=task.tenant_id,
                customer_id=plan.customer_id,
                conversation_id=plan.conversation_id,
                title=str(plan.payload.get("title") or "售后工单"),
                description=str(plan.payload.get("description") or plan.preview),
            )
            try:
                connector_receipt = self.connector.create_ticket(ticket, plan.idempotency_key)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                self._mark_write_unknown(task, plan, execution, str(error))
                return
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500:
                    self._mark_write_unknown(task, plan, execution, str(error))
                    return
                raise
            receipt = {
                "ticket_id": connector_receipt.external_id,
                "ticket_status": connector_receipt.status,
                "connector": connector_receipt.payload,
            }
        else:
            ticket = Ticket(
                ticket_id=self._ticket_id(plan.idempotency_key),
                tenant_id=task.tenant_id,
                customer_id=plan.customer_id,
                conversation_id=plan.conversation_id,
                title="补偿人工审批",
                description=plan.preview,
                priority="high",
            )
            try:
                connector_receipt = self.connector.create_ticket(ticket, plan.idempotency_key)
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                self._mark_write_unknown(task, plan, execution, str(error))
                return
            except httpx.HTTPStatusError as error:
                if error.response.status_code >= 500:
                    self._mark_write_unknown(task, plan, execution, str(error))
                    return
                raise
            receipt = {
                "approval_ticket_id": connector_receipt.external_id,
                "status": "pending_human_review",
                "connector": connector_receipt.payload,
            }
        guard.ensure_owned()
        ensure_action_transition(execution.status, ActionStatus.SUCCEEDED)
        execution.status = ActionStatus.SUCCEEDED
        execution.receipt = receipt
        execution.updated_at = utc_now()
        ensure_action_transition(plan.status, ActionStatus.SUCCEEDED)
        plan.status = ActionStatus.SUCCEEDED
        self.repository.save_action_plan(plan)
        self.repository.save_execution(execution)

    def _mark_write_unknown(self, task, plan, execution, detail: str) -> None:
        ensure_action_transition(execution.status, ActionStatus.UNKNOWN_NEEDS_RECONCILIATION)
        execution.status = ActionStatus.UNKNOWN_NEEDS_RECONCILIATION
        execution.error_code = "CONNECTOR_WRITE_RESULT_UNKNOWN"
        execution.updated_at = utc_now()
        execution.receipt = {
            "reconciliation": "required",
            "instruction": "Query connector by idempotency key; never repeat the write",
            "error_summary": detail[:300],
        }
        self.repository.save_execution(execution)
        ensure_action_transition(plan.status, ActionStatus.UNKNOWN_NEEDS_RECONCILIATION)
        plan.status = ActionStatus.UNKNOWN_NEEDS_RECONCILIATION
        self.repository.save_action_plan(plan)
        self.repository.enqueue_task(
            RuntimeTask(
                task_id=f"task_{uuid4().hex}",
                tenant_id=task.tenant_id,
                conversation_id=task.conversation_id,
                task_type="reconcile_execution",
                payload={"execution_id": execution.execution_id},
                available_at=utc_now() + timedelta(seconds=settings.reconciliation_delay_seconds),
                max_attempts=settings.reconciliation_max_attempts,
            )
        )

    @staticmethod
    def _ticket_id(idempotency_key: str) -> str:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
        return f"ticket_{digest}"

    def _mark_action_failure(self, task: RuntimeTask, task_status: RuntimeTaskStatus) -> None:
        plan = self.repository.get_action_plan(
            str(task.payload.get("action_plan_id")), task.tenant_id
        )
        if plan is None:
            return
        execution = self.repository.execution_for_key(task.tenant_id, plan.idempotency_key)
        if execution is None or execution.status not in {
            ActionStatus.EXECUTING,
            ActionStatus.RETRYING,
        }:
            return
        target = (
            ActionStatus.FAILED
            if task_status == RuntimeTaskStatus.DEAD_LETTER
            else ActionStatus.RETRYING
        )
        ensure_action_transition(execution.status, target)
        execution.status = target
        execution.updated_at = utc_now()
        execution.error_code = "ACTION_TASK_FAILED"
        self.repository.save_execution(execution)
        if plan.status in {ActionStatus.EXECUTING, ActionStatus.RETRYING}:
            ensure_action_transition(plan.status, target)
            plan.status = target
            self.repository.save_action_plan(plan)

    def _reconcile_execution(self, task: RuntimeTask, guard: LeaseGuard) -> None:
        execution = self.repository.get_execution(str(task.payload["execution_id"]), task.tenant_id)
        if not execution or execution.status != ActionStatus.UNKNOWN_NEEDS_RECONCILIATION:
            return
        plan = self.repository.get_action_plan(execution.action_plan_id, task.tenant_id)
        if plan is None:
            raise LookupError("Action plan disappeared during reconciliation")
        guard.ensure_owned()
        result = self.reconciler.lookup(execution, plan)
        execution.reconciliation_attempts += 1
        execution.last_reconciled_at = utc_now()
        execution.updated_at = utc_now()
        if result.outcome == "pending":
            self.repository.save_execution(execution)
            raise ReconciliationPending("External result is still pending; lookup will retry")
        target = ActionStatus(result.outcome)
        ensure_action_transition(execution.status, target)
        execution.status = target
        execution.receipt = {**execution.receipt, **result.receipt}
        execution.error_code = None if target == ActionStatus.SUCCEEDED else "RECONCILED_FAILURE"
        self.repository.save_execution(execution)
        if plan.status == ActionStatus.UNKNOWN_NEEDS_RECONCILIATION:
            ensure_action_transition(plan.status, target)
            plan.status = target
            self.repository.save_action_plan(plan)


def main() -> None:
    start_http_server(settings.worker_metrics_port)
    running = True
    worker: TaskWorker | None = None

    def stop(*_args) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while running:
        try:
            worker = worker or TaskWorker(get_repository())
            worked = worker.run_once()
            if not worked:
                time.sleep(0.5)
        except Exception:
            logger.exception("worker dependency unavailable; retrying")
            worker = None
            time.sleep(1.0)


if __name__ == "__main__":
    main()
