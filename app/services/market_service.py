from __future__ import annotations

import hashlib
import json
import math
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import AuthContext, require_roles
from app.domain.models import (
    ActionExecution,
    ActionPlan,
    ActionStatus,
    AuditEvent,
    Conversation,
    ConversationEvent,
    ConversationStatus,
    EvaluationCase,
    EvaluationRun,
    Handoff,
    Message,
    PolicyVersion,
    RetrievalProfile,
    RuntimeTask,
    RuntimeTaskStatus,
    utc_now,
)
from app.domain.transitions import ensure_action_transition, ensure_conversation_transition
from app.repositories.contracts import MarketRepository
from app.schemas.api import (
    ActionPlanCreate,
    EvaluationCaseImport,
    EvaluationExpected,
    EvaluationRunRequest,
    PolicyCreate,
    PolicyUpdate,
    ReconciliationResolve,
    RetrievalDebugRequest,
    RetrievalProfileActivate,
    RetrievalProfileCalibration,
    RetrievalProfileCreate,
    RetrievalProfileValidation,
)
from app.services.authorization import AuthorizationService
from app.services.policy_chunker import content_hash
from app.services.policy_service import PolicyService


def _failure_category(result) -> str:
    details = result.details
    diagnostics = details.get("diagnostics") or {}
    reason = details.get("reason_code")
    if reason == "ACL_CONTEXT_MISMATCH" or result.metrics.get("acl_leakage"):
        return "ACL_MISMATCH_FALLBACK"
    if diagnostics.get("domain_intent") == "unsupported":
        return "OUT_OF_SCOPE_SEMANTIC_MATCH"
    if reason == "CONFLICTING_POLICIES":
        return "CONFLICT_NOT_DETECTED"
    if reason == "LOW_SCORE_MARGIN":
        return "LOW_SCORE_MARGIN"
    if reason in {"MISSING_FACT", "MISSING_CONTEXT"}:
        return "INSUFFICIENT_FACT_COVERAGE"
    if details.get("hit_policy_ids") and not details.get("citation_policy_ids"):
        return "WRONG_POLICY_FAMILY"
    return "QUERY_ANALYSIS_ERROR"


