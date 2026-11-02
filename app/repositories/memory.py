from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from app.domain.models import (
    ActionExecution,
    ActionPlan,
    AgentRun,
    AuditEvent,
    CompensationProposal,
    Conversation,
    ConversationEvent,
    EvaluationCase,
    EvaluationDataset,
    EvaluationResult,
    EvaluationRun,
    Handoff,
    LlmTrace,
    Order,
    PilotFeedback,
    PilotMetricSnapshot,
    PilotParticipant,
    PilotRelease,
    PilotTaskAssignment,
    PolicyVersion,
    RetrievalProfile,
    RetrievalSnapshot,
    RuntimeTask,
    RuntimeTaskStatus,
    Ticket,
    utc_now,
)
from app.domain.transitions import LeaseLostError, ensure_task_transition
from app.services.policy_chunker import chunk_policy


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self.orders: dict[str, Order] = {}
        self.conversations: dict[str, Conversation] = {}
        self.handoffs: dict[str, Handoff] = {}
        self.action_plans: dict[str, ActionPlan] = {}
        self.compensation_proposals: dict[str, CompensationProposal] = {}
        self.executions_by_key: dict[str, ActionExecution] = {}
        self.tickets: dict[str, Ticket] = {}
        self.audit_events: list[AuditEvent] = []
        self.conversation_events: list[ConversationEvent] = []
        self.runtime_tasks: dict[str, RuntimeTask] = {}
        self.runtime_task_events: list[dict] = []
        self.runtime_workers: dict[str, dict] = {}
        self.policies: dict[str, PolicyVersion] = {}
        self.policy_chunks: dict[str, list[dict]] = {}
        self.policy_chunk_embeddings: dict[tuple[str, str], dict] = {}
        self.retrieval_profiles: dict[str, RetrievalProfile] = {}
        self.llm_traces: dict[str, LlmTrace] = {}
        self.agent_runs: dict[str, AgentRun] = {}
        self.pilot_releases: dict[str, PilotRelease] = {}
        self.pilot_participants: dict[str, PilotParticipant] = {}
        self.pilot_tasks: dict[str, PilotTaskAssignment] = {}
        self.pilot_feedback: dict[str, PilotFeedback] = {}
        self.pilot_metric_snapshots: dict[str, PilotMetricSnapshot] = {}
        self.retrieval_snapshots: dict[str, RetrievalSnapshot] = {}
        self.evaluation_cases: dict[str, EvaluationCase] = {}
        self.evaluation_datasets: dict[tuple[str, str, str], EvaluationDataset] = {}
        self.evaluation_results: dict[str, EvaluationResult] = {}
        self.evaluation_runs: dict[str, EvaluationRun] = {}
        self.members: dict[tuple[str, str], str] = {}
        self.channel_identities: dict[tuple[str, str, str], str] = {}
        self.queue_members: set[tuple[str, str, str]] = set()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.orders.clear()
            self.conversations.clear()
            self.handoffs.clear()
            self.action_plans.clear()
            self.compensation_proposals.clear()
            self.executions_by_key.clear()
            self.tickets.clear()
            self.audit_events.clear()
            self.conversation_events.clear()
            self.runtime_tasks.clear()
            self.runtime_task_events.clear()
            self.runtime_workers.clear()
            self.policies.clear()
            self.policy_chunks.clear()
            self.policy_chunk_embeddings.clear()
            self.retrieval_profiles.clear()
            self.llm_traces.clear()
            self.agent_runs.clear()
            self.pilot_releases.clear()
            self.pilot_participants.clear()
            self.pilot_tasks.clear()
            self.pilot_feedback.clear()
            self.pilot_metric_snapshots.clear()
            self.retrieval_snapshots.clear()
            self.evaluation_cases.clear()
            self.evaluation_datasets.clear()
            self.evaluation_results.clear()
            self.evaluation_runs.clear()
            self.members.clear()
            self.channel_identities.clear()
            self.queue_members.clear()
            self.members[("tenant_demo", "customer_001")] = "customer"
            self.members[("tenant_demo", "agent_001")] = "agent"
            self.members[("tenant_demo", "admin_001")] = "tenant_admin"
            self.members[
                ("tenant_demo", "11111111-1111-4111-8111-111111111111")
            ] = "customer"
            self.members[("tenant_demo", "22222222-2222-4222-8222-222222222222")] = "agent"
            self.members[
                ("tenant_demo", "33333333-3333-4333-8333-333333333333")
            ] = "tenant_admin"
            self.members[("tenant_platform", "platform_admin_001")] = "platform_admin"
            self.channel_identities[("tenant_demo", "oidc", "customer_001")] = "customer_001"
            self.channel_identities[
                ("tenant_demo", "oidc", "11111111-1111-4111-8111-111111111111")
            ] = "customer_001"
            self.queue_members.add(("tenant_demo", "queue_general", "agent_001"))
            self.queue_members.add(
                ("tenant_demo", "queue_general", "22222222-2222-4222-8222-222222222222")
            )
            order = Order(
                order_id="order_1001",
                tenant_id="tenant_demo",
                customer_id="customer_001",
                status="shipped",
                total_amount=128.0,
                shipment_status="in_transit",
                tracking_number="TRACK-1001",
            )
            self.orders[order.order_id] = order
            self.retrieval_profiles["hash-v1"] = RetrievalProfile(
                retrieval_profile_id="hash-v1",
                tenant_id="tenant_demo",
                provider="hash",
                embedding_model="hash-embedding-v1",
                embedding_dimension=1536,
                status="active",
            )

    def member_role(self, tenant_id: str, subject: str) -> str | None:
        return self.members.get((tenant_id, subject))

    def resolve_channel_customer(
        self, tenant_id: str, channel: str, channel_user_id: str
    ) -> str | None:
        return self.channel_identities.get((tenant_id, channel, channel_user_id))

    def list_agent_queue_ids(self, tenant_id: str, subject: str) -> set[str]:
        return {
            queue_id
            for member_tenant, queue_id, member_subject in self.queue_members
            if member_tenant == tenant_id and member_subject == subject
        }

    def save_conversation(self, conversation: Conversation) -> Conversation:
        with self._lock:
            self.conversations[conversation.conversation_id] = conversation
            return conversation

    def get_conversation(self, conversation_id: str, tenant_id: str) -> Conversation | None:
        conversation = self.conversations.get(conversation_id)
        return conversation if conversation and conversation.tenant_id == tenant_id else None

    def list_conversations(
        self, tenant_id: str, customer_id: str | None = None
    ) -> list[Conversation]:
        values = [item for item in self.conversations.values() if item.tenant_id == tenant_id]
        if customer_id:
            values = [item for item in values if item.customer_id == customer_id]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    def get_order(self, order_id: str, tenant_id: str, customer_id: str) -> Order | None:
        order = self.orders.get(order_id)
        if not order or order.tenant_id != tenant_id or order.customer_id != customer_id:
            return None
        return order

    def save_handoff(self, handoff: Handoff) -> Handoff:
        with self._lock:
            for item in self.handoffs.values():
                if (
                    item.tenant_id == handoff.tenant_id
                    and item.conversation_id == handoff.conversation_id
                    and item.status in {"queued", "claimed"}
                    and item.handoff_id != handoff.handoff_id
                ):
                    return item
            self.handoffs[handoff.handoff_id] = handoff
            return handoff

    def get_handoff(self, handoff_id: str, tenant_id: str) -> Handoff | None:
        handoff = self.handoffs.get(handoff_id)
        return handoff if handoff and handoff.tenant_id == tenant_id else None

    def claim_handoff_atomic(
        self,
        handoff_id: str,
        tenant_id: str,
        agent_id: str,
        allowed_queue_ids: set[str] | None,
    ) -> Handoff | None:
        with self._lock:
            handoff = self.get_handoff(handoff_id, tenant_id)
            if not handoff:
                return None
            if allowed_queue_ids is not None and handoff.queue_id not in allowed_queue_ids:
                return None
            if handoff.status == "claimed":
                return handoff if handoff.assigned_agent_id == agent_id else None
            if handoff.status != "queued":
                return None
            handoff.status = "claimed"
            handoff.assigned_agent_id = agent_id
            handoff.context_snapshot["claimed_at"] = utc_now().isoformat()
            return handoff

    def list_handoffs(
        self,
        tenant_id: str,
        status: str | None = None,
        allowed_queue_ids: set[str] | None = None,
    ) -> list[Handoff]:
        values = [item for item in self.handoffs.values() if item.tenant_id == tenant_id]
        if allowed_queue_ids is not None:
            values = [item for item in values if item.queue_id in allowed_queue_ids]
        if status:
            values = [item for item in values if item.status == status]
        return sorted(values, key=lambda item: (-item.priority, item.created_at))

    def save_action_plan(self, plan: ActionPlan) -> ActionPlan:
        with self._lock:
            self.action_plans[plan.action_plan_id] = plan
            return plan

    def get_action_plan(self, action_plan_id: str, tenant_id: str) -> ActionPlan | None:
        plan = self.action_plans.get(action_plan_id)
        return plan if plan and plan.tenant_id == tenant_id else None

    def save_compensation_proposal(
        self, proposal: CompensationProposal
    ) -> CompensationProposal:
        with self._lock:
            self.compensation_proposals[proposal.proposal_id] = proposal
            return proposal

    def get_compensation_proposal(
        self, proposal_id: str, tenant_id: str
    ) -> CompensationProposal | None:
        value = self.compensation_proposals.get(proposal_id)
        return value if value and value.tenant_id == tenant_id else None

    def execution_for_key(self, tenant_id: str, key: str) -> ActionExecution | None:
        execution = self.executions_by_key.get(f"{tenant_id}:{key}")
        return execution

    def save_execution(self, execution: ActionExecution) -> ActionExecution:
        with self._lock:
            self.executions_by_key[f"{execution.tenant_id}:{execution.idempotency_key}"] = execution
            return execution

    def get_execution(self, execution_id: str, tenant_id: str) -> ActionExecution | None:
        for execution in self.executions_by_key.values():
            if execution.execution_id == execution_id and execution.tenant_id == tenant_id:
                return execution
        return None

    def confirm_action_atomic(
        self, plan: ActionPlan, task: RuntimeTask, execution: ActionExecution
    ) -> ActionExecution:
        with self._lock:
            key = f"{plan.tenant_id}:{plan.idempotency_key}"
            existing = self.executions_by_key.get(key)
            if existing:
                return existing
            self.action_plans[plan.action_plan_id] = plan
            self.executions_by_key[key] = execution
            self.runtime_tasks[task.task_id] = task
            self._task_event(task, "queued", {"action_plan_id": plan.action_plan_id})
            return execution

    def save_ticket(self, ticket: Ticket) -> Ticket:
        with self._lock:
            self.tickets[ticket.ticket_id] = ticket
            return ticket

    def get_ticket(self, ticket_id: str, tenant_id: str, customer_id: str | None) -> Ticket | None:
        ticket = self.tickets.get(ticket_id)
        if not ticket or ticket.tenant_id != tenant_id:
            return None
        if customer_id and ticket.customer_id != customer_id:
            return None
        return ticket

    def list_tickets(self, tenant_id: str, customer_id: str | None = None) -> list[Ticket]:
        values = [item for item in self.tickets.values() if item.tenant_id == tenant_id]
        if customer_id:
            values = [item for item in values if item.customer_id == customer_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def append_audit(self, event: AuditEvent) -> None:
        with self._lock:
            self.audit_events.append(event)

    def append_event(self, event: ConversationEvent) -> None:
        with self._lock:
            self.conversation_events.append(event)

    def list_events(
        self, conversation_id: str, tenant_id: str, after: datetime | None = None
    ) -> list[ConversationEvent]:
        return [
            item
            for item in self.conversation_events
            if item.tenant_id == tenant_id
            and item.conversation_id == conversation_id
            and (after is None or item.created_at > after)
        ]

    def enqueue_task(self, task: RuntimeTask) -> RuntimeTask:
        with self._lock:
            if task.task_id not in self.runtime_tasks:
                self.runtime_tasks[task.task_id] = task
                self._task_event(task, "queued", {})
            return self.runtime_tasks[task.task_id]

    def enqueue_message_atomic(
        self, conversation: Conversation, task: RuntimeTask, event: ConversationEvent
    ) -> RuntimeTask:
        with self._lock:
            self.conversations[conversation.conversation_id] = conversation
            self.runtime_tasks[task.task_id] = task
            self.conversation_events.append(event)
            self._task_event(task, "queued", {"conversation_id": task.conversation_id})
            return task

    def claim_task(self, worker_id: str, lease_seconds: int) -> RuntimeTask | None:
        with self._lock:
            now = datetime.now(UTC)
            candidates = [
                task
                for task in self.runtime_tasks.values()
                if task.available_at <= now
                and (
                    task.status in {RuntimeTaskStatus.QUEUED, RuntimeTaskStatus.RETRY_WAIT}
                    or (
                        task.status == RuntimeTaskStatus.RUNNING
                        and task.lease_expires_at
                        and task.lease_expires_at <= now
                    )
                )
            ]
            if not candidates:
                return None
            task = min(candidates, key=lambda item: item.created_at)
            recovered = task.status == RuntimeTaskStatus.RUNNING
            if not recovered:
                ensure_task_transition(task.status, RuntimeTaskStatus.RUNNING)
            task.status = RuntimeTaskStatus.RUNNING
            task.lease_owner = worker_id
            task.lease_expires_at = now + timedelta(seconds=lease_seconds)
            task.attempts += 1
            task.updated_at = now
            self._task_event(
                task, "recovered" if recovered else "claimed", {"worker_id": worker_id}
            )
            return task.model_copy(deep=True)

    def heartbeat_task(self, task_id: str, worker_id: str, lease_seconds: int) -> bool:
        with self._lock:
            task = self.runtime_tasks.get(task_id)
            if (
                not task
                or task.status != RuntimeTaskStatus.RUNNING
                or task.lease_owner != worker_id
            ):
                return False
            task.lease_expires_at = utc_now() + timedelta(seconds=lease_seconds)
            task.updated_at = utc_now()
            return True

    def complete_task(self, task: RuntimeTask, worker_id: str) -> RuntimeTask:
        with self._lock:
            stored = self.runtime_tasks[task.task_id]
            if stored.status != RuntimeTaskStatus.RUNNING or stored.lease_owner != worker_id:
                raise LeaseLostError(f"Worker {worker_id} no longer owns task {task.task_id}")
            ensure_task_transition(stored.status, RuntimeTaskStatus.COMPLETED)
            stored.status = RuntimeTaskStatus.COMPLETED
            stored.lease_owner = None
            stored.lease_expires_at = None
            stored.updated_at = utc_now()
            self._task_event(stored, "completed", {"worker_id": worker_id})
            return stored

    def fail_task(self, task: RuntimeTask, error: str, worker_id: str) -> RuntimeTask:
        with self._lock:
            stored = self.runtime_tasks[task.task_id]
            if stored.status != RuntimeTaskStatus.RUNNING or stored.lease_owner != worker_id:
                raise LeaseLostError(f"Worker {worker_id} no longer owns task {task.task_id}")
            stored.last_error = error[:1000]
            stored.lease_owner = None
            stored.lease_expires_at = None
            if stored.attempts >= stored.max_attempts:
                target = RuntimeTaskStatus.DEAD_LETTER
                stored.dead_lettered_at = utc_now()
            else:
                target = RuntimeTaskStatus.RETRY_WAIT
                stored.available_at = utc_now() + timedelta(seconds=min(2**stored.attempts, 30))
            ensure_task_transition(stored.status, target)
            stored.status = target
            stored.updated_at = utc_now()
            self._task_event(
                stored, target.value, {"worker_id": worker_id, "error": stored.last_error}
            )
            return stored

    def get_task(self, task_id: str, tenant_id: str) -> RuntimeTask | None:
        task = self.runtime_tasks.get(task_id)
        return task if task and task.tenant_id == tenant_id else None

    def cancel_task(self, task_id: str, tenant_id: str) -> RuntimeTask | None:
        with self._lock:
            task = self.get_task(task_id, tenant_id)
            if task and task.status in {
                RuntimeTaskStatus.QUEUED,
                RuntimeTaskStatus.RETRY_WAIT,
            }:
                ensure_task_transition(task.status, RuntimeTaskStatus.CANCELLED)
                task.status = RuntimeTaskStatus.CANCELLED
                task.cancel_requested_at = utc_now()
                task.updated_at = utc_now()
                self._task_event(task, "cancelled", {})
            return task

    def list_tasks(
        self, tenant_id: str, status: str | None = None, task_type: str | None = None
    ) -> list[RuntimeTask]:
        items = [task for task in self.runtime_tasks.values() if task.tenant_id == tenant_id]
        if status:
            items = [task for task in items if task.status.value == status]
        if task_type:
            items = [task for task in items if task.task_type == task_type]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    def list_task_events(self, task_id: str, tenant_id: str) -> list[dict]:
        return [
            event
            for event in self.runtime_task_events
            if event["task_id"] == task_id and event["tenant_id"] == tenant_id
        ]

    def replay_dead_letter(
        self, task_id: str, tenant_id: str, actor_id: str, reason: str
    ) -> RuntimeTask:
        with self._lock:
            original = self.get_task(task_id, tenant_id)
            if original is None:
                raise LookupError(task_id)
            if original.status != RuntimeTaskStatus.DEAD_LETTER:
                raise ValueError("TASK_NOT_REPLAYABLE")
            existing = next(
                (task for task in self.runtime_tasks.values() if task.replay_of_task_id == task_id),
                None,
            )
            if existing:
                return existing
            replay = RuntimeTask(
                task_id=f"task_{uuid4().hex}",
                tenant_id=original.tenant_id,
                conversation_id=original.conversation_id,
                task_type=original.task_type,
                payload=original.payload.copy(),
                max_attempts=original.max_attempts,
                replay_of_task_id=original.task_id,
                replayed_by=actor_id,
                replay_reason=reason,
            )
            self.runtime_tasks[replay.task_id] = replay
            self._task_event(
                original, "replayed", {"new_task_id": replay.task_id, "actor_id": actor_id}
            )
            self._task_event(replay, "queued", {"replay_of_task_id": original.task_id})
            return replay

    def heartbeat_worker(
        self,
        worker_id: str,
        hostname: str,
        process_id: int,
        current_task_id: str | None,
        metadata: dict,
    ) -> None:
        now = utc_now()
        stored = self.runtime_workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "started_at": now,
                "hostname": hostname,
                "process_id": process_id,
            },
        )
        stored.update(
            {"last_heartbeat_at": now, "current_task_id": current_task_id, "metadata": metadata}
        )

    def list_workers(self) -> list[dict]:
        return sorted(
            self.runtime_workers.values(), key=lambda item: item["last_heartbeat_at"], reverse=True
        )

    def list_reconciliations(self, tenant_id: str) -> list[ActionExecution]:
        return [
            item
            for item in self.executions_by_key.values()
            if item.tenant_id == tenant_id and item.status.value == "unknown_needs_reconciliation"
        ]

    def _task_event(self, task: RuntimeTask, event_type: str, data: dict) -> None:
        self.runtime_task_events.append(
            {
                "event_id": f"rte_{uuid4().hex}",
                "tenant_id": task.tenant_id,
                "task_id": task.task_id,
                "event_type": event_type,
                "data": data,
                "created_at": utc_now(),
            }
        )

    def save_policy(self, policy: PolicyVersion) -> PolicyVersion:
        with self._lock:
            if not policy.policy_id:
                family = hashlib.sha256(
                    f"{policy.tenant_id}:{policy.scope}".encode()
                ).hexdigest()[:32]
                policy.policy_id = f"policy_family_{family}"
            if any(
                item.tenant_id == policy.tenant_id
                and item.scope == policy.scope
                and item.version == policy.version
                and item.policy_version_id != policy.policy_version_id
                for item in self.policies.values()
            ):
                raise ValueError("Policy version already exists")
            self.policies[policy.policy_version_id] = policy
            return policy

    def get_policy(self, policy_id: str, tenant_id: str) -> PolicyVersion | None:
        policy = self.policies.get(policy_id)
        return policy if policy and policy.tenant_id == tenant_id else None

    def list_policy_versions(
        self, tenant_id: str, scope: str | None = None, status: str | None = None
    ) -> list[PolicyVersion]:
        values = [item for item in self.policies.values() if item.tenant_id == tenant_id]
        if scope:
            values = [item for item in values if item.scope == scope]
        if status:
            values = [item for item in values if item.status == status]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def update_policy_draft(self, policy: PolicyVersion) -> PolicyVersion:
        current = self.get_policy(policy.policy_version_id, policy.tenant_id)
        if current is None:
            raise LookupError("Policy not found")
        if current.status != "draft":
            raise ValueError("Published policy is immutable")
        return self.save_policy(policy)

    def publish_policy(
        self, policy_id: str, tenant_id: str, actor_id: str | None = None
    ) -> PolicyVersion | None:
        with self._lock:
            policy = self.policies.get(policy_id)
            if not policy or policy.tenant_id != tenant_id:
                return None
            if policy.status not in {"draft", "published"}:
                raise ValueError("Policy cannot be published from current state")
            policy.status = "indexing"
            policy.index_status = "indexing"
            chunks = chunk_policy(policy.content)
            if not chunks:
                policy.status = "draft"
                policy.index_status = "failed"
                policy.index_error = "Policy produced no chunks"
                raise ValueError(policy.index_error)
            self.policy_chunks[policy_id] = [
                {
                    "chunk_id": f"{policy_id}:chunk:{chunk.ordinal}",
                    "ordinal": chunk.ordinal,
                    "content": chunk.content,
                    "title_path": chunk.title_path,
                    "content_hash": chunk.content_hash,
                    "embedding_model": "hash-embedding-v1",
                    "embedding_dimension": 1536,
                }
                for chunk in chunks
            ]
            for item in self.policies.values():
                if (
                    item.tenant_id == tenant_id
                    and item.scope == policy.scope
                    and item.status == "published"
                ):
                    item.status = "superseded"
            policy.status = "published"
            policy.index_status = "ready"
            policy.index_error = None
            policy.published_by = actor_id
            policy.published_at = utc_now()
            policy.updated_at = utc_now()
            return policy

    def reindex_policy(self, policy_id: str, tenant_id: str) -> PolicyVersion | None:
        with self._lock:
            policy = self.get_policy(policy_id, tenant_id)
            if policy is None:
                return None
            if policy.status not in {"published", "superseded"}:
                raise ValueError("Only indexed policy versions can be reindexed")
            chunks = chunk_policy(policy.content)
            self.policy_chunks[policy_id] = [
                {
                    "chunk_id": f"{policy_id}:chunk:{chunk.ordinal}",
                    "ordinal": chunk.ordinal,
                    "content": chunk.content,
                    "title_path": chunk.title_path,
                    "content_hash": chunk.content_hash,
                    "embedding_model": "hash-embedding-v1",
                    "embedding_dimension": 1536,
                }
                for chunk in chunks
            ]
            policy.index_status = "ready"
            policy.index_error = None
            policy.updated_at = utc_now()
            return policy

    def list_published_policies(self, tenant_id: str) -> list[PolicyVersion]:
        now = utc_now()
        return [
            item
            for item in self.policies.values()
            if item.tenant_id == tenant_id
            and item.status == "published"
            and (item.effective_from is None or item.effective_from <= now)
            and (item.effective_to is None or item.effective_to > now)
        ]

    def search_policy_chunks(
        self,
        tenant_id: str,
        query: str,
        embedding: list[float],
        limit: int,
        filters: dict | None = None,
        embedding_model: str = "hash-embedding-v1",
        embedding_dimension: int = 1536,
        retrieval_profile_id: str | None = None,
    ) -> list[dict]:
        del embedding
        filters = filters or {}
        query_value = query.lower()
        query_tokens = set(query_value)
        query_bigrams = {
            query_value[index : index + 2]
            for index in range(max(0, len(query_value) - 1))
            if " " not in query_value[index : index + 2]
        }
        candidates = []
        for policy in self.list_published_policies(tenant_id):
            if any(
                policy.acl.get(key)
                and (
                    not filters.get(key)
                    or str(filters[key]) not in {str(value) for value in policy.acl[key]}
                )
                for key in ("product_id", "category", "region", "channel", "customer_tier")
            ):
                continue
            for chunk in self.policy_chunks.get(policy.policy_version_id, []):
                profile_embedding = (
                    self.policy_chunk_embeddings.get((chunk["chunk_id"], retrieval_profile_id))
                    if retrieval_profile_id
                    else None
                )
                if retrieval_profile_id and not profile_embedding:
                    continue
                if (
                    (profile_embedding or chunk).get("embedding_model", "hash-embedding-v1")
                    != embedding_model
                    or (profile_embedding or chunk).get("embedding_dimension", 1536)
                    != embedding_dimension
                ):
                    continue
                content_value = (
                    f"{policy.title}{policy.scope}{''.join(chunk.get('title_path', []))}"
                    f"{chunk['content']}"
                ).lower()
                content_tokens = set(content_value)
                content_bigrams = {
                    content_value[index : index + 2]
                    for index in range(max(0, len(content_value) - 1))
                    if " " not in content_value[index : index + 2]
                }
                character_overlap = query_tokens & content_tokens
                bigram_overlap = query_bigrams & content_bigrams
                lexical_score = float(len(bigram_overlap))
                semantic_score = len(character_overlap) / max(len(query_tokens), 1)
                if lexical_score <= 0 and semantic_score < 0.2:
                    continue
                candidates.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "tenant_id": tenant_id,
                        "policy_id": policy.policy_id,
                        "policy_version_id": policy.policy_version_id,
                        "policy_status": policy.status,
                        "title": policy.title,
                        "version": policy.version,
                        "scope": policy.scope,
                        "title_path": chunk["title_path"],
                        "ordinal": chunk.get("ordinal", 0),
                        "content_hash": chunk.get("content_hash", ""),
                        "acl": policy.acl,
                        "excerpt": chunk["content"][:400],
                        "lexical_score": lexical_score,
                        "semantic_score": semantic_score,
                        "matched_entities": sorted(bigram_overlap)[:20],
                        "effective_from": policy.effective_from,
                        "effective_to": policy.effective_to,
                    }
                )
        lexical = sorted(
            (item for item in candidates if item["lexical_score"] > 0),
            key=lambda item: (-item["lexical_score"], item["chunk_id"]),
        )[:30]
        semantic = sorted(
            (item for item in candidates if item["semantic_score"] > 0),
            key=lambda item: (-item["semantic_score"], item["chunk_id"]),
        )[:30]
        lexical_ranks = {item["chunk_id"]: index + 1 for index, item in enumerate(lexical)}
        semantic_ranks = {item["chunk_id"]: index + 1 for index, item in enumerate(semantic)}
        results = []
        for item in candidates:
            lexical_rank = lexical_ranks.get(item["chunk_id"])
            semantic_rank = semantic_ranks.get(item["chunk_id"])
            if lexical_rank is None and semantic_rank is None:
                continue
            item["lexical_rank"] = lexical_rank
            item["semantic_rank"] = semantic_rank
            item["rrf_score"] = (1 / (60 + lexical_rank) if lexical_rank else 0) + (
                1 / (60 + semantic_rank) if semantic_rank else 0
            )
            item["score"] = item["rrf_score"]
            results.append(item)
        return sorted(
            results, key=lambda item: (-item["rrf_score"], item["chunk_id"])
        )[:limit]

    def count_incompatible_policy_chunks(
        self,
        tenant_id: str,
        embedding_model: str,
        embedding_dimension: int,
        retrieval_profile_id: str | None = None,
    ) -> int:
        return sum(
            1
            for policy in self.list_published_policies(tenant_id)
            for chunk in self.policy_chunks.get(policy.policy_version_id, [])
            if (
                retrieval_profile_id
                and not self.policy_chunk_embeddings.get((chunk["chunk_id"], retrieval_profile_id))
            )
            or (self.policy_chunk_embeddings.get((chunk["chunk_id"], retrieval_profile_id)) or chunk).get(
                "embedding_model", "hash-embedding-v1"
            )
            != embedding_model
            or (self.policy_chunk_embeddings.get((chunk["chunk_id"], retrieval_profile_id)) or chunk).get(
                "embedding_dimension", 1536
            )
            != embedding_dimension
        )

    def save_retrieval_snapshot(self, snapshot: RetrievalSnapshot) -> RetrievalSnapshot:
        self.retrieval_snapshots[snapshot.retrieval_snapshot_id] = snapshot
        return snapshot

    def get_retrieval_snapshot(
        self, snapshot_id: str, tenant_id: str
    ) -> RetrievalSnapshot | None:
        value = self.retrieval_snapshots.get(snapshot_id)
        return value if value and value.tenant_id == tenant_id else None

    def import_evaluation_cases(self, cases: list[EvaluationCase]) -> int:
        if not cases:
            return 0
        key = (cases[0].tenant_id, cases[0].dataset, cases[0].dataset_version)
        dataset = self.evaluation_datasets.get(key)
        if dataset and dataset.status == "frozen":
            raise ValueError("DATASET_FROZEN")
        if dataset is None:
            dataset = EvaluationDataset(
                tenant_id=key[0], dataset=key[1], version=key[2]
            )
            self.evaluation_datasets[key] = dataset
        for case in cases:
            existing = next(
                (
                    value
                    for value in self.evaluation_cases.values()
                    if value.tenant_id == case.tenant_id
                    and value.dataset == case.dataset
                    and value.dataset_version == case.dataset_version
                    and value.name == case.name
                ),
                None,
            )
            if existing:
                case.evaluation_case_id = existing.evaluation_case_id
            self.evaluation_cases[case.evaluation_case_id] = case
        values = self.list_evaluation_cases(*key)
        dataset.case_count = len(values)
        dataset.content_hash = hashlib.sha256(
            json.dumps(
                [item.model_dump(mode="json") for item in values],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return len(cases)

    def get_evaluation_dataset(
        self, tenant_id: str, dataset: str, version: str
    ) -> EvaluationDataset | None:
        return self.evaluation_datasets.get((tenant_id, dataset, version))

    def list_evaluation_datasets(self, tenant_id: str) -> list[EvaluationDataset]:
        return sorted(
            [value for value in self.evaluation_datasets.values() if value.tenant_id == tenant_id],
            key=lambda value: value.created_at,
            reverse=True,
        )

    def freeze_evaluation_dataset(
        self, tenant_id: str, dataset: str, version: str
    ) -> EvaluationDataset | None:
        value = self.get_evaluation_dataset(tenant_id, dataset, version)
        if value:
            value.status = "frozen"
            value.frozen_at = utc_now()
        return value

    def list_evaluation_cases(
        self, tenant_id: str, dataset: str, dataset_version: str
    ) -> list[EvaluationCase]:
        return [
            item
            for item in self.evaluation_cases.values()
            if item.tenant_id == tenant_id
            and item.dataset == dataset
            and item.dataset_version == dataset_version
            and item.active
        ]

    def save_evaluation_run(self, run: EvaluationRun) -> EvaluationRun:
        with self._lock:
            self.evaluation_runs[run.evaluation_run_id] = run
            return run

    def get_evaluation_run(self, run_id: str, tenant_id: str) -> EvaluationRun | None:
        run = self.evaluation_runs.get(run_id)
        return run if run and run.tenant_id == tenant_id else None

    def list_evaluation_runs(self, tenant_id: str) -> list[EvaluationRun]:
        return sorted(
            [item for item in self.evaluation_runs.values() if item.tenant_id == tenant_id],
            key=lambda item: item.created_at,
            reverse=True,
        )

    def save_evaluation_results(self, results: list[EvaluationResult]) -> None:
        for result in results:
            self.evaluation_results[result.evaluation_result_id] = result

    def list_evaluation_results(
        self, run_id: str, tenant_id: str
    ) -> list[EvaluationResult]:
        return [
            item
            for item in self.evaluation_results.values()
            if item.run_id == run_id and item.tenant_id == tenant_id
        ]

    def save_retrieval_profile(self, profile: RetrievalProfile) -> RetrievalProfile:
        with self._lock:
            current = self.retrieval_profiles.get(profile.retrieval_profile_id)
            if current and current.tenant_id != profile.tenant_id:
                raise ValueError("Retrieval profile ID belongs to another tenant")
            self.retrieval_profiles[profile.retrieval_profile_id] = profile
            return profile

    def get_retrieval_profile(
        self, profile_id: str, tenant_id: str
    ) -> RetrievalProfile | None:
        value = self.retrieval_profiles.get(profile_id)
        return value if value and value.tenant_id == tenant_id else None

    def list_retrieval_profiles(self, tenant_id: str) -> list[RetrievalProfile]:
        return sorted(
            [value for value in self.retrieval_profiles.values() if value.tenant_id == tenant_id],
            key=lambda value: value.created_at,
        )

    def reindex_retrieval_profile(
        self, profile_id: str, tenant_id: str
    ) -> RetrievalProfile | None:
        profile = self.get_retrieval_profile(profile_id, tenant_id)
        if profile is None:
            return None
        if profile.status == "rejected":
            raise ValueError("Rejected retrieval profiles are immutable")
        profile.status = "building"
        chunks = [
            chunk
            for policy in self.list_published_policies(tenant_id)
            for chunk in self.policy_chunks.get(policy.policy_version_id, [])
        ]
        profile.total_chunks = len(chunks)
        for chunk in chunks:
            self.policy_chunk_embeddings[(chunk["chunk_id"], profile_id)] = {
                "tenant_id": tenant_id,
                "chunk_id": chunk["chunk_id"],
                "retrieval_profile_id": profile_id,
                "embedding_model": profile.embedding_model,
                "embedding_dimension": profile.embedding_dimension,
                "content_hash": chunk.get("content_hash", ""),
                "status": "ready",
            }
        profile.indexed_chunks = len(chunks)
        profile.status = "ready"
        profile.updated_at = utc_now()
        return profile

    def activate_retrieval_profile(
        self, profile_id: str, tenant_id: str, evaluation_run_id: str, actor_id: str
    ) -> RetrievalProfile | None:
        profile = self.get_retrieval_profile(profile_id, tenant_id)
        run = self.get_evaluation_run(evaluation_run_id, tenant_id)
        if profile is None:
            return None
        if profile.retrieval_profile_id in {"bge-m3-v1", "bge-m3-v2"} or profile.status == "rejected":
            raise ValueError("Rejected retrieval profiles are immutable and cannot be activated")
        if profile.status not in {"ready", "active"} or profile.indexed_chunks != profile.total_chunks:
            raise ValueError("Retrieval profile is not fully indexed")
        expected_gold = (
            profile.validation_datasets[0]
            if profile.validation_datasets
            else "week4_gold/v3"
        )
        holdout = (
            self.get_evaluation_run(profile.holdout_run_id, tenant_id)
            if profile.holdout_run_id
            else None
        )
        if (
            run is None
            or run.quality_status != "passed"
            or f"{run.dataset}/{run.dataset_version}" != expected_gold
            or run.metric_schema_version != "v3"
            or run.config_snapshot.get("retrieval_profile_id") != profile_id
        ):
            raise ValueError("A passed week4_gold/v3 run for this profile is required")
        if (
            profile.gate_version == "v3"
            and (
                holdout is None
                or holdout.quality_status != "passed"
                or holdout.dataset != "week4_hard_negative"
                or holdout.dataset_version != "v1"
                or holdout.config_snapshot.get("retrieval_profile_id") != profile_id
                or not profile.calibration_run_id
                or profile.normalization_version != "query-normalizer-v3"
                or profile.retriever_version != "hybrid-rrf-v3"
                or profile.gate_config.get("gate_version") != "v3"
                or any(
                    float((run.summary if value == "gold" else holdout.summary).get(metric, 0)) != 0
                    for value in ("gold", "holdout")
                    for metric in (
                        "false_answer_rate",
                        "old_version_hit_rate",
                        "tenant_leakage_rate",
                        "acl_leakage_rate",
                    )
                )
            )
        ):
            raise ValueError("Passed calibration and hard-negative holdout runs are required")
        if profile.gate_version == "v4" and (
            (calibration := self.get_evaluation_run(profile.calibration_run_id, tenant_id)) is None
            or calibration.quality_status != "passed"
            or f"{calibration.dataset}/{calibration.dataset_version}"
            != profile.calibration_dataset
            or calibration.config_snapshot.get("retrieval_profile_id") != profile_id
            or holdout is None
            or holdout.quality_status != "passed"
            or len(profile.validation_datasets) != 2
            or f"{run.dataset}/{run.dataset_version}" != profile.validation_datasets[0]
            or f"{holdout.dataset}/{holdout.dataset_version}" != profile.validation_datasets[1]
            or not profile.calibration_dataset
            or not profile.config_hash
            or run.config_snapshot.get("config_hash") != profile.config_hash
            or holdout.config_snapshot.get("config_hash") != profile.config_hash
            or profile.normalization_version != "query-normalizer-v4"
            or profile.retriever_version != "hybrid-rrf-v4"
            or profile.gate_config.get("gate_version") != "v4"
            or run.config_snapshot.get("policy_corpus_hash")
            != holdout.config_snapshot.get("policy_corpus_hash")
            or run.config_snapshot.get("policy_corpus_hash")
            != calibration.config_snapshot.get("policy_corpus_hash")
        ):
            raise ValueError("Gate v4 requires matching calibration, gold and hard-negative v2 runs")
        for value in self.list_retrieval_profiles(tenant_id):
            if value.status == "active" and value.retrieval_profile_id != profile_id:
                value.status = "ready"
        profile.status = "active"
        profile.evaluation_run_id = evaluation_run_id
        profile.activated_by = actor_id
        profile.activated_at = utc_now()
        profile.updated_at = utc_now()
        return profile

    def rollback_hash_retrieval_profile(
        self, profile_id: str, tenant_id: str, actor_id: str
    ) -> RetrievalProfile | None:
        profile = self.get_retrieval_profile(profile_id, tenant_id)
        if profile is None:
            return None
        if (
            profile_id != f"hash-v1:{tenant_id}"
            or profile.provider != "hash"
            or profile.status != "ready"
            or profile.total_chunks <= 0
            or profile.indexed_chunks != profile.total_chunks
        ):
            raise ValueError("Only the fully indexed immutable Hash baseline can be restored")
        for value in self.list_retrieval_profiles(tenant_id):
            if value.status == "active" and value.retrieval_profile_id != profile_id:
                value.status = "ready"
                value.updated_at = utc_now()
        profile.status = "active"
        profile.activated_by = actor_id
        profile.activated_at = utc_now()
        profile.updated_at = utc_now()
        return profile

    def save_llm_trace(self, trace: LlmTrace) -> LlmTrace:
        self.llm_traces[trace.llm_trace_id] = trace
        return trace

    def get_llm_trace(self, trace_id: str, tenant_id: str) -> LlmTrace | None:
        value = self.llm_traces.get(trace_id)
        return value if value and value.tenant_id == tenant_id else None

    def list_llm_traces(self, tenant_id: str) -> list[LlmTrace]:
        return sorted(
            [value for value in self.llm_traces.values() if value.tenant_id == tenant_id],
            key=lambda value: value.created_at,
            reverse=True,
        )

    def save_agent_run(self, run: AgentRun) -> AgentRun:
        self.agent_runs[run.agent_run_id] = run
        return run

    def get_agent_run(self, run_id: str, tenant_id: str) -> AgentRun | None:
        value = self.agent_runs.get(run_id)
        return value if value and value.tenant_id == tenant_id else None

    def list_agent_runs(self, tenant_id: str) -> list[AgentRun]:
        return sorted(
            [value for value in self.agent_runs.values() if value.tenant_id == tenant_id],
            key=lambda value: value.created_at,
            reverse=True,
        )

    def create_pilot(
        self,
        release: PilotRelease,
        participants: list[PilotParticipant],
        tasks: list[PilotTaskAssignment],
    ) -> PilotRelease:
        with self._lock:
            if any(
                value.tenant_id == release.tenant_id and value.version == release.version
                for value in self.pilot_releases.values()
            ):
                raise ValueError("Pilot version already exists")
            self.pilot_releases[release.pilot_id] = release
            self.pilot_participants.update(
                {value.participant_id: value for value in participants}
            )
            self.pilot_tasks.update({value.assignment_id: value for value in tasks})
            return release

    def get_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        value = self.pilot_releases.get(pilot_id)
        return value if value and value.tenant_id == tenant_id else None

    def list_pilot_participants(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotParticipant]:
        return sorted(
            [
                value
                for value in self.pilot_participants.values()
                if value.tenant_id == tenant_id and value.pilot_id == pilot_id
            ],
            key=lambda value: value.participant_code,
        )

    def find_active_pilot_participant(
        self, tenant_id: str, subject: str
    ) -> PilotParticipant | None:
        return next(
            (
                value
                for value in self.pilot_participants.values()
                if value.tenant_id == tenant_id
                and value.subject == subject
                and value.active
                and self.pilot_releases[value.pilot_id].status == "frozen"
            ),
            None,
        )

    def list_pilot_tasks(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotTaskAssignment]:
        return sorted(
            [
                value
                for value in self.pilot_tasks.values()
                if value.tenant_id == tenant_id and value.pilot_id == pilot_id
            ],
            key=lambda value: (value.participant_code, value.created_at),
        )

    def freeze_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        with self._lock:
            value = self.get_pilot(pilot_id, tenant_id)
            if value is None:
                return None
            if value.status != "draft":
                raise ValueError("Only draft pilots can be frozen")
            value.status = "frozen"
            value.frozen_at = utc_now()
            return value

    def close_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        with self._lock:
            value = self.get_pilot(pilot_id, tenant_id)
            if value is None:
                return None
            if value.status != "frozen":
                raise ValueError("Only frozen pilots can be closed")
            value.status = "closed"
            value.closed_at = utc_now()
            return value

    def record_pilot_feedback(self, feedback: PilotFeedback) -> PilotFeedback:
        with self._lock:
            for value in self.pilot_feedback.values():
                if (
                    value.tenant_id == feedback.tenant_id
                    and value.pilot_id == feedback.pilot_id
                    and value.conversation_id == feedback.conversation_id
                    and value.participant_code == feedback.participant_code
                ):
                    raise ValueError("Feedback already exists for this conversation")
            if feedback.assignment_id:
                task = self.pilot_tasks.get(feedback.assignment_id)
                if (
                    task is None
                    or task.tenant_id != feedback.tenant_id
                    or task.pilot_id != feedback.pilot_id
                    or task.participant_code != feedback.participant_code
                ):
                    raise ValueError("Pilot task not found")
                task.conversation_id = feedback.conversation_id
                task.status = "completed" if feedback.completed else "failed"
                task.started_at = task.started_at or feedback.created_at
                task.completed_at = feedback.created_at
                task.notes_redacted = feedback.comment_redacted
            self.pilot_feedback[feedback.feedback_id] = feedback
            return feedback

    def list_pilot_feedback(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotFeedback]:
        return sorted(
            [
                value
                for value in self.pilot_feedback.values()
                if value.tenant_id == tenant_id and value.pilot_id == pilot_id
            ],
            key=lambda value: value.created_at,
        )

    def save_pilot_metric_snapshot(
        self, snapshot: PilotMetricSnapshot
    ) -> PilotMetricSnapshot:
        self.pilot_metric_snapshots[snapshot.snapshot_id] = snapshot
        return snapshot

    def list_pilot_metric_snapshots(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotMetricSnapshot]:
        return sorted(
            [
                value
                for value in self.pilot_metric_snapshots.values()
                if value.tenant_id == tenant_id and value.pilot_id == pilot_id
            ],
            key=lambda value: value.created_at,
        )


store = InMemoryStore()