class MarketAssistantService:
    def __init__(self, repository: MarketRepository) -> None:
        self.repository = repository
        self.authorization = AuthorizationService(repository)

    def create_conversation(
        self, auth: AuthContext, customer_id: str, channel: str
    ) -> Conversation:
        owned_customer = self.authorization.require_customer(auth)
        if owned_customer != customer_id:
            raise HTTPException(status_code=404, detail="Customer not found")
        conversation = Conversation(
            conversation_id=f"conv_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            customer_id=customer_id,
            channel=channel,
            status=ConversationStatus.AI_ACTIVE,
        )
        self.repository.save_conversation(conversation)
        self._audit(auth, "conversation.create", "conversation", conversation.conversation_id)
        return conversation

    def list_conversations(self, auth: AuthContext) -> list[Conversation]:
        self.authorization.require(auth, "customer", "agent", "tenant_admin")
        customer = auth.customer_id if auth.role == "customer" else None
        return self.repository.list_conversations(auth.tenant_id, customer)

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> Conversation:
        conversation = self.repository.get_conversation(conversation_id, auth.tenant_id)
        return self.authorization.authorize_conversation(auth, conversation)

    def enqueue_message(self, auth: AuthContext, conversation_id: str, content: str) -> dict:
        self.authorization.require_customer(auth)
        conversation = self.get_conversation(auth, conversation_id)
        if conversation.owner_type == "human":
            raise HTTPException(
                status_code=409, detail="Conversation is currently handled by a human"
            )
        task_id = f"task_{uuid4().hex}"
        message = Message(
            message_id=f"msg_{uuid4().hex}",
            sender_type="customer",
            content=content,
            task_id=task_id,
        )
        conversation.messages.append(message)
        ensure_conversation_transition(conversation.status, ConversationStatus.AI_ACTIVE)
        conversation.status = ConversationStatus.AI_ACTIVE
        conversation.updated_at = utc_now()
        task = RuntimeTask(
            task_id=task_id,
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            task_type="agent_message",
            payload={
                "message_id": message.message_id,
                "content": content,
                "customer_id": conversation.customer_id,
                "actor_id": auth.subject,
                "subject": auth.subject,
                "username": auth.username,
                "role": auth.role,
                "request_id": auth.request_id,
            },
            max_attempts=settings.task_max_attempts,
        )
        event = ConversationEvent(
            event_id=f"evt_{uuid4().hex}",
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.conversation_id,
            event_type="task.queued",
            data={"task_id": task_id, "message_id": message.message_id},
        )
        self.repository.enqueue_message_atomic(conversation, task, event)
        self._audit(auth, "conversation.message.queued", "runtime_task", task_id)
        return {"message_id": message.message_id, "task_id": task_id, "status": task.status}

    def create_handoff(self, auth: AuthContext, conversation_id: str, reason: str) -> Handoff:
        self.authorization.require_customer(auth)
        conversation = self.get_conversation(auth, conversation_id)
        existing = next(
            (
                item
                for item in self.repository.list_handoffs(auth.tenant_id)
                if item.conversation_id == conversation_id and item.status in {"queued", "claimed"}
            ),
            None,
        )
        if existing:
            return existing
        ensure_conversation_transition(conversation.status, ConversationStatus.HANDOFF_QUEUED)
        conversation.status = ConversationStatus.HANDOFF_QUEUED
        conversation.owner_type = "handoff_queue"
        conversation.updated_at = utc_now()
        latest_facts = next(
            (message.facts for message in reversed(conversation.messages) if message.facts), {}
        )
        handoff = Handoff(
            handoff_id=f"handoff_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            conversation_id=conversation_id,
            queue_id="queue_general",
            reason=reason,
            context_snapshot={
                "customer_id": conversation.customer_id,
                "summary": [message.content for message in conversation.messages[-8:]],
                "reason": reason,
                "latest_order": latest_facts.get("order"),
                "policy_citations": latest_facts.get("citations", []),
                "answerability": latest_facts.get("answerability"),
                "action_plan": latest_facts.get("action_plan"),
                "risk": "high" if reason in {"high_risk", "complaint"} else "normal",
                "sla_due_at": (utc_now() + timedelta(minutes=15)).isoformat(),
            },
            priority=80 if reason in {"high_risk", "complaint"} else 50,
        )
        self.repository.save_conversation(conversation)
        handoff = self.repository.save_handoff(handoff)
        self._event(conversation, "handoff.queued", {"handoff_id": handoff.handoff_id})
        self._audit(auth, "handoff.create", "handoff", handoff.handoff_id)
        return handoff

    def list_handoffs(self, auth: AuthContext, status: str | None = None) -> list[Handoff]:
        allowed = self.authorization.handoff_queue_scope(auth)
        return self.repository.list_handoffs(auth.tenant_id, status, allowed)

    def claim_handoff(self, auth: AuthContext, handoff_id: str) -> Handoff:
        allowed = self.authorization.handoff_queue_scope(auth)
        handoff = self.repository.claim_handoff_atomic(
            handoff_id, auth.tenant_id, auth.subject, allowed
        )
        if not handoff:
            raise HTTPException(status_code=409, detail="Handoff is unavailable or already claimed")
        conversation = self.get_conversation(auth, handoff.conversation_id)
        ensure_conversation_transition(conversation.status, ConversationStatus.HUMAN_ACTIVE)
        conversation.status = ConversationStatus.HUMAN_ACTIVE
        conversation.owner_type = "human"
        self.repository.save_conversation(conversation)
        self._event(
            conversation, "handoff.claimed", {"handoff_id": handoff_id, "agent_id": auth.subject}
        )
        self._audit(auth, "handoff.claim", "handoff", handoff_id)
        return handoff

    def release_handoff(self, auth: AuthContext, handoff_id: str) -> Handoff:
        handoff = self.authorization.authorize_handoff(
            auth, self.repository.get_handoff(handoff_id, auth.tenant_id)
        )
        if handoff.status != "claimed" or handoff.assigned_agent_id != auth.subject:
            raise HTTPException(
                status_code=409, detail="Only the assigned agent can release this handoff"
            )
        handoff.status = "queued"
        handoff.assigned_agent_id = None
        self.repository.save_handoff(handoff)
        conversation = self.get_conversation(auth, handoff.conversation_id)
        ensure_conversation_transition(conversation.status, ConversationStatus.HANDOFF_QUEUED)
        conversation.status = ConversationStatus.HANDOFF_QUEUED
        conversation.owner_type = "handoff_queue"
        self.repository.save_conversation(conversation)
        self._event(conversation, "handoff.released", {"handoff_id": handoff_id})
        self._audit(auth, "handoff.release", "handoff", handoff_id)
        return handoff

    def transfer_handoff(
        self, auth: AuthContext, handoff_id: str, queue_id: str, reason: str
    ) -> Handoff:
        handoff = self.authorization.authorize_handoff(
            auth, self.repository.get_handoff(handoff_id, auth.tenant_id)
        )
        if handoff.status != "claimed" or (
            auth.role == "agent" and handoff.assigned_agent_id != auth.subject
        ):
            raise HTTPException(status_code=409, detail="Handoff must be claimed before transfer")
        allowed = self.authorization.handoff_queue_scope(auth)
        if allowed is not None and queue_id not in allowed:
            raise HTTPException(status_code=403, detail="Target queue is not authorized")
        previous_agent = handoff.assigned_agent_id
        history = list(handoff.context_snapshot.get("transfer_history") or [])
        history.append(
            {
                "from_agent_id": previous_agent,
                "to_queue_id": queue_id,
                "reason": reason,
                "transferred_at": utc_now().isoformat(),
            }
        )
        handoff.queue_id = queue_id
        handoff.status = "queued"
        handoff.assigned_agent_id = None
        handoff.context_snapshot["transfer_history"] = history
        self.repository.save_handoff(handoff)
        conversation = self.get_conversation(auth, handoff.conversation_id)
        ensure_conversation_transition(conversation.status, ConversationStatus.HANDOFF_QUEUED)
        conversation.status = ConversationStatus.HANDOFF_QUEUED
        conversation.owner_type = "handoff_queue"
        self.repository.save_conversation(conversation)
        self._event(
            conversation,
            "handoff.transferred",
            {"handoff_id": handoff_id, "queue_id": queue_id, "reason": reason},
        )
        self._audit(
            auth,
            "handoff.transfer",
            "handoff",
            handoff_id,
            {"queue_id": queue_id, "reason": reason},
        )
        return handoff

    def handoff_reply(self, auth: AuthContext, handoff_id: str, content: str) -> Message:
        handoff = self.authorization.authorize_handoff(
            auth, self.repository.get_handoff(handoff_id, auth.tenant_id)
        )
        if handoff.status != "claimed" or handoff.assigned_agent_id != auth.subject:
            raise HTTPException(
                status_code=409, detail="Handoff must be claimed by the current agent"
            )
        conversation = self.get_conversation(auth, handoff.conversation_id)
        message = Message(
            message_id=f"msg_{uuid4().hex}", sender_type="human_agent", content=content
        )
        conversation.messages.append(message)
        handoff.context_snapshot.setdefault("first_response_at", utc_now().isoformat())
        self.repository.save_handoff(handoff)
        conversation.updated_at = utc_now()
        self.repository.save_conversation(conversation)
        self._event(
            conversation, "handoff.reply", {"message_id": message.message_id, "content": content}
        )
        self._audit(auth, "handoff.reply", "handoff", handoff_id)
        return message

    def resolve_handoff(self, auth: AuthContext, handoff_id: str, resume_ai: bool) -> Handoff:
        handoff = self.authorization.authorize_handoff(
            auth, self.repository.get_handoff(handoff_id, auth.tenant_id)
        )
        if handoff.assigned_agent_id not in {auth.subject, None}:
            raise HTTPException(status_code=404, detail="Handoff not found")
        handoff.status = "resolved"
        handoff.context_snapshot["resolved_at"] = utc_now().isoformat()
        self.repository.save_handoff(handoff)
        conversation = self.get_conversation(auth, handoff.conversation_id)
        target = ConversationStatus.AI_ACTIVE if resume_ai else ConversationStatus.RESOLVED
        ensure_conversation_transition(conversation.status, target)
        conversation.status = target
        conversation.owner_type = "ai" if resume_ai else "none"
        self.repository.save_conversation(conversation)
        self._event(conversation, "handoff.resolved", {"resume_ai": resume_ai})
        self._audit(auth, "handoff.resolve", "handoff", handoff_id)
        return handoff

    def create_action_plan(self, auth: AuthContext, data: ActionPlanCreate) -> ActionPlan:
        self.authorization.require_customer(auth)
        conversation = self.get_conversation(auth, data.conversation_id)
        if data.action_type == "create_ticket":
            risk, preview = "medium", f"为会话 {conversation.conversation_id} 创建一张售后工单"
            payload = data.payload
        else:
            proposal_id = str(data.payload.get("proposal_id") or "")
            proposal = self.repository.get_compensation_proposal(
                proposal_id, auth.tenant_id
            )
            if (
                proposal is None
                or proposal.customer_id != conversation.customer_id
                or proposal.conversation_id != conversation.conversation_id
            ):
                raise HTTPException(status_code=404, detail="Compensation proposal not found")
            if proposal.status != "proposed" or proposal.expires_at <= utc_now():
                raise HTTPException(status_code=409, detail="Compensation proposal is no longer valid")
            amount = proposal.recommended_amount
            payload = {
                "proposal_id": proposal.proposal_id,
                "order_id": proposal.order_id,
                "amount": amount,
                "retrieval_snapshot_id": proposal.retrieval_snapshot_id,
                "policy_version_id": proposal.policy_version_id,
                "reason": proposal.reason,
            }
            risk, preview = "high", f"提交 {amount:.2f} 元补偿申请；仅创建人工审批任务"
        plan = ActionPlan(
            action_plan_id=f"plan_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            customer_id=conversation.customer_id,
            conversation_id=conversation.conversation_id,
            action_type=data.action_type,
            risk_level=risk,
            payload=payload,
            preview=preview,
            idempotency_key=data.idempotency_key,
            status=ActionStatus.APPROVAL_REQUIRED,
        )
        self.repository.save_action_plan(plan)
        ensure_conversation_transition(conversation.status, ConversationStatus.APPROVAL_PENDING)
        conversation.status = ConversationStatus.APPROVAL_PENDING
        self.repository.save_conversation(conversation)
        self._audit(auth, "action_plan.create", "action_plan", plan.action_plan_id)
        return plan

    def confirm_action(self, auth: AuthContext, action_plan_id: str) -> ActionExecution:
        plan = self.repository.get_action_plan(action_plan_id, auth.tenant_id)
        if auth.role == "platform_admin" or not plan or (
            auth.role == "customer" and plan.customer_id != auth.customer_id
        ):
            raise HTTPException(status_code=404, detail="Action plan not found")
        existing = self.repository.execution_for_key(auth.tenant_id, plan.idempotency_key)
        if existing:
            return existing
        task = RuntimeTask(
            task_id=f"task_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            conversation_id=plan.conversation_id,
            task_type="execute_action",
            payload={
                "action_plan_id": plan.action_plan_id,
                "actor_id": auth.subject,
                "subject": auth.subject,
                "username": auth.username,
                "customer_id": plan.customer_id,
                "role": auth.role,
            },
            max_attempts=settings.task_max_attempts,
        )
        ensure_action_transition(plan.status, ActionStatus.QUEUED)
        plan.status = ActionStatus.QUEUED
        execution = ActionExecution(
            execution_id=f"exec_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            action_plan_id=plan.action_plan_id,
            idempotency_key=plan.idempotency_key,
            status=ActionStatus.QUEUED,
            task_id=task.task_id,
        )
        execution = self.repository.confirm_action_atomic(plan, task, execution)
        self._audit(auth, "action.confirm", "action_execution", execution.execution_id)
        return execution

    def get_execution(self, auth: AuthContext, execution_id: str) -> ActionExecution:
        execution = self.repository.get_execution(execution_id, auth.tenant_id)
        if not execution:
            raise HTTPException(status_code=404, detail="Action execution not found")
        plan = self.repository.get_action_plan(execution.action_plan_id, auth.tenant_id)
        if auth.role == "platform_admin" or (
            auth.role == "customer" and (not plan or plan.customer_id != auth.customer_id)
        ):
            raise HTTPException(status_code=404, detail="Action execution not found")
        return execution

    def cancel_execution(self, auth: AuthContext, execution_id: str) -> ActionExecution:
        execution = self.get_execution(auth, execution_id)
        if execution.status not in {ActionStatus.QUEUED, ActionStatus.RETRYING}:
            raise HTTPException(status_code=409, detail="Execution can no longer be cancelled")
        if execution.task_id:
            task = self.repository.cancel_task(execution.task_id, auth.tenant_id)
            if not task or task.status != RuntimeTaskStatus.CANCELLED:
                raise HTTPException(status_code=409, detail="Execution has already started")
        ensure_action_transition(execution.status, ActionStatus.CANCELLED)
        execution.status = ActionStatus.CANCELLED
        execution.updated_at = utc_now()
        self.repository.save_execution(execution)
        return execution

    def list_runtime_tasks(
        self, auth: AuthContext, status: str | None, task_type: str | None
    ) -> list[dict]:
        require_roles(auth, "tenant_admin")
        tasks = self.repository.list_tasks(auth.tenant_id, status, task_type)
        return [self._task_view(task, include_payload=False) for task in tasks]

    def get_runtime_task(self, auth: AuthContext, task_id: str) -> dict:
        require_roles(auth, "tenant_admin")
        task = self.repository.get_task(task_id, auth.tenant_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Runtime task not found")
        value = self._task_view(task, include_payload=True)
        value["events"] = self.repository.list_task_events(task_id, auth.tenant_id)
        return value

    def replay_runtime_task(self, auth: AuthContext, task_id: str, reason: str) -> RuntimeTask:
        require_roles(auth, "tenant_admin")
        try:
            task = self.repository.replay_dead_letter(
                task_id, auth.tenant_id, auth.subject, reason
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Runtime task not found") from error
        except ValueError as error:
            raise ApiError(
                409, "TASK_NOT_REPLAYABLE", "Only dead-letter tasks can be replayed"
            ) from error
        self._audit(
            auth,
            "runtime_task.replay",
            "runtime_task",
            task.task_id,
            {"replay_of_task_id": task_id, "reason": reason},
        )
        return task

    def cancel_runtime_task(self, auth: AuthContext, task_id: str) -> RuntimeTask:
        require_roles(auth, "tenant_admin")
        task = self.repository.cancel_task(task_id, auth.tenant_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Runtime task not found")
        if task.status != RuntimeTaskStatus.CANCELLED:
            raise ApiError(
                409, "TASK_NOT_CANCELLABLE", "Only queued or retrying tasks can be cancelled"
            )
        self._audit(auth, "runtime_task.cancel", "runtime_task", task_id)
        return task

    def list_workers(self, auth: AuthContext) -> list[dict]:
        require_roles(auth, "platform_admin")
        now = utc_now()
        values = self.repository.list_workers()
        for value in values:
            value["status"] = (
                "online" if (now - value["last_heartbeat_at"]).total_seconds() <= 15 else "stale"
            )
        return values

    def runtime_summary(self, auth: AuthContext) -> dict:
        require_roles(auth, "tenant_admin", "platform_admin")
        task_counts: dict[str, int] = {}
        if auth.role == "tenant_admin":
            for task in self.repository.list_tasks(auth.tenant_id):
                task_counts[task.status.value] = task_counts.get(task.status.value, 0) + 1
        now = utc_now()
        worker_counts = {"online": 0, "stale": 0}
        for worker in self.repository.list_workers():
            status = (
                "online" if (now - worker["last_heartbeat_at"]).total_seconds() <= 15 else "stale"
            )
            worker_counts[status] += 1
        return {"tasks": task_counts, "workers": worker_counts}

    def list_reconciliations(self, auth: AuthContext) -> list[ActionExecution]:
        require_roles(auth, "tenant_admin")
        return self.repository.list_reconciliations(auth.tenant_id)

    def resolve_reconciliation(
        self, auth: AuthContext, execution_id: str, data: ReconciliationResolve
    ) -> ActionExecution:
        require_roles(auth, "tenant_admin")
        execution = self.repository.get_execution(execution_id, auth.tenant_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Action execution not found")
        target = ActionStatus(data.outcome)
        ensure_action_transition(execution.status, target)
        execution.status = target
        execution.updated_at = utc_now()
        execution.last_reconciled_at = utc_now()
        execution.receipt = {
            **execution.receipt,
            **self._redact(data.receipt),
            "manual_resolution": True,
            "reason": data.reason,
        }
        execution.error_code = None if target == ActionStatus.SUCCEEDED else "MANUAL_RECONCILIATION"
        self.repository.save_execution(execution)
        plan = self.repository.get_action_plan(execution.action_plan_id, auth.tenant_id)
        if plan and plan.status == ActionStatus.UNKNOWN_NEEDS_RECONCILIATION:
            ensure_action_transition(plan.status, target)
            plan.status = target
            self.repository.save_action_plan(plan)
        self._audit(
            auth,
            "action_execution.reconcile.manual",
            "action_execution",
            execution_id,
            {"outcome": data.outcome, "reason": data.reason},
        )
        return execution

    def _task_view(self, task: RuntimeTask, include_payload: bool) -> dict:
        value = task.model_dump(mode="json", exclude={"payload"})
        value["payload"] = (
            self._redact(task.payload) if include_payload else {"keys": sorted(task.payload.keys())}
        )
        return value

    @classmethod
    def _redact(cls, value):
        sensitive = {"token", "secret", "password", "address", "phone", "api_key"}
        if isinstance(value, dict):
            return {
                key: "***" if key.lower() in sensitive else cls._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        return value

    def list_tickets(self, auth: AuthContext):
        self.authorization.require(auth, "customer", "agent", "tenant_admin")
        customer = auth.customer_id if auth.role == "customer" else None
        return self.repository.list_tickets(auth.tenant_id, customer)

    def get_ticket(self, auth: AuthContext, ticket_id: str):
        self.authorization.require(auth, "customer", "agent", "tenant_admin")
        customer = auth.customer_id if auth.role == "customer" else None
        ticket = self.repository.get_ticket(ticket_id, auth.tenant_id, customer)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        return ticket

    def create_policy(self, auth: AuthContext, data: PolicyCreate) -> PolicyVersion:
        require_roles(auth, "tenant_admin")
        if data.effective_from and data.effective_to and data.effective_from >= data.effective_to:
            raise ApiError(422, "INVALID_EFFECTIVE_RANGE", "政策失效时间必须晚于生效时间")
        policy = PolicyVersion(
            policy_version_id=f"policy_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            content_hash=content_hash(data.content),
            **data.model_dump(),
        )
        try:
            self.repository.save_policy(policy)
        except Exception as error:
            raise ApiError(409, "POLICY_VERSION_EXISTS", "同一作用域下版本号必须唯一") from error
        self._audit(auth, "policy.create", "policy_version", policy.policy_version_id)
        return policy

    def list_policies(
        self, auth: AuthContext, scope: str | None = None, status: str | None = None
    ) -> list[PolicyVersion]:
        require_roles(auth, "tenant_admin")
        return self.repository.list_policy_versions(auth.tenant_id, scope, status)

    def get_policy(self, auth: AuthContext, policy_id: str) -> PolicyVersion:
        require_roles(auth, "tenant_admin")
        policy = self.repository.get_policy(policy_id, auth.tenant_id)
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        return policy

    def update_policy(
        self, auth: AuthContext, policy_id: str, data: PolicyUpdate
    ) -> PolicyVersion:
        policy = self.get_policy(auth, policy_id)
        if policy.status != "draft":
            raise ApiError(409, "POLICY_IMMUTABLE", "已发布政策版本不可原地修改")
        updates = data.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(policy, key, value)
        policy.content_hash = content_hash(policy.content)
        policy.updated_at = utc_now()
        if policy.effective_from and policy.effective_to and policy.effective_from >= policy.effective_to:
            raise ApiError(422, "INVALID_EFFECTIVE_RANGE", "政策失效时间必须晚于生效时间")
        updated = self.repository.update_policy_draft(policy)
        self._audit(auth, "policy.update", "policy_version", policy_id)
        return updated

    def publish_policy(self, auth: AuthContext, policy_id: str) -> PolicyVersion:
        require_roles(auth, "tenant_admin")
        try:
            policy = self.repository.publish_policy(policy_id, auth.tenant_id, auth.subject)
        except ValueError as error:
            raise ApiError(409, "POLICY_NOT_PUBLISHABLE", str(error)) from error
        if not policy:
            raise HTTPException(status_code=404, detail="Policy not found")
        self._audit(auth, "policy.publish", "policy_version", policy_id)
        return policy

    def reindex_policy(self, auth: AuthContext, policy_id: str) -> dict:
        policy = self.get_policy(auth, policy_id)
        if policy.status not in {"published", "superseded"}:
            raise ApiError(409, "POLICY_NOT_REINDEXABLE", "只有已建立索引的版本可重建索引")
        task = RuntimeTask(
            task_id=f"task_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            task_type="reindex_policy",
            payload={"policy_id": policy_id, "actor_id": auth.subject},
        )
        self.repository.enqueue_task(task)
        self._audit(auth, "policy.reindex.request", "policy_version", policy_id)
        return {"task_id": task.task_id, "status": task.status}

    def debug_retrieval(self, auth: AuthContext, data: RetrievalDebugRequest) -> dict:
        require_roles(auth, "tenant_admin")
        decision = PolicyService(
            self.repository, settings.answerability_threshold
        ).answerability_gate(auth.tenant_id, data.query, filters=data.filters)
        return decision.to_dict()

    def import_evaluation_cases(
        self, auth: AuthContext, data: EvaluationCaseImport
    ) -> dict:
        require_roles(auth, "tenant_admin")
        if data.dataset_version in {"v2", "v3"}:
            for case in data.cases:
                EvaluationExpected.model_validate(case.expected)
        cases = [
            EvaluationCase(
                evaluation_case_id=f"eval_case_{uuid4().hex}",
                tenant_id=auth.tenant_id,
                dataset=data.dataset,
                dataset_version=data.dataset_version,
                **case.model_dump(),
            )
            for case in data.cases
        ]
        try:
            count = self.repository.import_evaluation_cases(cases)
        except ValueError as error:
            if str(error) == "DATASET_FROZEN":
                raise ApiError(409, "DATASET_FROZEN", "评测数据集已冻结，不允许修改") from error
            raise
        self._audit(auth, "evaluation.case.import", "evaluation_dataset", data.dataset)
        return {"imported": count, "dataset": data.dataset, "dataset_version": data.dataset_version}

    def list_evaluation_datasets(self, auth: AuthContext):
        require_roles(auth, "tenant_admin")
        return self.repository.list_evaluation_datasets(auth.tenant_id)

    def freeze_evaluation_dataset(
        self, auth: AuthContext, dataset: str, version: str
    ):
        require_roles(auth, "tenant_admin")
        value = self.repository.freeze_evaluation_dataset(auth.tenant_id, dataset, version)
        if value is None:
            raise HTTPException(status_code=404, detail="Evaluation dataset not found")
        self._audit(
            auth,
            "evaluation.dataset.freeze",
            "evaluation_dataset",
            f"{dataset}:{version}",
        )
        return value

    def list_evaluation_cases(
        self, auth: AuthContext, dataset: str, dataset_version: str
    ) -> list[EvaluationCase]:
        require_roles(auth, "tenant_admin")
        return self.repository.list_evaluation_cases(auth.tenant_id, dataset, dataset_version)

    def run_evaluation(
        self, auth: AuthContext, data: EvaluationRunRequest | None = None
    ) -> EvaluationRun:
        require_roles(auth, "tenant_admin")
        data = data or EvaluationRunRequest()
        profile = (
            self.repository.get_retrieval_profile(data.retrieval_profile_id, auth.tenant_id)
            if data.retrieval_profile_id
            else None
        )
        policy_service = PolicyService(
            self.repository, retrieval_profile_id=data.retrieval_profile_id
        )
        run = EvaluationRun(
            evaluation_run_id=f"eval_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            dataset=data.dataset,
            dataset_version=data.dataset_version,
            config_snapshot={
                "retriever": profile.retriever_version if profile else PolicyService.retriever_version,
                "gate": profile.gate_version if profile else PolicyService.gate_version,
                "threshold": settings.answerability_threshold,
                "embedding_model": profile.embedding_model if profile else policy_service.embeddings.model_id,
                "embedding_dimension": profile.embedding_dimension if profile else policy_service.embeddings.dimensions,
                "normalization": profile.normalization_version if profile else "query-normalizer-v4",
                "retrieval_profile_id": data.retrieval_profile_id,
                "split": data.split,
                "gate_config": profile.gate_config if profile else PolicyService.default_gate_config,
                "config_hash": profile.config_hash if profile else "",
                "query_analyzer_version": "v4",
                "scope_match_version": "v4",
                "threshold_grid_version": "week4-v4-grid-1",
                "policy_corpus_hash": self._policy_corpus_hash(auth.tenant_id),
                "evaluator": data.evaluator,
                "provider": data.provider,
            },
            summary={"case_count": 0},
            metric_schema_version=(
                "agent-v1"
                if data.evaluator == "agent"
                else
                "v3"
                if data.dataset_version == "v3"
                or data.dataset in {"week4_gate_calibration", "week4_hard_negative"}
                else "v2"
            ),
            query_analyzer_version="v4",
            scope_match_version="v4",
            threshold_grid_version="week4-v4-grid-1",
        )
        self.repository.save_evaluation_run(run)
        self.repository.enqueue_task(
            RuntimeTask(
                task_id=f"task_{uuid4().hex}",
                tenant_id=auth.tenant_id,
                task_type="evaluation_run",
                payload={"evaluation_run_id": run.evaluation_run_id},
            )
        )
        return run

    def get_evaluation(self, auth: AuthContext, run_id: str) -> EvaluationRun:
        require_roles(auth, "tenant_admin")
        run = self.repository.get_evaluation_run(run_id, auth.tenant_id)
        if not run:
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        return run.model_copy(
            update={"summary": {**run.summary, "results": [item.model_dump(mode="json") for item in self.repository.list_evaluation_results(run_id, auth.tenant_id)]}}
        )

    def list_evaluations(
        self,
        auth: AuthContext,
        dataset: str | None = None,
        dataset_version: str | None = None,
    ) -> list[EvaluationRun]:
        require_roles(auth, "tenant_admin")
        values = self.repository.list_evaluation_runs(auth.tenant_id)
        if dataset:
            values = [value for value in values if value.dataset == dataset]
        if dataset_version:
            values = [
                value for value in values if value.dataset_version == dataset_version
            ]
        return values

    def compare_evaluations(self, auth: AuthContext, left: str, right: str) -> dict:
        left_run = self.get_evaluation(auth, left)
        right_run = self.get_evaluation(auth, right)
        reasons = []
        if (left_run.dataset, left_run.dataset_version) != (
            right_run.dataset,
            right_run.dataset_version,
        ):
            reasons.append("dataset_or_version_differs")
        if left_run.metric_schema_version != right_run.metric_schema_version:
            reasons.append("metric_schema_differs")
        if reasons:
            return {
                "left": left,
                "right": right,
                "comparable": False,
                "reasons": reasons,
                "metrics": {},
            }
        keys = sorted(
            (set(left_run.summary) | set(right_run.summary))
            - {"results", "confusion_matrix"}
        )
        return {
            "left": left,
            "right": right,
            "comparable": True,
            "reasons": [],
            "metrics": {
                key: {
                    "left": left_run.summary.get(key),
                    "right": right_run.summary.get(key),
                    "delta": self._numeric_delta(left_run.summary.get(key), right_run.summary.get(key)),
                }
                for key in keys
            },
        }

    def evaluation_failures(self, auth: AuthContext, run_id: str) -> list[dict]:
        run = self.get_evaluation(auth, run_id)
        del run
        results = self.repository.list_evaluation_results(run_id, auth.tenant_id)
        return [
            {
                **item.model_dump(mode="json"),
                "failure_category": _failure_category(item),
            }
            for item in results
            if not item.passed
        ]

    def evaluation_diagnostics(self, auth: AuthContext, run_id: str) -> dict:
        run = self.get_evaluation(auth, run_id)
        failures = self.evaluation_failures(auth, run_id)
        categories: dict[str, int] = {}
        for item in failures:
            category = item["failure_category"]
            categories[category] = categories.get(category, 0) + 1
        return {
            "run_id": run_id,
            "dataset": f"{run.dataset}/{run.dataset_version}",
            "quality_status": run.quality_status,
            "summary": run.summary,
            "failure_count": len(failures),
            "failure_categories": categories,
        }

    def create_retrieval_profile(
        self, auth: AuthContext, data: RetrievalProfileCreate
    ) -> RetrievalProfile:
        require_roles(auth, "tenant_admin")
        existing = self.repository.get_retrieval_profile(
            data.retrieval_profile_id, auth.tenant_id
        )
        if existing:
            raise ApiError(409, "RETRIEVAL_PROFILE_EXISTS", "检索配置已存在")
        profile = RetrievalProfile(
            tenant_id=auth.tenant_id,
            **data.model_dump(),
            gate_version="v4",
            retriever_version="hybrid-rrf-v4",
            normalization_version="query-normalizer-v4",
            gate_config={**PolicyService.default_gate_config},
        )
        self.repository.save_retrieval_profile(profile)
        self._audit(
            auth,
            "retrieval_profile.create",
            "retrieval_profile",
            profile.retrieval_profile_id,
        )
        return profile

    def list_retrieval_profiles(self, auth: AuthContext) -> list[RetrievalProfile]:
        require_roles(auth, "tenant_admin")
        return self.repository.list_retrieval_profiles(auth.tenant_id)

    def calibrate_retrieval_profile(
        self, auth: AuthContext, profile_id: str, data: RetrievalProfileCalibration
    ) -> RetrievalProfile:
        require_roles(auth, "tenant_admin")
        profile = self.repository.get_retrieval_profile(profile_id, auth.tenant_id)
        run = self.repository.get_evaluation_run(data.evaluation_run_id, auth.tenant_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        calibration_ref = profile.calibration_dataset or "week4_gate_calibration/v1"
        calibration_dataset, calibration_version = calibration_ref.rsplit("/", 1)
        if (
            run is None
            or run.status != "completed"
            or run.dataset != calibration_dataset
            or run.dataset_version != calibration_version
        ):
            raise ApiError(409, "INVALID_CALIBRATION_RUN", f"必须使用已完成的 {calibration_ref} 运行")
        if profile.status == "rejected":
            raise ApiError(409, "PROFILE_IMMUTABLE", "Rejected retrieval profiles are immutable")
        if run.config_snapshot.get("retrieval_profile_id") != profile_id:
            raise ApiError(409, "PROFILE_RUN_MISMATCH", "评测运行不属于该检索配置")
        results = self.repository.list_evaluation_results(run.evaluation_run_id, auth.tenant_id)
        samples = results
        if not samples:
            raise ApiError(409, "EMPTY_CALIBRATION_SET", "Calibration 子集没有结果")
        best: tuple[float, float, float, float] | None = None
        for min_rrf in (0.012, 0.015, 0.018, 0.02):
            for semantic in (0.35, 0.45, 0.55, 0.65):
                for margin in (0.0, 0.02, 0.05, 0.10):
                    predicted: list[tuple[bool, bool]] = []
                    for result in samples:
                        diagnostics = result.details.get("diagnostics") or {}
                        candidates = result.details.get("candidates") or []
                        top = candidates[0] if candidates else {}
                        expected_answer = result.details.get("expected_decision") == "answer"
                        allowed = bool(candidates) and float(top.get("rrf_score") or 0) >= min_rrf
                        if diagnostics.get("semantic_only"):
                            allowed = (
                                allowed
                                and diagnostics.get("domain_intent") == "supported"
                                and float(top.get("semantic_score") or 0) >= semantic
                                and float(diagnostics.get("top1_top2_margin") or 0) >= margin
                            )
                        elif diagnostics.get("lexical_anchor_quality") == "weak":
                            allowed = allowed and float(
                                diagnostics.get("top1_top2_margin") or 0
                            ) >= margin
                        blocked_reason = result.details.get("reason_code") in {
                            "UNSUPPORTED_SCOPE", "ACL_CONTEXT_MISMATCH", "CONFLICTING_POLICIES",
                            "MISSING_CONTEXT", "MISSING_FACT", "NO_EVIDENCE",
                        }
                        allowed = allowed and not blocked_reason
                        predicted.append((expected_answer, allowed))
                    false_answers = sum(not expected and allowed for expected, allowed in predicted)
                    recall = (
                        sum(expected and allowed for expected, allowed in predicted)
                        / max(1, sum(expected for expected, _ in predicted))
                    )
                    candidate = (recall, margin, semantic, min_rrf)
                    if false_answers == 0 and (best is None or candidate > best):
                        best = candidate
        if best is None:
            profile.status = "rejected"
            profile.rejected_reason = "No calibration threshold achieved false_answer=0"
            self.repository.save_retrieval_profile(profile)
            raise ApiError(409, "CALIBRATION_FAILED", profile.rejected_reason)
        recall, margin, semantic, min_rrf = best
        profile.gate_version = "v4"
        profile.retriever_version = "hybrid-rrf-v4"
        profile.normalization_version = "query-normalizer-v4"
        profile.gate_config = {
            **PolicyService.default_gate_config,
            "min_rrf": min_rrf,
            "semantic_only_min_score": semantic,
            "min_top1_top2_margin": margin,
        }
        profile.calibration_run_id = run.evaluation_run_id
        profile.calibration_dataset = calibration_ref
        profile.config_hash = self._retrieval_profile_config_hash(profile)
        profile.quality_report = {
            **profile.quality_report,
            "calibration": {
                "false_answer_count": 0,
                "answerability_recall": recall,
                "dataset": calibration_ref,
                "threshold_grid_version": "week4-v4-grid-1",
            },
        }
        profile.updated_at = utc_now()
        self.repository.save_retrieval_profile(profile)
        self._audit(auth, "retrieval_profile.calibrate", "retrieval_profile", profile_id)
        return profile

    def validate_retrieval_profile(
        self, auth: AuthContext, profile_id: str, data: RetrievalProfileValidation
    ) -> RetrievalProfile:
        require_roles(auth, "tenant_admin")
        profile = self.repository.get_retrieval_profile(profile_id, auth.tenant_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        gold = self.repository.get_evaluation_run(data.gold_run_id, auth.tenant_id)
        holdout = self.repository.get_evaluation_run(data.holdout_run_id, auth.tenant_id)
        calibration = (
            self.repository.get_evaluation_run(profile.calibration_run_id, auth.tenant_id)
            if profile.calibration_run_id
            else None
        )
        validation_refs = profile.validation_datasets or ["week4_gold/v3", "week4_hard_negative/v2"]
        if len(validation_refs) != 2:
            raise ApiError(409, "INVALID_VALIDATION_DATASETS", "Profile 必须绑定 Gold 和 holdout 两个数据集")
        gold_ref, holdout_ref = validation_refs
        gold_dataset, gold_version = gold_ref.rsplit("/", 1)
        holdout_dataset, holdout_version = holdout_ref.rsplit("/", 1)
        valid = (
            calibration is not None
            and gold is not None
            and gold.quality_status == "passed"
            and gold.dataset == gold_dataset
            and gold.dataset_version == gold_version
            and holdout is not None
            and holdout.quality_status == "passed"
            and holdout.dataset == holdout_dataset
            and holdout.dataset_version == holdout_version
            and gold.config_snapshot.get("retrieval_profile_id") == profile_id
            and holdout.config_snapshot.get("retrieval_profile_id") == profile_id
            and gold.config_snapshot.get("config_hash") == profile.config_hash
            and holdout.config_snapshot.get("config_hash") == profile.config_hash
            and gold.config_snapshot.get("gate") == "v4"
            and holdout.config_snapshot.get("gate") == "v4"
            and gold.config_snapshot.get("policy_corpus_hash")
            == holdout.config_snapshot.get("policy_corpus_hash")
            == calibration.config_snapshot.get("policy_corpus_hash")
            and profile.indexed_chunks == profile.total_chunks
            and float(gold.summary.get("recall_at_5", 0)) >= 0.90
            and float(gold.summary.get("mrr", 0)) >= 0.80
            and float(gold.summary.get("citation_policy_accuracy", 0)) == 1.0
            and float(gold.summary.get("citation_version_accuracy", 0)) == 1.0
            and float(gold.summary.get("citation_chunk_accuracy", 0)) >= 0.95
            and float(gold.summary.get("answerability_recall", 0)) >= 0.90
            and float(holdout.summary.get("false_answer_rate", 1)) == 0
            and float(holdout.summary.get("unanswerable_block_rate", 0)) == 1.0
            and all(
                float(summary.get(metric, 1)) == 0
                for summary in (gold.summary, holdout.summary)
                for metric in ("old_version_hit_rate", "tenant_leakage_rate", "acl_leakage_rate")
            )
        )
        profile.evaluation_run_id = data.gold_run_id if valid else None
        profile.holdout_run_id = data.holdout_run_id if valid else None
        profile.validation_datasets = validation_refs
        profile.rejected_reason = None if valid else "Formal gold/holdout quality gate failed"
        profile.quality_report = {
            **profile.quality_report,
            "validation": {"passed": valid, "gold_run_id": data.gold_run_id, "holdout_run_id": data.holdout_run_id},
        }
        if not valid:
            profile.status = "rejected"
        profile.updated_at = utc_now()
        self.repository.save_retrieval_profile(profile)
        self._audit(auth, "retrieval_profile.validate", "retrieval_profile", profile_id)
        if not valid:
            raise ApiError(409, "PROFILE_VALIDATION_FAILED", profile.rejected_reason or "验证失败")
        return profile

    def reindex_retrieval_profile(self, auth: AuthContext, profile_id: str) -> dict:
        require_roles(auth, "tenant_admin")
        if self.repository.get_retrieval_profile(profile_id, auth.tenant_id) is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        task = RuntimeTask(
            task_id=f"task_{uuid4().hex}",
            tenant_id=auth.tenant_id,
            task_type="reindex_retrieval_profile",
            payload={"retrieval_profile_id": profile_id, "actor_id": auth.subject},
        )
        self.repository.enqueue_task(task)
        self._audit(auth, "retrieval_profile.reindex", "retrieval_profile", profile_id)
        return {"task_id": task.task_id, "status": task.status}

    def evaluate_retrieval_profile(self, auth: AuthContext, profile_id: str) -> EvaluationRun:
        require_roles(auth, "tenant_admin")
        profile = self.repository.get_retrieval_profile(profile_id, auth.tenant_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        if profile.status not in {"ready", "active"}:
            raise ApiError(409, "PROFILE_NOT_READY", "检索配置尚未完成索引")
        return self.run_evaluation(
            auth,
            EvaluationRunRequest(
                dataset="week4_gold",
                dataset_version="v3",
                retrieval_profile_id=profile_id,
            ),
        )

    def activate_retrieval_profile(
        self, auth: AuthContext, profile_id: str, data: RetrievalProfileActivate
    ) -> RetrievalProfile:
        require_roles(auth, "tenant_admin")
        try:
            profile = self.repository.activate_retrieval_profile(
                profile_id,
                auth.tenant_id,
                data.evaluation_run_id,
                auth.subject,
            )
        except ValueError as error:
            raise ApiError(409, "PROFILE_NOT_ACTIVATABLE", str(error)) from error
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        self._audit(auth, "retrieval_profile.activate", "retrieval_profile", profile_id)
        return profile

    def rollback_hash_retrieval_profile(
        self, auth: AuthContext, profile_id: str, reason: str
    ) -> RetrievalProfile:
        require_roles(auth, "tenant_admin")
        try:
            profile = self.repository.rollback_hash_retrieval_profile(
                profile_id, auth.tenant_id, auth.subject
            )
        except ValueError as error:
            raise ApiError(409, "HASH_ROLLBACK_NOT_AVAILABLE", str(error)) from error
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        self._audit(
            auth,
            "retrieval_profile.rollback_hash",
            "retrieval_profile",
            profile_id,
            {"reason": reason},
        )
        return profile

    def retrieval_profile_activation_readiness(self, auth: AuthContext, profile_id: str) -> dict:
        require_roles(auth, "tenant_admin")
        profile = self.repository.get_retrieval_profile(profile_id, auth.tenant_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Retrieval profile not found")
        failed: list[str] = []
        if profile.status not in {"ready", "active"}:
            failed.append("PROFILE_NOT_READY")
        if profile.indexed_chunks != profile.total_chunks:
            failed.append("INDEX_COVERAGE_INCOMPLETE")
        if profile.gate_version != "v4" or profile.retriever_version != "hybrid-rrf-v4":
            failed.append("VERSION_MISMATCH")
        if not profile.calibration_dataset or not profile.calibration_run_id:
            failed.append("CALIBRATION_MISSING")
        if len(profile.validation_datasets) != 2 or not profile.evaluation_run_id or not profile.holdout_run_id:
            failed.append("VALIDATION_MISSING")
        expected_hash = self._retrieval_profile_config_hash(profile)
        if not profile.config_hash or profile.config_hash != expected_hash:
            failed.append("CONFIG_HASH_MISMATCH")
        return {
            "activatable": not failed,
            "profile_status": profile.status,
            "required_runs": {
                "calibration": profile.calibration_run_id,
                "gold": profile.evaluation_run_id,
                "holdout": profile.holdout_run_id,
            },
            "failed_gates": failed,
            "config_hash_match": profile.config_hash == expected_hash,
            "index_coverage": profile.indexed_chunks / max(1, profile.total_chunks),
        }

    @staticmethod
    def _retrieval_profile_config_hash(profile: RetrievalProfile) -> str:
        payload = {
            "provider": profile.provider,
            "embedding_model": profile.embedding_model,
            "embedding_dimension": profile.embedding_dimension,
            "retriever_version": profile.retriever_version,
            "gate_version": profile.gate_version,
            "normalization_version": profile.normalization_version,
            "gate_config": profile.gate_config,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _policy_corpus_hash(self, tenant_id: str) -> str:
        payload = [
            {
                "policy_version_id": policy.policy_version_id,
                "policy_id": policy.policy_id,
                "scope": policy.scope,
                "version": policy.version,
                "content_hash": policy.content_hash,
                "acl": policy.acl,
            }
            for policy in self.repository.list_published_policies(tenant_id)
        ]
        payload.sort(key=lambda item: item["policy_version_id"])
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def list_llm_traces(self, auth: AuthContext):
        require_roles(auth, "tenant_admin")
        return self.repository.list_llm_traces(auth.tenant_id)

    def get_llm_trace(self, auth: AuthContext, trace_id: str):
        require_roles(auth, "tenant_admin")
        trace = self.repository.get_llm_trace(trace_id, auth.tenant_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="LLM trace not found")
        return trace

    def list_agent_runs(self, auth: AuthContext):
        require_roles(auth, "tenant_admin")
        return self.repository.list_agent_runs(auth.tenant_id)

    def get_agent_run(self, auth: AuthContext, run_id: str):
        require_roles(auth, "tenant_admin")
        run = self.repository.get_agent_run(run_id, auth.tenant_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run not found")
        return run

    def pilot_metrics(self, auth: AuthContext) -> dict:
        require_roles(auth, "tenant_admin")
        sql_aggregate = getattr(self.repository, "pilot_metrics", None)
        if callable(sql_aggregate):
            return sql_aggregate(auth.tenant_id)
        conversations = self.repository.list_conversations(auth.tenant_id)
        handoffs = self.repository.list_handoffs(auth.tenant_id)
        tickets = self.repository.list_tickets(auth.tenant_id)
        runs = self.repository.list_agent_runs(auth.tenant_id)
        handed_conversations = {item.conversation_id for item in handoffs}
        resolved = [
            item
            for item in conversations
            if item.status in {ConversationStatus.RESOLVED, ConversationStatus.CLOSED}
        ]
        self_resolved = [
            item for item in resolved if item.conversation_id not in handed_conversations
        ]
        first_response_seconds: list[float] = []
        resolution_seconds: list[float] = []
        for handoff in handoffs:
            first = handoff.context_snapshot.get("first_response_at")
            resolved_at = handoff.context_snapshot.get("resolved_at")
            if first:
                first_response_seconds.append(
                    max(0.0, (type(handoff.created_at).fromisoformat(first) - handoff.created_at).total_seconds())
                )
            if resolved_at:
                resolution_seconds.append(
                    max(
                        0.0,
                        (
                            type(handoff.created_at).fromisoformat(resolved_at)
                            - handoff.created_at
                        ).total_seconds(),
                    )
                )
        latencies = sorted(item.latency_ms for item in runs)

        def ratio(numerator: int, denominator: int) -> float:
            return numerator / denominator if denominator else 0.0

        def average(values: list[float]) -> float:
            return sum(values) / len(values) if values else 0.0

        p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else 0
        return {
            "conversation_count": len(conversations),
            "resolved_count": len(resolved),
            "self_service_resolution_rate": ratio(len(self_resolved), len(resolved)),
            "handoff_rate": ratio(len(handed_conversations), len(conversations)),
            "first_contact_resolution_rate": ratio(
                sum(item.status == "resolved" for item in handoffs), len(handoffs)
            ),
            "ticket_count": len(tickets),
            "open_ticket_count": sum(item.status == "open" for item in tickets),
            "average_first_response_seconds": average(first_response_seconds),
            "average_resolution_seconds": average(resolution_seconds),
            "agent_run_count": len(runs),
            "agent_p95_latency_ms": p95,
            "tool_failure_rate": ratio(sum(bool(item.error_code) for item in runs), len(runs)),
            "input_tokens": sum(item.input_tokens for item in runs),
            "output_tokens": sum(item.output_tokens for item in runs),
            "estimated_cost": sum(item.estimated_cost for item in runs),
            "unsafe_write_count": 0,
        }

    @staticmethod
    def _numeric_delta(left, right):
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return right - left
        return None

    def _event(self, conversation: Conversation, event_type: str, data: dict) -> None:
        self.repository.append_event(
            ConversationEvent(
                event_id=f"evt_{uuid4().hex}",
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.conversation_id,
                event_type=event_type,
                data=data,
            )
        )

    def _audit(
        self,
        auth: AuthContext,
        action: str,
        resource_type: str,
        resource_id: str,
        data: dict | None = None,
    ) -> None:
        self.repository.append_audit(
            AuditEvent(
                event_id=f"audit_{uuid4().hex}",
                tenant_id=auth.tenant_id,
                actor_id=auth.subject,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=auth.request_id,
                data=data or {},
            )
        )
