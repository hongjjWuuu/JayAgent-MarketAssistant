from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import (
    ActionExecution,
    ActionPlan,
    ActionStatus,
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
    Message,
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
from app.persistence.database import get_session_factory
from app.persistence.models import (
    ActionExecutionRow,
    ActionPlanRow,
    AgentRunRow,
    AuditEventRow,
    ChannelIdentityRow,
    CompensationProposalRow,
    ConversationEventRow,
    ConversationRow,
    EvaluationCaseRow,
    EvaluationDatasetRow,
    EvaluationResultRow,
    EvaluationRunRow,
    HandoffRow,
    LlmTraceRow,
    MessageRow,
    OrderRow,
    PilotFeedbackRow,
    PilotMetricSnapshotRow,
    PilotParticipantRow,
    PilotReleaseRow,
    PilotTaskAssignmentRow,
    PolicyChunkEmbeddingRow,
    PolicyChunkRow,
    PolicyDocumentRow,
    PolicyVersionRow,
    RetrievalProfileRow,
    RetrievalSnapshotRow,
    RuntimeTaskEventRow,
    RuntimeTaskRow,
    RuntimeWorkerRow,
    ShipmentRow,
    SupportQueueMemberRow,
    TenantMemberRow,
    TicketRow,
)
from app.services.embeddings import embedding_provider_for, get_embedding_provider
from app.services.policy_chunker import chunk_policy


class PostgresRepository:
    def __init__(self, session_factory_override: sessionmaker[Session] | None = None) -> None:
        self.session_factory = session_factory_override or get_session_factory()

    def member_role(self, tenant_id: str, subject: str) -> str | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(TenantMemberRow).where(
                    TenantMemberRow.tenant_id == tenant_id,
                    TenantMemberRow.subject == subject,
                    TenantMemberRow.active.is_(True),
                )
            )
            return row.role if row else None

    def resolve_channel_customer(
        self, tenant_id: str, channel: str, channel_user_id: str
    ) -> str | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ChannelIdentityRow).where(
                    ChannelIdentityRow.tenant_id == tenant_id,
                    ChannelIdentityRow.channel == channel,
                    ChannelIdentityRow.channel_user_id == channel_user_id,
                )
            )
            return row.customer_id if row else None

    def list_agent_queue_ids(self, tenant_id: str, subject: str) -> set[str]:
        with self.session_factory() as session:
            return set(
                session.scalars(
                    select(SupportQueueMemberRow.queue_id).where(
                        SupportQueueMemberRow.tenant_id == tenant_id,
                        SupportQueueMemberRow.subject == subject,
                        SupportQueueMemberRow.active.is_(True),
                    )
                )
            )

    @staticmethod
    def _conversation(row: ConversationRow, messages: list[MessageRow]) -> Conversation:
        return Conversation(
            conversation_id=row.id,
            tenant_id=row.tenant_id,
            customer_id=row.customer_id,
            channel=row.channel,
            status=row.status,
            owner_type=row.owner_type,
            messages=[
                Message(
                    message_id=item.id,
                    sender_type=item.sender_type,
                    content=item.content,
                    facts=item.facts or {},
                    task_id=item.task_id,
                    created_at=item.created_at,
                )
                for item in messages
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _execution(row: ActionExecutionRow) -> ActionExecution:
        return ActionExecution(
            execution_id=row.id,
            tenant_id=row.tenant_id,
            action_plan_id=row.action_plan_id,
            idempotency_key=row.idempotency_key,
            status=row.status,
            receipt=row.receipt or {},
            task_id=row.task_id,
            error_code=row.error_code,
            created_at=row.created_at,
            updated_at=row.updated_at,
            reconciliation_attempts=row.reconciliation_attempts,
            last_reconciled_at=row.last_reconciled_at,
        )

    @staticmethod
    def _task(row: RuntimeTaskRow) -> RuntimeTask:
        return RuntimeTask(
            task_id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            task_type=row.task_type,
            payload=row.payload,
            status=row.status,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            available_at=row.available_at,
            last_error=row.last_error,
            replay_of_task_id=row.replay_of_task_id,
            replayed_by=row.replayed_by,
            replay_reason=row.replay_reason,
            dead_lettered_at=row.dead_lettered_at,
            cancel_requested_at=row.cancel_requested_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def save_conversation(self, conversation: Conversation) -> Conversation:
        with self.session_factory.begin() as session:
            row = session.get(ConversationRow, conversation.conversation_id)
            if row is None:
                row = ConversationRow(
                    id=conversation.conversation_id,
                    tenant_id=conversation.tenant_id,
                    customer_id=conversation.customer_id,
                    channel=conversation.channel,
                    status=conversation.status.value,
                    owner_type=conversation.owner_type,
                    created_at=conversation.created_at,
                    updated_at=conversation.updated_at,
                )
                session.add(row)
            else:
                row.status = conversation.status.value
                row.owner_type = conversation.owner_type
                row.updated_at = conversation.updated_at
            known = set(
                session.scalars(
                    select(MessageRow.id).where(
                        MessageRow.conversation_id == conversation.conversation_id
                    )
                )
            )
            for message in conversation.messages:
                if message.message_id not in known:
                    session.add(
                        MessageRow(
                            id=message.message_id,
                            tenant_id=conversation.tenant_id,
                            conversation_id=conversation.conversation_id,
                            sender_type=message.sender_type,
                            content=message.content,
                            facts=message.facts,
                            task_id=message.task_id,
                            created_at=message.created_at,
                        )
                    )
        return conversation

    def get_conversation(self, conversation_id: str, tenant_id: str) -> Conversation | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ConversationRow).where(
                    ConversationRow.id == conversation_id, ConversationRow.tenant_id == tenant_id
                )
            )
            if row is None:
                return None
            messages = list(
                session.scalars(
                    select(MessageRow)
                    .where(MessageRow.conversation_id == conversation_id)
                    .order_by(MessageRow.created_at)
                )
            )
            return self._conversation(row, messages)

    def list_conversations(
        self, tenant_id: str, customer_id: str | None = None
    ) -> list[Conversation]:
        with self.session_factory() as session:
            query = select(ConversationRow).where(ConversationRow.tenant_id == tenant_id)
            if customer_id:
                query = query.where(ConversationRow.customer_id == customer_id)
            rows = list(session.scalars(query.order_by(ConversationRow.updated_at.desc())))
            return [self._conversation(row, []) for row in rows]

    def get_order(self, order_id: str, tenant_id: str, customer_id: str) -> Order | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(OrderRow).where(
                    OrderRow.external_order_id == order_id,
                    OrderRow.tenant_id == tenant_id,
                    OrderRow.customer_id == customer_id,
                )
            )
            if row is None:
                return None
            shipment = session.scalar(select(ShipmentRow).where(ShipmentRow.order_id == row.id))
            return Order(
                order_id=row.external_order_id,
                tenant_id=row.tenant_id,
                customer_id=row.customer_id,
                status=row.status,
                total_amount=row.total_amount,
                shipment_status=shipment.status if shipment else "unknown",
                tracking_number=shipment.tracking_number if shipment else "",
                updated_at=row.updated_at,
            )

    @staticmethod
    def _handoff(row: HandoffRow) -> Handoff:
        return Handoff(
            handoff_id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            queue_id=row.queue_id or "queue_general",
            reason=row.reason,
            status=row.status,
            assigned_agent_id=row.assigned_agent_id,
            priority=row.priority,
            context_snapshot=row.context_snapshot,
            created_at=row.created_at,
        )

    def save_handoff(self, handoff: Handoff) -> Handoff:
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(HandoffRow).where(
                    HandoffRow.tenant_id == handoff.tenant_id,
                    HandoffRow.conversation_id == handoff.conversation_id,
                    HandoffRow.status.in_(["queued", "claimed"]),
                )
            )
            if existing and existing.id != handoff.handoff_id:
                return self._handoff(existing)
            row = session.get(HandoffRow, handoff.handoff_id)
            if row is None:
                row = HandoffRow(
                    id=handoff.handoff_id,
                    tenant_id=handoff.tenant_id,
                    conversation_id=handoff.conversation_id,
                    queue_id=handoff.queue_id,
                    reason=handoff.reason,
                    status=handoff.status,
                    assigned_agent_id=handoff.assigned_agent_id,
                    priority=handoff.priority,
                    context_snapshot=handoff.context_snapshot,
                    created_at=handoff.created_at,
                    updated_at=utc_now(),
                )
                session.add(row)
            else:
                row.status = handoff.status
                row.assigned_agent_id = handoff.assigned_agent_id
                row.queue_id = handoff.queue_id
                row.priority = handoff.priority
                row.context_snapshot = handoff.context_snapshot
                row.updated_at = utc_now()
        return handoff

    def get_handoff(self, handoff_id: str, tenant_id: str) -> Handoff | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(HandoffRow).where(
                    HandoffRow.id == handoff_id, HandoffRow.tenant_id == tenant_id
                )
            )
            return self._handoff(row) if row else None

    def claim_handoff_atomic(
        self,
        handoff_id: str,
        tenant_id: str,
        agent_id: str,
        allowed_queue_ids: set[str] | None,
    ) -> Handoff | None:
        with self.session_factory.begin() as session:
            query = select(HandoffRow).where(
                HandoffRow.id == handoff_id, HandoffRow.tenant_id == tenant_id
            )
            if allowed_queue_ids is not None:
                if not allowed_queue_ids:
                    return None
                query = query.where(HandoffRow.queue_id.in_(allowed_queue_ids))
            row = session.scalar(query.with_for_update())
            if row is None:
                return None
            if row.status == "claimed":
                return self._handoff(row) if row.assigned_agent_id == agent_id else None
            if row.status != "queued":
                return None
            row.status = "claimed"
            row.assigned_agent_id = agent_id
            row.context_snapshot = {
                **(row.context_snapshot or {}),
                "claimed_at": utc_now().isoformat(),
            }
            row.updated_at = utc_now()
            session.flush()
            return self._handoff(row)

    def list_handoffs(
        self,
        tenant_id: str,
        status: str | None = None,
        allowed_queue_ids: set[str] | None = None,
    ) -> list[Handoff]:
        with self.session_factory() as session:
            query = select(HandoffRow).where(HandoffRow.tenant_id == tenant_id)
            if allowed_queue_ids is not None:
                if not allowed_queue_ids:
                    return []
                query = query.where(HandoffRow.queue_id.in_(allowed_queue_ids))
            if status:
                query = query.where(HandoffRow.status == status)
            rows = session.scalars(
                query.order_by(HandoffRow.priority.desc(), HandoffRow.created_at)
            )
            return [self._handoff(row) for row in rows]

    def save_action_plan(self, plan: ActionPlan) -> ActionPlan:
        with self.session_factory.begin() as session:
            row = session.get(ActionPlanRow, plan.action_plan_id)
            values = {
                "tenant_id": plan.tenant_id,
                "customer_id": plan.customer_id,
                "conversation_id": plan.conversation_id,
                "action_type": plan.action_type,
                "risk_level": plan.risk_level,
                "payload": plan.payload,
                "preview": plan.preview,
                "idempotency_key": plan.idempotency_key,
                "status": plan.status.value,
                "created_at": plan.created_at,
            }
            if row is None:
                session.add(ActionPlanRow(id=plan.action_plan_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        return plan

    def get_action_plan(self, action_plan_id: str, tenant_id: str) -> ActionPlan | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ActionPlanRow).where(
                    ActionPlanRow.id == action_plan_id, ActionPlanRow.tenant_id == tenant_id
                )
            )
            if row is None:
                return None
            return ActionPlan(
                action_plan_id=row.id,
                tenant_id=row.tenant_id,
                customer_id=row.customer_id,
                conversation_id=row.conversation_id,
                action_type=row.action_type,
                risk_level=row.risk_level,
                payload=row.payload,
                preview=row.preview,
                idempotency_key=row.idempotency_key,
                status=row.status,
                created_at=row.created_at,
            )

    @staticmethod
    def _compensation_proposal(row: CompensationProposalRow) -> CompensationProposal:
        return CompensationProposal(
            proposal_id=row.id,
            tenant_id=row.tenant_id,
            customer_id=row.customer_id,
            order_id=row.order_id,
            conversation_id=row.conversation_id,
            retrieval_snapshot_id=row.retrieval_snapshot_id,
            policy_version_id=row.policy_version_id,
            recommended_amount=row.recommended_amount,
            reason=row.reason,
            status=row.status,
            expires_at=row.expires_at,
            created_at=row.created_at,
        )

    def save_compensation_proposal(
        self, proposal: CompensationProposal
    ) -> CompensationProposal:
        with self.session_factory.begin() as session:
            session.merge(
                CompensationProposalRow(
                    id=proposal.proposal_id,
                    tenant_id=proposal.tenant_id,
                    customer_id=proposal.customer_id,
                    order_id=proposal.order_id,
                    conversation_id=proposal.conversation_id,
                    retrieval_snapshot_id=proposal.retrieval_snapshot_id,
                    policy_version_id=proposal.policy_version_id,
                    recommended_amount=proposal.recommended_amount,
                    reason=proposal.reason,
                    status=proposal.status,
                    expires_at=proposal.expires_at,
                    created_at=proposal.created_at,
                )
            )
        return proposal

    def get_compensation_proposal(
        self, proposal_id: str, tenant_id: str
    ) -> CompensationProposal | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(CompensationProposalRow).where(
                    CompensationProposalRow.id == proposal_id,
                    CompensationProposalRow.tenant_id == tenant_id,
                )
            )
            return self._compensation_proposal(row) if row else None

    def execution_for_key(self, tenant_id: str, key: str) -> ActionExecution | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.tenant_id == tenant_id,
                    ActionExecutionRow.idempotency_key == key,
                )
            )
            return self._execution(row) if row else None

    def get_execution(self, execution_id: str, tenant_id: str) -> ActionExecution | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.id == execution_id,
                    ActionExecutionRow.tenant_id == tenant_id,
                )
            )
            return self._execution(row) if row else None

    def save_execution(self, execution: ActionExecution) -> ActionExecution:
        with self.session_factory.begin() as session:
            row = session.get(ActionExecutionRow, execution.execution_id)
            if row is None:
                session.add(
                    ActionExecutionRow(
                        id=execution.execution_id,
                        tenant_id=execution.tenant_id,
                        action_plan_id=execution.action_plan_id,
                        idempotency_key=execution.idempotency_key,
                        status=execution.status.value,
                        receipt=execution.receipt,
                        task_id=execution.task_id,
                        error_code=execution.error_code,
                        created_at=execution.created_at,
                        updated_at=execution.updated_at,
                        reconciliation_attempts=execution.reconciliation_attempts,
                        last_reconciled_at=execution.last_reconciled_at,
                    )
                )
            else:
                row.status = execution.status.value
                row.receipt = execution.receipt
                row.error_code = execution.error_code
                row.updated_at = execution.updated_at
                row.reconciliation_attempts = execution.reconciliation_attempts
                row.last_reconciled_at = execution.last_reconciled_at
        return execution

    def confirm_action_atomic(
        self, plan: ActionPlan, task: RuntimeTask, execution: ActionExecution
    ) -> ActionExecution:
        try:
            with self.session_factory.begin() as session:
                existing = session.scalar(
                    select(ActionExecutionRow).where(
                        ActionExecutionRow.tenant_id == plan.tenant_id,
                        ActionExecutionRow.idempotency_key == plan.idempotency_key,
                    )
                )
                if existing:
                    return self._execution(existing)
                plan_row = session.execute(
                    select(ActionPlanRow)
                    .where(
                        ActionPlanRow.id == plan.action_plan_id,
                        ActionPlanRow.tenant_id == plan.tenant_id,
                    )
                    .with_for_update()
                ).scalar_one()
                plan_row.status = ActionStatus.QUEUED.value
                session.add(
                    RuntimeTaskRow(
                        id=task.task_id,
                        tenant_id=task.tenant_id,
                        conversation_id=task.conversation_id,
                        task_type=task.task_type,
                        payload=task.payload,
                        status=task.status.value,
                        attempts=task.attempts,
                        max_attempts=task.max_attempts,
                        available_at=task.available_at,
                        created_at=task.created_at,
                        updated_at=task.updated_at,
                    )
                )
                # RuntimeTaskEventRow has an FK to runtime_task but these
                # persistence rows intentionally have no ORM relationship.
                # Flush the parent before adding dependent outbox rows.
                session.flush()
                session.add(
                    ActionExecutionRow(
                        id=execution.execution_id,
                        tenant_id=execution.tenant_id,
                        action_plan_id=execution.action_plan_id,
                        idempotency_key=execution.idempotency_key,
                        status=execution.status.value,
                        receipt={},
                        task_id=task.task_id,
                        created_at=execution.created_at,
                    )
                )
                session.add(
                    RuntimeTaskEventRow(
                        id=f"rte_{uuid4().hex}",
                        tenant_id=task.tenant_id,
                        task_id=task.task_id,
                        event_type="queued",
                        data={"action_plan_id": plan.action_plan_id},
                    )
                )
            return execution
        except IntegrityError:
            existing = self.execution_for_key(plan.tenant_id, plan.idempotency_key)
            if existing is None:
                raise
            return existing

    def save_ticket(self, ticket: Ticket) -> Ticket:
        with self.session_factory.begin() as session:
            session.merge(
                TicketRow(
                    id=ticket.ticket_id,
                    tenant_id=ticket.tenant_id,
                    customer_id=ticket.customer_id,
                    conversation_id=ticket.conversation_id,
                    title=ticket.title,
                    description=ticket.description,
                    priority=ticket.priority,
                    status=ticket.status,
                    created_at=ticket.created_at,
                )
            )
        return ticket

    @staticmethod
    def _ticket(row: TicketRow) -> Ticket:
        return Ticket(
            ticket_id=row.id,
            tenant_id=row.tenant_id,
            customer_id=row.customer_id,
            conversation_id=row.conversation_id,
            title=row.title,
            description=row.description,
            priority=row.priority,
            status=row.status,
            created_at=row.created_at,
        )

    def get_ticket(self, ticket_id: str, tenant_id: str, customer_id: str | None) -> Ticket | None:
        with self.session_factory() as session:
            query = select(TicketRow).where(
                TicketRow.id == ticket_id, TicketRow.tenant_id == tenant_id
            )
            if customer_id:
                query = query.where(TicketRow.customer_id == customer_id)
            row = session.scalar(query)
            return self._ticket(row) if row else None

    def list_tickets(self, tenant_id: str, customer_id: str | None = None) -> list[Ticket]:
        with self.session_factory() as session:
            query = select(TicketRow).where(TicketRow.tenant_id == tenant_id)
            if customer_id:
                query = query.where(TicketRow.customer_id == customer_id)
            return [
                self._ticket(row)
                for row in session.scalars(query.order_by(TicketRow.created_at.desc()))
            ]

    def append_audit(self, event: AuditEvent) -> None:
        with self.session_factory.begin() as session:
            session.add(
                AuditEventRow(
                    id=event.event_id,
                    tenant_id=event.tenant_id,
                    actor_id=event.actor_id,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    request_id=event.request_id,
                    data=event.data,
                    created_at=event.created_at,
                )
            )

    def append_event(self, event: ConversationEvent) -> None:
        with self.session_factory.begin() as session:
            session.add(
                ConversationEventRow(
                    id=event.event_id,
                    tenant_id=event.tenant_id,
                    conversation_id=event.conversation_id,
                    event_type=event.event_type,
                    data=event.data,
                    created_at=event.created_at,
                )
            )

    def list_events(
        self, conversation_id: str, tenant_id: str, after=None
    ) -> list[ConversationEvent]:
        with self.session_factory() as session:
            query = select(ConversationEventRow).where(
                ConversationEventRow.conversation_id == conversation_id,
                ConversationEventRow.tenant_id == tenant_id,
            )
            if after:
                query = query.where(ConversationEventRow.created_at > after)
            rows = session.scalars(query.order_by(ConversationEventRow.created_at))
            return [
                ConversationEvent(
                    event_id=row.id,
                    tenant_id=row.tenant_id,
                    conversation_id=row.conversation_id,
                    event_type=row.event_type,
                    data=row.data,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    def enqueue_task(self, task: RuntimeTask) -> RuntimeTask:
        with self.session_factory.begin() as session:
            row = RuntimeTaskRow(
                id=task.task_id,
                tenant_id=task.tenant_id,
                conversation_id=task.conversation_id,
                task_type=task.task_type,
                payload=task.payload,
                status=task.status.value,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                available_at=task.available_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
            session.add(row)
            session.flush()
            session.add(
                RuntimeTaskEventRow(
                    id=f"rte_{uuid4().hex}",
                    tenant_id=task.tenant_id,
                    task_id=task.task_id,
                    event_type="queued",
                    data={},
                )
            )
        return task

    def enqueue_message_atomic(
        self, conversation: Conversation, task: RuntimeTask, event: ConversationEvent
    ) -> RuntimeTask:
        with self.session_factory.begin() as session:
            row = session.execute(
                select(ConversationRow)
                .where(
                    ConversationRow.id == conversation.conversation_id,
                    ConversationRow.tenant_id == conversation.tenant_id,
                )
                .with_for_update()
            ).scalar_one()
            row.status = conversation.status.value
            row.owner_type = conversation.owner_type
            row.updated_at = conversation.updated_at
            message = conversation.messages[-1]
            session.add(
                MessageRow(
                    id=message.message_id,
                    tenant_id=conversation.tenant_id,
                    conversation_id=conversation.conversation_id,
                    sender_type=message.sender_type,
                    content=message.content,
                    facts=message.facts,
                    task_id=message.task_id,
                    created_at=message.created_at,
                )
            )
            session.add(
                RuntimeTaskRow(
                    id=task.task_id,
                    tenant_id=task.tenant_id,
                    conversation_id=task.conversation_id,
                    task_type=task.task_type,
                    payload=task.payload,
                    status=task.status.value,
                    attempts=task.attempts,
                    max_attempts=task.max_attempts,
                    available_at=task.available_at,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
            # See confirm_action_atomic: explicit ordering is required because
            # RuntimeTaskRow and RuntimeTaskEventRow are not ORM-related.
            session.flush()
            session.add(
                ConversationEventRow(
                    id=event.event_id,
                    tenant_id=event.tenant_id,
                    conversation_id=event.conversation_id,
                    event_type=event.event_type,
                    data=event.data,
                    created_at=event.created_at,
                )
            )
            session.add(
                RuntimeTaskEventRow(
                    id=f"rte_{uuid4().hex}",
                    tenant_id=task.tenant_id,
                    task_id=task.task_id,
                    event_type="queued",
                    data={"conversation_id": task.conversation_id},
                )
            )
        return task

    def claim_task(self, worker_id: str, lease_seconds: int) -> RuntimeTask | None:
        now = utc_now()
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RuntimeTaskRow)
                .where(
                    RuntimeTaskRow.available_at <= now,
                    or_(
                        RuntimeTaskRow.status.in_(["queued", "retry_wait"]),
                        (RuntimeTaskRow.status == "running")
                        & (RuntimeTaskRow.lease_expires_at <= now),
                    ),
                )
                .order_by(RuntimeTaskRow.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            recovered = row.status == "running"
            if not recovered:
                ensure_task_transition(RuntimeTaskStatus(row.status), RuntimeTaskStatus.RUNNING)
            row.status = "running"
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.attempts += 1
            row.updated_at = now
            session.add(
                RuntimeTaskEventRow(
                    id=f"rte_{uuid4().hex}",
                    tenant_id=row.tenant_id,
                    task_id=row.id,
                    event_type="recovered" if recovered else "claimed",
                    data={"worker_id": worker_id, "attempt": row.attempts},
                )
            )
            session.flush()
            return self._task(row)

    def heartbeat_task(self, task_id: str, worker_id: str, lease_seconds: int) -> bool:
        with self.session_factory.begin() as session:
            result = session.execute(
                update(RuntimeTaskRow)
                .where(
                    RuntimeTaskRow.id == task_id,
                    RuntimeTaskRow.status == "running",
                    RuntimeTaskRow.lease_owner == worker_id,
                )
                .values(
                    lease_expires_at=utc_now() + timedelta(seconds=lease_seconds),
                    updated_at=utc_now(),
                )
            )
            return bool(result.rowcount)

    def complete_task(self, task: RuntimeTask, worker_id: str) -> RuntimeTask:
        return self._finish_task(task, RuntimeTaskStatus.COMPLETED, None, worker_id)

    def fail_task(self, task: RuntimeTask, error: str, worker_id: str) -> RuntimeTask:
        status = (
            RuntimeTaskStatus.DEAD_LETTER
            if task.attempts >= task.max_attempts
            else RuntimeTaskStatus.RETRY_WAIT
        )
        return self._finish_task(task, status, error[:1000], worker_id)

    def _finish_task(
        self, task: RuntimeTask, status: RuntimeTaskStatus, error: str | None, worker_id: str
    ) -> RuntimeTask:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RuntimeTaskRow).where(RuntimeTaskRow.id == task.task_id).with_for_update()
            )
            if row is None or row.status != "running" or row.lease_owner != worker_id:
                raise LeaseLostError(f"Worker {worker_id} no longer owns task {task.task_id}")
            ensure_task_transition(RuntimeTaskStatus(row.status), status)
            row.status = status.value
            row.last_error = error
            row.lease_owner = None
            row.lease_expires_at = None
            row.updated_at = utc_now()
            if status == RuntimeTaskStatus.RETRY_WAIT:
                row.available_at = utc_now() + timedelta(seconds=min(2**row.attempts, 30))
            if status == RuntimeTaskStatus.DEAD_LETTER:
                row.dead_lettered_at = utc_now()
            session.add(
                RuntimeTaskEventRow(
                    id=f"rte_{uuid4().hex}",
                    tenant_id=row.tenant_id,
                    task_id=row.id,
                    event_type=status.value,
                    data={"worker_id": worker_id, "error": error},
                )
            )
            session.flush()
            return self._task(row)

    def get_task(self, task_id: str, tenant_id: str) -> RuntimeTask | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RuntimeTaskRow).where(
                    RuntimeTaskRow.id == task_id, RuntimeTaskRow.tenant_id == tenant_id
                )
            )
            return self._task(row) if row else None

    def cancel_task(self, task_id: str, tenant_id: str) -> RuntimeTask | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(RuntimeTaskRow).where(
                    RuntimeTaskRow.id == task_id, RuntimeTaskRow.tenant_id == tenant_id
                )
            )
            if row and row.status in {"queued", "retry_wait"}:
                ensure_task_transition(RuntimeTaskStatus(row.status), RuntimeTaskStatus.CANCELLED)
                row.status = "cancelled"
                row.cancel_requested_at = utc_now()
                row.updated_at = utc_now()
                session.add(
                    RuntimeTaskEventRow(
                        id=f"rte_{uuid4().hex}",
                        tenant_id=row.tenant_id,
                        task_id=row.id,
                        event_type="cancelled",
                        data={},
                    )
                )
                session.flush()
            return self._task(row) if row else None

    def list_tasks(
        self, tenant_id: str, status: str | None = None, task_type: str | None = None
    ) -> list[RuntimeTask]:
        with self.session_factory() as session:
            query = select(RuntimeTaskRow).where(RuntimeTaskRow.tenant_id == tenant_id)
            if status:
                query = query.where(RuntimeTaskRow.status == status)
            if task_type:
                query = query.where(RuntimeTaskRow.task_type == task_type)
            rows = session.scalars(query.order_by(RuntimeTaskRow.created_at.desc()).limit(200))
            return [self._task(row) for row in rows]

    def list_task_events(self, task_id: str, tenant_id: str) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(RuntimeTaskEventRow)
                .where(
                    RuntimeTaskEventRow.task_id == task_id,
                    RuntimeTaskEventRow.tenant_id == tenant_id,
                )
                .order_by(RuntimeTaskEventRow.created_at)
            )
            return [
                {
                    "event_id": row.id,
                    "task_id": row.task_id,
                    "event_type": row.event_type,
                    "data": row.data,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    def replay_dead_letter(
        self, task_id: str, tenant_id: str, actor_id: str, reason: str
    ) -> RuntimeTask:
        replay_id = f"task_{uuid4().hex}"
        try:
            with self.session_factory.begin() as session:
                original = session.scalar(
                    select(RuntimeTaskRow)
                    .where(RuntimeTaskRow.id == task_id, RuntimeTaskRow.tenant_id == tenant_id)
                    .with_for_update()
                )
                if original is None:
                    raise LookupError(task_id)
                if original.status != "dead_letter":
                    raise ValueError("TASK_NOT_REPLAYABLE")
                replay = RuntimeTaskRow(
                    id=replay_id,
                    tenant_id=original.tenant_id,
                    conversation_id=original.conversation_id,
                    task_type=original.task_type,
                    payload=original.payload,
                    status="queued",
                    attempts=0,
                    max_attempts=original.max_attempts,
                    available_at=utc_now(),
                    replay_of_task_id=original.id,
                    replayed_by=actor_id,
                    replay_reason=reason,
                )
                session.add(replay)
                session.flush()
                session.add_all(
                    [
                        RuntimeTaskEventRow(
                            id=f"rte_{uuid4().hex}",
                            tenant_id=tenant_id,
                            task_id=original.id,
                            event_type="replayed",
                            data={"new_task_id": replay_id, "actor_id": actor_id, "reason": reason},
                        ),
                        RuntimeTaskEventRow(
                            id=f"rte_{uuid4().hex}",
                            tenant_id=tenant_id,
                            task_id=replay_id,
                            event_type="queued",
                            data={"replay_of_task_id": original.id},
                        ),
                    ]
                )
                session.flush()
                return self._task(replay)
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(RuntimeTaskRow).where(RuntimeTaskRow.replay_of_task_id == task_id)
                )
                if existing is None:
                    raise
                return self._task(existing)

    def heartbeat_worker(
        self,
        worker_id: str,
        hostname: str,
        process_id: int,
        current_task_id: str | None,
        metadata: dict,
    ) -> None:
        now = utc_now()
        with self.session_factory.begin() as session:
            row = session.get(RuntimeWorkerRow, worker_id)
            if row is None:
                session.add(
                    RuntimeWorkerRow(
                        id=worker_id,
                        started_at=now,
                        last_heartbeat_at=now,
                        current_task_id=current_task_id,
                        hostname=hostname,
                        process_id=process_id,
                        metadata_json=metadata,
                    )
                )
            else:
                row.last_heartbeat_at = now
                row.current_task_id = current_task_id
                row.metadata_json = metadata

    def list_workers(self) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(RuntimeWorkerRow).order_by(RuntimeWorkerRow.last_heartbeat_at.desc())
            )
            return [
                {
                    "worker_id": row.id,
                    "started_at": row.started_at,
                    "last_heartbeat_at": row.last_heartbeat_at,
                    "current_task_id": row.current_task_id,
                    "hostname": row.hostname,
                    "process_id": row.process_id,
                    "metadata": row.metadata_json,
                }
                for row in rows
            ]

    def list_reconciliations(self, tenant_id: str) -> list[ActionExecution]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ActionExecutionRow).where(
                    ActionExecutionRow.tenant_id == tenant_id,
                    ActionExecutionRow.status == ActionStatus.UNKNOWN_NEEDS_RECONCILIATION.value,
                )
            )
            return [self._execution(row) for row in rows]

    @staticmethod
    def _policy(row: PolicyVersionRow) -> PolicyVersion:
        return PolicyVersion(
            policy_version_id=row.id,
            policy_id=row.policy_id,
            tenant_id=row.tenant_id,
            scope=row.scope,
            version=row.version,
            title=row.title,
            status=row.status,
            index_status=row.index_status,
            index_error=row.index_error,
            content=row.content,
            content_hash=row.content_hash,
            acl=row.acl_json or {},
            published_by=row.published_by,
            effective_from=row.effective_from,
            effective_to=row.effective_to,
            published_at=row.published_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def save_policy(self, policy: PolicyVersion) -> PolicyVersion:
        if not policy.policy_id:
            family = hashlib.sha256(
                f"{policy.tenant_id}:{policy.scope}".encode()
            ).hexdigest()[:32]
            policy.policy_id = f"policy_family_{family}"
        with self.session_factory.begin() as session:
            session.add(
                PolicyVersionRow(
                    id=policy.policy_version_id,
                    policy_id=policy.policy_id,
                    tenant_id=policy.tenant_id,
                    scope=policy.scope,
                    version=policy.version,
                    title=policy.title,
                    status=policy.status,
                    index_status=policy.index_status,
                    index_error=policy.index_error,
                    content=policy.content,
                    content_hash=policy.content_hash,
                    acl_json=policy.acl,
                    published_by=policy.published_by,
                    effective_from=policy.effective_from,
                    effective_to=policy.effective_to,
                    published_at=policy.published_at,
                    created_at=policy.created_at,
                    updated_at=policy.updated_at,
                )
            )
        return policy

    def get_policy(self, policy_id: str, tenant_id: str) -> PolicyVersion | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(PolicyVersionRow).where(
                    PolicyVersionRow.id == policy_id,
                    PolicyVersionRow.tenant_id == tenant_id,
                )
            )
            return self._policy(row) if row else None

    def list_policy_versions(
        self, tenant_id: str, scope: str | None = None, status: str | None = None
    ) -> list[PolicyVersion]:
        with self.session_factory() as session:
            query = select(PolicyVersionRow).where(PolicyVersionRow.tenant_id == tenant_id)
            if scope:
                query = query.where(PolicyVersionRow.scope == scope)
            if status:
                query = query.where(PolicyVersionRow.status == status)
            rows = session.scalars(query.order_by(PolicyVersionRow.scope, PolicyVersionRow.created_at.desc()))
            return [self._policy(row) for row in rows]

    def update_policy_draft(self, policy: PolicyVersion) -> PolicyVersion:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(PolicyVersionRow)
                .where(
                    PolicyVersionRow.id == policy.policy_version_id,
                    PolicyVersionRow.tenant_id == policy.tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                raise LookupError("Policy not found")
            if row.status != "draft":
                raise ValueError("Published policy versions are immutable")
            row.title = policy.title
            row.content = policy.content
            row.content_hash = policy.content_hash
            row.acl_json = policy.acl
            row.effective_from = policy.effective_from
            row.effective_to = policy.effective_to
            row.index_status = "pending"
            row.index_error = None
            row.updated_at = utc_now()
            session.flush()
            return self._policy(row)

    @staticmethod
    def _build_policy_index(session: Session, row: PolicyVersionRow) -> None:
        embedder = get_embedding_provider()
        chunks = chunk_policy(row.content)
        if not chunks:
            raise ValueError("Policy content produced no searchable chunks")
        document_ids = select(PolicyDocumentRow.id).where(
            PolicyDocumentRow.policy_version_id == row.id
        )
        session.execute(delete(PolicyChunkRow).where(PolicyChunkRow.policy_document_id.in_(document_ids)))
        session.execute(delete(PolicyDocumentRow).where(PolicyDocumentRow.policy_version_id == row.id))
        document_id = f"doc_{uuid4().hex}"
        session.add(
            PolicyDocumentRow(
                id=document_id,
                tenant_id=row.tenant_id,
                policy_version_id=row.id,
                metadata_json={"scope": row.scope, "version": row.version},
            )
        )
        session.flush()
        vectors = embedder.embed_documents([chunk.content for chunk in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            session.add(
                PolicyChunkRow(
                    id=f"chunk_{uuid4().hex}",
                    tenant_id=row.tenant_id,
                    policy_document_id=document_id,
                    content=chunk.content,
                    ordinal=chunk.ordinal,
                    title_path=chunk.title_path,
                    metadata_json=chunk.metadata,
                    content_hash=chunk.content_hash,
                    embedding_model=embedder.model_id,
                    embedding_dimension=embedder.dimensions,
                    token_count=len(chunk.content),
                    embedding=vector,
                )
            )

    def publish_policy(
        self, policy_id: str, tenant_id: str, actor_id: str | None = None
    ) -> PolicyVersion | None:
        try:
            with self.session_factory.begin() as session:
                row = session.scalar(
                    select(PolicyVersionRow).where(
                        PolicyVersionRow.id == policy_id,
                        PolicyVersionRow.tenant_id == tenant_id,
                    )
                )
                if row is None:
                    return None
                # A scope-level transaction lock gives every publisher the same
                # lock order and also protects scopes that do not yet have a
                # current published row.
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_key, 0))"),
                    {"scope_key": f"{tenant_id}:{row.scope}"},
                )
                row = session.scalar(
                    select(PolicyVersionRow)
                    .where(
                        PolicyVersionRow.id == policy_id,
                        PolicyVersionRow.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
                if row.status != "draft":
                    raise ValueError("Only draft policies can be published")
                row.status = "indexing"
                row.index_status = "indexing"
                row.index_error = None
                row.updated_at = utc_now()
                self._build_policy_index(session, row)
                session.execute(
                    update(PolicyVersionRow)
                    .where(
                        PolicyVersionRow.tenant_id == tenant_id,
                        PolicyVersionRow.scope == row.scope,
                        PolicyVersionRow.status == "published",
                        PolicyVersionRow.id != policy_id,
                    )
                    .values(status="superseded", updated_at=utc_now())
                )
                row.status = "published"
                row.index_status = "ready"
                row.published_by = actor_id
                row.published_at = utc_now()
                row.updated_at = utc_now()
                session.flush()
                return self._policy(row)
        except Exception as error:
            with self.session_factory.begin() as session:
                row = session.scalar(
                    select(PolicyVersionRow).where(
                        PolicyVersionRow.id == policy_id,
                        PolicyVersionRow.tenant_id == tenant_id,
                    )
                )
                if row is not None and row.status == "draft":
                    row.index_status = "failed"
                    row.index_error = str(error)[:2000]
                    row.updated_at = utc_now()
            raise

    def reindex_policy(self, policy_id: str, tenant_id: str) -> PolicyVersion | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(PolicyVersionRow)
                .where(
                    PolicyVersionRow.id == policy_id,
                    PolicyVersionRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status not in {"published", "superseded"}:
                raise ValueError("Only indexed policy versions can be reindexed")
            row.index_status = "indexing"
            self._build_policy_index(session, row)
            row.index_status = "ready"
            row.index_error = None
            row.updated_at = utc_now()
            session.flush()
            return self._policy(row)

    def list_published_policies(self, tenant_id: str) -> list[PolicyVersion]:
        now = utc_now()
        with self.session_factory() as session:
            rows = session.scalars(
                select(PolicyVersionRow).where(
                    PolicyVersionRow.tenant_id == tenant_id,
                    PolicyVersionRow.status == "published",
                    or_(
                        PolicyVersionRow.effective_from.is_(None),
                        PolicyVersionRow.effective_from <= now,
                    ),
                    or_(
                        PolicyVersionRow.effective_to.is_(None), PolicyVersionRow.effective_to > now
                    ),
                )
            )
            return [self._policy(row) for row in rows]

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
        """Run independently explainable lexical/vector queries and fuse with RRF."""
        filters = filters or {}
        vector_literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
        compact_query = re.sub(r"\s+", "", query.lower())
        lexical_terms = sorted(
            {
                compact_query[index : index + 2]
                for index in range(max(0, len(compact_query) - 1))
                if not re.search(r"\W", compact_query[index : index + 2], re.ASCII)
                or re.search(r"[\u4e00-\u9fff]", compact_query[index : index + 2])
            }
            | set(re.findall(r"[a-z0-9_]{2,}", query.lower()))
        )
        if retrieval_profile_id:
            cte_prefix = """
                WITH selected_embeddings AS MATERIALIZED (
                    SELECT * FROM policy_chunk_embedding
                    WHERE tenant_id=:tenant_id
                      AND retrieval_profile_id=:retrieval_profile_id
                      AND status='ready'
                      AND embedding_model=:embedding_model
                      AND embedding_dimension=:embedding_dimension
                )
            """
            embedding_join = (
                "JOIN selected_embeddings pce ON pce.chunk_id=pc.id "
                "AND pce.tenant_id=pc.tenant_id"
            )
            vector_expression = f"CAST(pce.embedding AS vector({int(embedding_dimension)}))"
            model_filter = (
                "AND pce.embedding_model=:embedding_model "
                "AND pce.embedding_dimension=:embedding_dimension"
            )
        else:
            cte_prefix = ""
            embedding_join = ""
            vector_expression = "pc.embedding"
            model_filter = (
                "AND pc.embedding_model=:embedding_model "
                "AND pc.embedding_dimension=:embedding_dimension"
            )
        fields = """
            pc.id AS chunk_id, pv.policy_id, pv.id AS policy_version_id,
            pv.status AS policy_status, pv.title, pv.version, pv.scope, pv.acl,
            pc.title_path, pc.ordinal, pc.content_hash, left(pc.content,400) AS excerpt,
            pv.effective_from, pv.effective_to
        """
        joins_where = f"""
            FROM policy_chunk pc {embedding_join}
            JOIN policy_document pd ON pd.id=pc.policy_document_id
            JOIN policy_version pv ON pv.id=pd.policy_version_id
            WHERE pc.tenant_id=:tenant_id AND pv.tenant_id=:tenant_id
              AND pv.status='published' {model_filter}
              AND (pv.effective_from IS NULL OR pv.effective_from<=now())
              AND (pv.effective_to IS NULL OR pv.effective_to>now())
              AND (NOT (CAST(pv.acl AS jsonb) ? 'product_id') OR (:product_id<>'' AND CAST(pv.acl AS jsonb)->'product_id' ? :product_id))
              AND (NOT (CAST(pv.acl AS jsonb) ? 'category') OR (:category<>'' AND CAST(pv.acl AS jsonb)->'category' ? :category))
              AND (NOT (CAST(pv.acl AS jsonb) ? 'region') OR (:region<>'' AND CAST(pv.acl AS jsonb)->'region' ? :region))
              AND (NOT (CAST(pv.acl AS jsonb) ? 'channel') OR (:channel<>'' AND CAST(pv.acl AS jsonb)->'channel' ? :channel))
              AND (NOT (CAST(pv.acl AS jsonb) ? 'customer_tier') OR (:customer_tier<>'' AND CAST(pv.acl AS jsonb)->'customer_tier' ? :customer_tier))
        """
        lexical_score = """
            GREATEST(
                ts_rank_cd(to_tsvector('simple',pc.content||' '||pv.title||' '||pv.scope||' '||COALESCE(pc.title_path->>0,'')),plainto_tsquery('simple',:query)),
                COALESCE((SELECT avg(CASE WHEN lower(pc.content||' '||pv.title||' '||pv.scope||' '||COALESCE(pc.title_path->>0,'')) LIKE '%'||lower(term.value)||'%' THEN 1.0 ELSE 0.0 END) FROM jsonb_array_elements_text(CAST(:lexical_terms AS jsonb)) term(value)),0)
            )
        """
        lexical_sql = text(
            f"{cte_prefix}{',' if cte_prefix else 'WITH'} lexical_candidates AS MATERIALIZED ("
            f"SELECT {fields}, {lexical_score} AS lexical_score {joins_where}"
            f") SELECT * FROM lexical_candidates WHERE lexical_score>0 "
            f"ORDER BY lexical_score DESC, chunk_id LIMIT 30"
        )
        vector_sql = text(
            f"{cte_prefix} SELECT {fields}, 1-({vector_expression}<=>CAST(:embedding AS vector({int(embedding_dimension)}))) AS semantic_score "
            f"{joins_where} AND {vector_expression} IS NOT NULL "
            f"ORDER BY {vector_expression}<=>CAST(:embedding AS vector({int(embedding_dimension)})),pc.id LIMIT 30"
        )
        parameters = {
            "tenant_id": tenant_id,
            "query": query,
            "lexical_terms": json.dumps(lexical_terms, ensure_ascii=False),
            "embedding": vector_literal,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "retrieval_profile_id": retrieval_profile_id or "",
            **{
                key: str(filters.get(key) or "")
                for key in ("product_id", "category", "region", "channel", "customer_tier")
            },
        }
        with self.session_factory() as session:
            started = time.perf_counter()
            lexical_rows = list(session.execute(lexical_sql, parameters).mappings())
            lexical_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            vector_rows = list(session.execute(vector_sql, parameters).mappings())
            vector_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        combined: dict[str, dict] = {}
        for rank, row in enumerate(lexical_rows, 1):
            combined[row["chunk_id"]] = {**dict(row), "lexical_rank": rank, "semantic_rank": None, "semantic_score": 0.0}
        for rank, row in enumerate(vector_rows, 1):
            item = combined.setdefault(
                row["chunk_id"],
                {**dict(row), "lexical_rank": None, "lexical_score": 0.0},
            )
            item["semantic_rank"] = rank
            item["semantic_score"] = float(row["semantic_score"] or 0)
        values = []
        for item in combined.values():
            item["rrf_score"] = (
                1 / (60 + item["lexical_rank"]) if item.get("lexical_rank") else 0
            ) + (1 / (60 + item["semantic_rank"]) if item.get("semantic_rank") else 0)
            item["score"] = item["rrf_score"]
            item["tenant_id"] = tenant_id
            item["matched_entities"] = [
                str(filters[key])
                for key in ("product_id", "category", "region", "channel", "customer_tier")
                if filters.get(key)
            ]
            values.append(item)
        rrf_ms = (time.perf_counter() - started) * 1000
        timings = {
            "lexical_sql_ms": round(lexical_ms, 3),
            "vector_sql_ms": round(vector_ms, 3),
            "rrf_ms": round(rrf_ms, 3),
        }
        ordered = sorted(values, key=lambda item: (-item["rrf_score"], item["chunk_id"]))[:limit]
        for item in ordered:
            item["retrieval_timings"] = timings
        return ordered

    def _search_policy_chunks_legacy(
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
        filters = filters or {}
        vector_literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
        compact_query = re.sub(r"\s+", "", query.lower())
        lexical_terms = sorted(
            {
                compact_query[index : index + 2]
                for index in range(max(0, len(compact_query) - 1))
                if not re.search(r"\W", compact_query[index : index + 2], re.ASCII)
                or re.search(r"[\u4e00-\u9fff]", compact_query[index : index + 2])
            }
            | set(re.findall(r"[a-z0-9_]{2,}", query.lower()))
        )
        if retrieval_profile_id:
            embedding_join = """
                JOIN policy_chunk_embedding pce
                  ON pce.chunk_id = pc.id
                 AND pce.tenant_id = pc.tenant_id
                 AND pce.retrieval_profile_id = :retrieval_profile_id
                 AND pce.status = 'ready'
            """
            vector_expression = "pce.embedding"
            embedding_filter = """
                  AND pce.embedding_model = :embedding_model
                  AND pce.embedding_dimension = :embedding_dimension
            """
        else:
            embedding_join = ""
            vector_expression = "pc.embedding"
            embedding_filter = """
                  AND pc.embedding_model = :embedding_model
                  AND pc.embedding_dimension = :embedding_dimension
            """
        statement = text(
            f"""
            WITH eligible AS (
                SELECT pc.id, pc.content, pc.embedding, pc.title_path,
                       pc.ordinal, pc.content_hash,
                       pv.policy_id, pv.id AS policy_version_id, pv.title, pv.version, pv.scope,
                       pv.status AS policy_status, pv.acl,
                       pv.effective_from, pv.effective_to,
                       GREATEST(
                           ts_rank_cd(
                               to_tsvector(
                                   'simple',
                                   pc.content || ' ' || pv.title || ' ' || pv.scope || ' '
                                   || COALESCE(pc.title_path->>0, '')
                               ),
                               plainto_tsquery('simple', :query)
                           ),
                           COALESCE((
                               SELECT avg(
                                   CASE WHEN lower(
                                                  pc.content || ' ' || pv.title || ' ' || pv.scope
                                                  || ' ' || COALESCE(pc.title_path->>0, '')
                                              )
                                             LIKE '%' || lower(term.value) || '%'
                                        THEN 1.0 ELSE 0.0 END
                               )
                               FROM jsonb_array_elements_text(
                                   CAST(:lexical_terms AS jsonb)
                               ) AS term(value)
                           ), 0)
                       ) AS lexical_score,
                       1 - ({vector_expression} <=> CAST(:embedding AS vector)) AS semantic_score
                FROM policy_chunk pc
                {embedding_join}
                JOIN policy_document pd ON pd.id = pc.policy_document_id
                JOIN policy_version pv ON pv.id = pd.policy_version_id
                WHERE pc.tenant_id = :tenant_id AND pv.tenant_id = :tenant_id
                  AND pv.status = 'published'
                  {embedding_filter}
                  AND (pv.effective_from IS NULL OR pv.effective_from <= now())
                  AND (pv.effective_to IS NULL OR pv.effective_to > now())
                  AND (NOT (CAST(pv.acl AS jsonb) ? 'product_id')
                       OR (:product_id <> '' AND CAST(pv.acl AS jsonb)->'product_id' ? :product_id))
                  AND (NOT (CAST(pv.acl AS jsonb) ? 'category')
                       OR (:category <> '' AND CAST(pv.acl AS jsonb)->'category' ? :category))
                  AND (NOT (CAST(pv.acl AS jsonb) ? 'region')
                       OR (:region <> '' AND CAST(pv.acl AS jsonb)->'region' ? :region))
                  AND (NOT (CAST(pv.acl AS jsonb) ? 'channel')
                       OR (:channel <> '' AND CAST(pv.acl AS jsonb)->'channel' ? :channel))
                  AND (NOT (CAST(pv.acl AS jsonb) ? 'customer_tier')
                       OR (:customer_tier <> '' AND CAST(pv.acl AS jsonb)->'customer_tier' ? :customer_tier))
            ), lexical AS (
                SELECT id, row_number() OVER (ORDER BY lexical_score DESC, id) AS lexical_rank
                FROM eligible WHERE lexical_score > 0 ORDER BY lexical_score DESC, id LIMIT 30
            ), semantic AS (
                SELECT id, row_number() OVER (ORDER BY semantic_score DESC, id) AS semantic_rank
                FROM eligible WHERE semantic_score > 0 ORDER BY semantic_score DESC, id LIMIT 30
            ), rrf AS (
                SELECT COALESCE(l.id, s.id) AS id, l.lexical_rank, s.semantic_rank,
                       COALESCE(1.0 / (60 + l.lexical_rank), 0) +
                       COALESCE(1.0 / (60 + s.semantic_rank), 0) AS rrf_score
                FROM lexical l FULL OUTER JOIN semantic s ON s.id = l.id
            )
            SELECT e.id AS chunk_id, e.policy_id, e.policy_version_id, e.policy_status,
                   e.title, e.version, e.scope, e.acl,
                   e.title_path, e.ordinal, e.content_hash, left(e.content, 400) AS excerpt,
                   rrf.lexical_rank, rrf.semantic_rank, rrf.rrf_score,
                   e.lexical_score, e.semantic_score,
                   e.effective_from, e.effective_to
            FROM rrf JOIN eligible e ON e.id = rrf.id
            ORDER BY rrf.rrf_score DESC, e.id LIMIT :limit
            """
        )
        parameters = {
            "tenant_id": tenant_id,
            "query": query,
            "lexical_terms": json.dumps(lexical_terms, ensure_ascii=False),
            "embedding": vector_literal,
            "embedding_model": embedding_model,
            "embedding_dimension": embedding_dimension,
            "retrieval_profile_id": retrieval_profile_id or "",
            "limit": limit,
            **{
                key: str(filters.get(key) or "")
                for key in ("product_id", "category", "region", "channel", "customer_tier")
            },
        }
        with self.session_factory() as session:
            rows = session.execute(statement, parameters).mappings()
            return [
                {
                    "chunk_id": row["chunk_id"],
                    "tenant_id": tenant_id,
                    "policy_id": row["policy_id"],
                    "policy_version_id": row["policy_version_id"],
                    "policy_status": row["policy_status"],
                    "title": row["title"],
                    "version": row["version"],
                    "scope": row["scope"],
                    "title_path": row["title_path"] or [],
                    "ordinal": row["ordinal"],
                    "content_hash": row["content_hash"],
                    "acl": row["acl"] or {},
                    "excerpt": row["excerpt"],
                    "lexical_rank": row["lexical_rank"],
                    "semantic_rank": row["semantic_rank"],
                    "rrf_score": float(row["rrf_score"] or 0),
                    "score": float(row["rrf_score"] or 0),
                    "lexical_score": float(row["lexical_score"] or 0),
                    "semantic_score": float(row["semantic_score"] or 0),
                    "matched_entities": [
                        str(filters[key])
                        for key in ("product_id", "category", "region", "channel", "customer_tier")
                        if filters.get(key)
                    ],
                    "effective_from": row["effective_from"],
                    "effective_to": row["effective_to"],
                }
                for row in rows
            ]

    def count_incompatible_policy_chunks(
        self,
        tenant_id: str,
        embedding_model: str,
        embedding_dimension: int,
        retrieval_profile_id: str | None = None,
    ) -> int:
        now = utc_now()
        with self.session_factory() as session:
            query = (
                select(func.count(PolicyChunkRow.id))
                .join(PolicyDocumentRow, PolicyDocumentRow.id == PolicyChunkRow.policy_document_id)
                .join(PolicyVersionRow, PolicyVersionRow.id == PolicyDocumentRow.policy_version_id)
                .where(
                    PolicyChunkRow.tenant_id == tenant_id,
                    PolicyVersionRow.tenant_id == tenant_id,
                    PolicyVersionRow.status == "published",
                    or_(PolicyVersionRow.effective_from.is_(None), PolicyVersionRow.effective_from <= now),
                    or_(PolicyVersionRow.effective_to.is_(None), PolicyVersionRow.effective_to > now),
                )
            )
            if retrieval_profile_id:
                query = query.outerjoin(
                    PolicyChunkEmbeddingRow,
                    (PolicyChunkEmbeddingRow.chunk_id == PolicyChunkRow.id)
                    & (PolicyChunkEmbeddingRow.retrieval_profile_id == retrieval_profile_id),
                ).where(
                    or_(
                        PolicyChunkEmbeddingRow.id.is_(None),
                        PolicyChunkEmbeddingRow.embedding_model != embedding_model,
                        PolicyChunkEmbeddingRow.embedding_dimension != embedding_dimension,
                    )
                )
            else:
                query = query.where(
                    or_(
                        PolicyChunkRow.embedding_model != embedding_model,
                        PolicyChunkRow.embedding_dimension != embedding_dimension,
                    )
                )
            return int(session.scalar(query) or 0)

    @staticmethod
    def _snapshot(row: RetrievalSnapshotRow) -> RetrievalSnapshot:
        return RetrievalSnapshot(
            retrieval_snapshot_id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            query_redacted=row.query_redacted,
            query_hash=row.query_hash,
            filters=row.filters,
            candidates=row.candidates,
            citations=row.citations,
            decision=row.decision,
            reason_code=row.reason_code,
            confidence=row.confidence,
            retriever_version=row.retriever_version,
            embedding_model=row.embedding_model,
            embedding_dimension=row.embedding_dimension,
            normalization_version=row.normalization_version,
            diagnostics=row.diagnostics or {},
            gate_version=row.gate_version,
            latency_ms=row.latency_ms,
            created_at=row.created_at,
        )

    def save_retrieval_snapshot(self, snapshot: RetrievalSnapshot) -> RetrievalSnapshot:
        with self.session_factory.begin() as session:
            session.add(
                RetrievalSnapshotRow(
                    id=snapshot.retrieval_snapshot_id,
                    tenant_id=snapshot.tenant_id,
                    conversation_id=snapshot.conversation_id,
                    message_id=snapshot.message_id,
                    query_redacted=snapshot.query_redacted,
                    query_hash=snapshot.query_hash,
                    filters=snapshot.filters,
                    candidates=snapshot.candidates,
                    citations=snapshot.citations,
                    decision=snapshot.decision,
                    reason_code=snapshot.reason_code,
                    confidence=snapshot.confidence,
                    retriever_version=snapshot.retriever_version,
                    embedding_model=snapshot.embedding_model,
                    embedding_dimension=snapshot.embedding_dimension,
                    normalization_version=snapshot.normalization_version,
                    diagnostics=snapshot.diagnostics,
                    gate_version=snapshot.gate_version,
                    latency_ms=snapshot.latency_ms,
                    created_at=snapshot.created_at,
                )
            )
        return snapshot

    def get_retrieval_snapshot(
        self, snapshot_id: str, tenant_id: str
    ) -> RetrievalSnapshot | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RetrievalSnapshotRow).where(
                    RetrievalSnapshotRow.id == snapshot_id,
                    RetrievalSnapshotRow.tenant_id == tenant_id,
                )
            )
            return self._snapshot(row) if row else None

    def import_evaluation_cases(self, cases: list[EvaluationCase]) -> int:
        if not cases:
            return 0
        with self.session_factory.begin() as session:
            first = cases[0]
            dataset_row = session.scalar(
                select(EvaluationDatasetRow)
                .where(
                    EvaluationDatasetRow.tenant_id == first.tenant_id,
                    EvaluationDatasetRow.dataset == first.dataset,
                    EvaluationDatasetRow.version == first.dataset_version,
                )
                .with_for_update()
            )
            if dataset_row and dataset_row.status == "frozen":
                raise ValueError("DATASET_FROZEN")
            if dataset_row is None:
                dataset_row = EvaluationDatasetRow(
                    tenant_id=first.tenant_id,
                    dataset=first.dataset,
                    version=first.dataset_version,
                    status="draft",
                )
                session.add(dataset_row)
            for case in cases:
                existing = session.scalar(
                    select(EvaluationCaseRow).where(
                        EvaluationCaseRow.tenant_id == case.tenant_id,
                        EvaluationCaseRow.dataset == case.dataset,
                        EvaluationCaseRow.dataset_version == case.dataset_version,
                        EvaluationCaseRow.name == case.name,
                    )
                )
                values = {
                    "case_type": case.case_type,
                    "input_data": case.input_data,
                    "expected": case.expected,
                    "tags": case.tags,
                    "active": case.active,
                }
                if existing:
                    for key, value in values.items():
                        setattr(existing, key, value)
                else:
                    session.add(
                        EvaluationCaseRow(
                            id=case.evaluation_case_id,
                            tenant_id=case.tenant_id,
                            name=case.name,
                            dataset=case.dataset,
                            dataset_version=case.dataset_version,
                            **values,
                        )
                    )
            session.flush()
            rows = list(
                session.scalars(
                    select(EvaluationCaseRow)
                    .where(
                        EvaluationCaseRow.tenant_id == first.tenant_id,
                        EvaluationCaseRow.dataset == first.dataset,
                        EvaluationCaseRow.dataset_version == first.dataset_version,
                        EvaluationCaseRow.active.is_(True),
                    )
                    .order_by(EvaluationCaseRow.name)
                )
            )
            dataset_row.case_count = len(rows)
            dataset_row.content_hash = hashlib.sha256(
                json.dumps(
                    [
                        {
                            "name": row.name,
                            "case_type": row.case_type,
                            "input_data": row.input_data,
                            "expected": row.expected,
                            "tags": row.tags,
                        }
                        for row in rows
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        return len(cases)

    @staticmethod
    def _evaluation_dataset(row: EvaluationDatasetRow) -> EvaluationDataset:
        return EvaluationDataset(
            tenant_id=row.tenant_id,
            dataset=row.dataset,
            version=row.version,
            status=row.status,
            case_count=row.case_count,
            content_hash=row.content_hash,
            created_at=row.created_at,
            frozen_at=row.frozen_at,
        )

    def get_evaluation_dataset(
        self, tenant_id: str, dataset: str, version: str
    ) -> EvaluationDataset | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(EvaluationDatasetRow).where(
                    EvaluationDatasetRow.tenant_id == tenant_id,
                    EvaluationDatasetRow.dataset == dataset,
                    EvaluationDatasetRow.version == version,
                )
            )
            return self._evaluation_dataset(row) if row else None

    def list_evaluation_datasets(self, tenant_id: str) -> list[EvaluationDataset]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EvaluationDatasetRow)
                .where(EvaluationDatasetRow.tenant_id == tenant_id)
                .order_by(EvaluationDatasetRow.created_at.desc())
            )
            return [self._evaluation_dataset(row) for row in rows]

    def freeze_evaluation_dataset(
        self, tenant_id: str, dataset: str, version: str
    ) -> EvaluationDataset | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(EvaluationDatasetRow)
                .where(
                    EvaluationDatasetRow.tenant_id == tenant_id,
                    EvaluationDatasetRow.dataset == dataset,
                    EvaluationDatasetRow.version == version,
                )
                .with_for_update()
            )
            if row is None:
                return None
            row.status = "frozen"
            row.frozen_at = utc_now()
            session.flush()
            return self._evaluation_dataset(row)

    def list_evaluation_cases(
        self, tenant_id: str, dataset: str, dataset_version: str
    ) -> list[EvaluationCase]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EvaluationCaseRow).where(
                    EvaluationCaseRow.tenant_id == tenant_id,
                    EvaluationCaseRow.dataset == dataset,
                    EvaluationCaseRow.dataset_version == dataset_version,
                    EvaluationCaseRow.active.is_(True),
                )
            )
            return [
                EvaluationCase(
                    evaluation_case_id=row.id,
                    tenant_id=row.tenant_id,
                    dataset=row.dataset,
                    dataset_version=row.dataset_version,
                    name=row.name,
                    case_type=row.case_type,
                    input_data=row.input_data,
                    expected=row.expected,
                    tags=row.tags or [],
                    active=row.active,
                )
                for row in rows
            ]

    def save_evaluation_run(self, run: EvaluationRun) -> EvaluationRun:
        with self.session_factory.begin() as session:
            session.merge(
                EvaluationRunRow(
                    id=run.evaluation_run_id,
                    tenant_id=run.tenant_id,
                    status=run.status,
                    dataset=run.dataset,
                    dataset_version=run.dataset_version,
                    config_snapshot=run.config_snapshot,
                    summary=run.summary,
                    quality_status=run.quality_status,
                    quality_failures=run.quality_failures,
                    metric_schema_version=run.metric_schema_version,
                    query_analyzer_version=run.query_analyzer_version,
                    scope_match_version=run.scope_match_version,
                    threshold_grid_version=run.threshold_grid_version,
                    created_at=run.created_at,
                    completed_at=run.completed_at,
                )
            )
        return run

    def get_evaluation_run(self, run_id: str, tenant_id: str) -> EvaluationRun | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(EvaluationRunRow).where(
                    EvaluationRunRow.id == run_id, EvaluationRunRow.tenant_id == tenant_id
                )
            )
            if row is None:
                return None
            return EvaluationRun(
                evaluation_run_id=row.id,
                tenant_id=row.tenant_id,
                status=row.status,
                dataset=row.dataset,
                dataset_version=row.dataset_version,
                config_snapshot=row.config_snapshot,
                summary=row.summary,
                quality_status=row.quality_status,
                quality_failures=row.quality_failures or [],
                metric_schema_version=row.metric_schema_version,
                query_analyzer_version=row.query_analyzer_version,
                scope_match_version=row.scope_match_version,
                threshold_grid_version=row.threshold_grid_version,
                created_at=row.created_at,
                completed_at=row.completed_at,
            )

    def list_evaluation_runs(self, tenant_id: str) -> list[EvaluationRun]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EvaluationRunRow)
                .where(EvaluationRunRow.tenant_id == tenant_id)
                .order_by(EvaluationRunRow.created_at.desc())
            )
            return [
                EvaluationRun(
                    evaluation_run_id=row.id,
                    tenant_id=row.tenant_id,
                    status=row.status,
                    dataset=row.dataset,
                    dataset_version=row.dataset_version,
                    config_snapshot=row.config_snapshot,
                    summary=row.summary,
                    quality_status=row.quality_status,
                    quality_failures=row.quality_failures or [],
                    metric_schema_version=row.metric_schema_version,
                    query_analyzer_version=row.query_analyzer_version,
                    scope_match_version=row.scope_match_version,
                    threshold_grid_version=row.threshold_grid_version,
                    created_at=row.created_at,
                    completed_at=row.completed_at,
                )
                for row in rows
            ]

    def save_evaluation_results(self, results: list[EvaluationResult]) -> None:
        with self.session_factory.begin() as session:
            for result in results:
                session.merge(
                    EvaluationResultRow(
                        id=result.evaluation_result_id,
                        tenant_id=result.tenant_id,
                        run_id=result.run_id,
                        case_id=result.case_id,
                        passed=result.passed,
                        metrics=result.metrics,
                        details=result.details,
                    )
                )

    def list_evaluation_results(
        self, run_id: str, tenant_id: str
    ) -> list[EvaluationResult]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EvaluationResultRow).where(
                    EvaluationResultRow.run_id == run_id,
                    EvaluationResultRow.tenant_id == tenant_id,
                )
            )
            return [
                EvaluationResult(
                    evaluation_result_id=row.id,
                    tenant_id=row.tenant_id,
                    run_id=row.run_id,
                    case_id=row.case_id,
                    passed=row.passed,
                    metrics=row.metrics,
                    details=row.details,
                )
                for row in rows
            ]

    @staticmethod
    def _retrieval_profile(row: RetrievalProfileRow) -> RetrievalProfile:
        return RetrievalProfile(
            retrieval_profile_id=row.id,
            tenant_id=row.tenant_id,
            provider=row.provider,
            embedding_model=row.embedding_model,
            embedding_dimension=row.embedding_dimension,
            retriever_version=row.retriever_version,
            gate_version=row.gate_version,
            normalization_version=row.normalization_version,
            status=row.status,
            indexed_chunks=row.indexed_chunks,
            total_chunks=row.total_chunks,
            evaluation_run_id=row.evaluation_run_id,
            gate_config=row.gate_config or {},
            quality_report=row.quality_report or {},
            rejected_reason=row.rejected_reason,
            parent_profile_id=row.parent_profile_id,
            calibration_run_id=row.calibration_run_id,
            holdout_run_id=row.holdout_run_id,
            calibration_dataset=row.calibration_dataset or "",
            validation_datasets=row.validation_datasets or [],
            config_hash=row.config_hash or "",
            activated_by=row.activated_by,
            activated_at=row.activated_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def save_retrieval_profile(self, profile: RetrievalProfile) -> RetrievalProfile:
        with self.session_factory.begin() as session:
            session.merge(
                RetrievalProfileRow(
                    id=profile.retrieval_profile_id,
                    tenant_id=profile.tenant_id,
                    provider=profile.provider,
                    embedding_model=profile.embedding_model,
                    embedding_dimension=profile.embedding_dimension,
                    retriever_version=profile.retriever_version,
                    gate_version=profile.gate_version,
                    normalization_version=profile.normalization_version,
                    status=profile.status,
                    indexed_chunks=profile.indexed_chunks,
                    total_chunks=profile.total_chunks,
                    evaluation_run_id=profile.evaluation_run_id,
                    gate_config=profile.gate_config,
                    quality_report=profile.quality_report,
                    rejected_reason=profile.rejected_reason,
                    parent_profile_id=profile.parent_profile_id,
                    calibration_run_id=profile.calibration_run_id,
                    holdout_run_id=profile.holdout_run_id,
                    calibration_dataset=profile.calibration_dataset,
                    validation_datasets=profile.validation_datasets,
                    config_hash=profile.config_hash,
                    activated_by=profile.activated_by,
                    activated_at=profile.activated_at,
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                )
            )
        return profile

    def get_retrieval_profile(
        self, profile_id: str, tenant_id: str
    ) -> RetrievalProfile | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(RetrievalProfileRow).where(
                    RetrievalProfileRow.id == profile_id,
                    RetrievalProfileRow.tenant_id == tenant_id,
                )
            )
            return self._retrieval_profile(row) if row else None

    def list_retrieval_profiles(self, tenant_id: str) -> list[RetrievalProfile]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(RetrievalProfileRow)
                .where(RetrievalProfileRow.tenant_id == tenant_id)
                .order_by(RetrievalProfileRow.created_at)
            )
            return [self._retrieval_profile(row) for row in rows]

    def reindex_retrieval_profile(
        self, profile_id: str, tenant_id: str
    ) -> RetrievalProfile | None:
        with self.session_factory.begin() as session:
            profile = session.scalar(
                select(RetrievalProfileRow)
                .where(
                    RetrievalProfileRow.id == profile_id,
                    RetrievalProfileRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if profile is None:
                return None
            if profile.id in {"bge-m3-v1", "bge-m3-v2"} or profile.status == "rejected":
                raise ValueError("Rejected retrieval profiles are immutable")
            profile.status = "building"
            chunks = list(
                session.execute(
                    select(
                        PolicyChunkRow.id,
                        PolicyChunkRow.content,
                        PolicyChunkRow.content_hash,
                    )
                    .join(
                        PolicyDocumentRow,
                        PolicyDocumentRow.id == PolicyChunkRow.policy_document_id,
                    )
                    .join(
                        PolicyVersionRow,
                        PolicyVersionRow.id == PolicyDocumentRow.policy_version_id,
                    )
                    .where(
                        PolicyChunkRow.tenant_id == tenant_id,
                        PolicyVersionRow.status == "published",
                    )
                    .order_by(PolicyChunkRow.id)
                )
            )
            profile.total_chunks = len(chunks)
            profile.updated_at = utc_now()
            provider_name = profile.provider
            model = profile.embedding_model
            dimension = profile.embedding_dimension
        provider = embedding_provider_for(provider_name, model, dimension)
        for start in range(0, len(chunks), 32):
            batch = chunks[start : start + 32]
            with self.session_factory() as session:
                existing = {
                    row.chunk_id: row
                    for row in session.scalars(
                        select(PolicyChunkEmbeddingRow).where(
                            PolicyChunkEmbeddingRow.tenant_id == tenant_id,
                            PolicyChunkEmbeddingRow.retrieval_profile_id == profile_id,
                            PolicyChunkEmbeddingRow.chunk_id.in_([row.id for row in batch]),
                        )
                    )
                }
            pending = [
                row
                for row in batch
                if row.id not in existing
                or existing[row.id].content_hash != row.content_hash
                or existing[row.id].embedding_model != model
                or existing[row.id].embedding_dimension != dimension
                or existing[row.id].status != "ready"
            ]
            if not pending:
                continue
            vectors = provider.embed_documents([row.content for row in pending])
            with self.session_factory.begin() as session:
                for chunk, vector in zip(pending, vectors, strict=True):
                    value = session.scalar(
                        select(PolicyChunkEmbeddingRow).where(
                            PolicyChunkEmbeddingRow.tenant_id == tenant_id,
                            PolicyChunkEmbeddingRow.chunk_id == chunk.id,
                            PolicyChunkEmbeddingRow.retrieval_profile_id == profile_id,
                        )
                    )
                    if value is None:
                        value = PolicyChunkEmbeddingRow(
                            id=f"pce_{uuid4().hex}",
                            tenant_id=tenant_id,
                            chunk_id=chunk.id,
                            retrieval_profile_id=profile_id,
                            embedding_model=model,
                            embedding_dimension=dimension,
                            content_hash=chunk.content_hash,
                        )
                        session.add(value)
                    value.embedding_model = model
                    value.embedding_dimension = dimension
                    value.content_hash = chunk.content_hash
                    value.embedding = vector
                    value.status = "ready"
                    value.error = None
                    value.updated_at = utc_now()
        with self.session_factory.begin() as session:
            profile = session.scalar(
                select(RetrievalProfileRow)
                .where(
                    RetrievalProfileRow.id == profile_id,
                    RetrievalProfileRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if profile is None:
                return None
            ready = int(
                session.scalar(
                    select(func.count(PolicyChunkEmbeddingRow.id)).where(
                        PolicyChunkEmbeddingRow.tenant_id == tenant_id,
                        PolicyChunkEmbeddingRow.retrieval_profile_id == profile_id,
                        PolicyChunkEmbeddingRow.chunk_id.in_([row.id for row in chunks]),
                        PolicyChunkEmbeddingRow.status == "ready",
                    )
                )
                or 0
            )
            profile.indexed_chunks = ready
            profile.status = "ready" if ready == profile.total_chunks else "building"
            profile.updated_at = utc_now()
            session.flush()
            return self._retrieval_profile(profile)

    def activate_retrieval_profile(
        self, profile_id: str, tenant_id: str, evaluation_run_id: str, actor_id: str
    ) -> RetrievalProfile | None:
        with self.session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"retrieval-profile:{tenant_id}"},
            )
            profile = session.scalar(
                select(RetrievalProfileRow).where(
                    RetrievalProfileRow.id == profile_id,
                    RetrievalProfileRow.tenant_id == tenant_id,
                )
            )
            if profile is None:
                return None
            if profile.id == "bge-m3-v1" or profile.status == "rejected":
                raise ValueError("Rejected retrieval profiles are immutable and cannot be activated")
            run = session.scalar(
                select(EvaluationRunRow).where(
                    EvaluationRunRow.id == evaluation_run_id,
                    EvaluationRunRow.tenant_id == tenant_id,
                )
            )
            if profile.status not in {"ready", "active"} or profile.indexed_chunks != profile.total_chunks:
                raise ValueError("Retrieval profile is not fully indexed")
            expected_gold = (
                profile.validation_datasets[0]
                if profile.validation_datasets
                else "week4_gold/v3"
            )
            if (
                run is None
                or run.quality_status != "passed"
                or f"{run.dataset}/{run.dataset_version}" != expected_gold
                or run.metric_schema_version != "v3"
                or run.config_snapshot.get("retrieval_profile_id") != profile_id
            ):
                raise ValueError("A passed week4_gold/v3 run for this profile is required")
            holdout = session.scalar(
                select(EvaluationRunRow).where(
                    EvaluationRunRow.id == profile.holdout_run_id,
                    EvaluationRunRow.tenant_id == tenant_id,
                )
            ) if profile.holdout_run_id else None
            calibration = session.scalar(
                select(EvaluationRunRow).where(
                    EvaluationRunRow.id == profile.calibration_run_id,
                    EvaluationRunRow.tenant_id == tenant_id,
                )
            ) if profile.calibration_run_id else None
            if profile.gate_version == "v3" and (
                holdout is None
                or holdout.quality_status != "passed"
                or holdout.dataset != "week4_hard_negative"
                or holdout.dataset_version != "v1"
                or holdout.config_snapshot.get("retrieval_profile_id") != profile_id
                or not profile.calibration_run_id
                or profile.normalization_version != "query-normalizer-v3"
                or profile.retriever_version != "hybrid-rrf-v3"
                or (profile.gate_config or {}).get("gate_version") != "v3"
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
            ):
                raise ValueError("Passed calibration and hard-negative holdout runs are required")
            if profile.gate_version == "v4" and (
                calibration is None
                or calibration.quality_status != "passed"
                or f"{calibration.dataset}/{calibration.dataset_version}"
                != profile.calibration_dataset
                or calibration.config_snapshot.get("retrieval_profile_id") != profile_id
                or holdout is None
                or holdout.quality_status != "passed"
                or len(profile.validation_datasets or []) != 2
                or f"{run.dataset}/{run.dataset_version}" != profile.validation_datasets[0]
                or f"{holdout.dataset}/{holdout.dataset_version}" != profile.validation_datasets[1]
                or not profile.calibration_dataset
                or not profile.config_hash
                or run.config_snapshot.get("config_hash") != profile.config_hash
                or holdout.config_snapshot.get("config_hash") != profile.config_hash
                or profile.normalization_version != "query-normalizer-v4"
                or profile.retriever_version != "hybrid-rrf-v4"
                or (profile.gate_config or {}).get("gate_version") != "v4"
                or calibration is None
                or run.config_snapshot.get("policy_corpus_hash")
                != holdout.config_snapshot.get("policy_corpus_hash")
                or run.config_snapshot.get("policy_corpus_hash")
                != calibration.config_snapshot.get("policy_corpus_hash")
            ):
                raise ValueError("Gate v4 requires matching calibration, gold and hard-negative v2 runs")
            session.execute(
                update(RetrievalProfileRow)
                .where(
                    RetrievalProfileRow.tenant_id == tenant_id,
                    RetrievalProfileRow.status == "active",
                    RetrievalProfileRow.id != profile_id,
                )
                .values(status="ready", updated_at=utc_now())
            )
            profile.status = "active"
            profile.evaluation_run_id = evaluation_run_id
            profile.activated_by = actor_id
            profile.activated_at = utc_now()
            profile.updated_at = utc_now()
            session.flush()
            return self._retrieval_profile(profile)

    def rollback_hash_retrieval_profile(
        self, profile_id: str, tenant_id: str, actor_id: str
    ) -> RetrievalProfile | None:
        with self.session_factory.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"retrieval-profile:{tenant_id}"},
            )
            profile = session.scalar(
                select(RetrievalProfileRow).where(
                    RetrievalProfileRow.id == profile_id,
                    RetrievalProfileRow.tenant_id == tenant_id,
                )
            )
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
            session.execute(
                update(RetrievalProfileRow)
                .where(
                    RetrievalProfileRow.tenant_id == tenant_id,
                    RetrievalProfileRow.status == "active",
                    RetrievalProfileRow.id != profile_id,
                )
                .values(status="ready", updated_at=utc_now())
            )
            profile.status = "active"
            profile.activated_by = actor_id
            profile.activated_at = utc_now()
            profile.updated_at = utc_now()
            session.flush()
            return self._retrieval_profile(profile)

    @staticmethod
    def _llm_trace(row: LlmTraceRow) -> LlmTrace:
        return LlmTrace(
            llm_trace_id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            task_id=row.task_id,
            request_id=row.request_id,
            provider=row.provider,
            model=row.model,
            prompt_version=row.prompt_version,
            prompt_hash=row.prompt_hash,
            call_type=row.call_type,
            status=row.status,
            error_code=row.error_code,
            input_redacted=row.input_redacted,
            output_redacted=row.output_redacted,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            latency_ms=row.latency_ms,
            fallback_used=row.fallback_used,
            fallback_reason=row.fallback_reason,
            agent_run_id=row.agent_run_id,
            budget=row.budget_json,
            allowed_citation_ids=row.allowed_citation_ids or [],
            created_at=row.created_at,
        )

    def save_llm_trace(self, trace: LlmTrace) -> LlmTrace:
        with self.session_factory.begin() as session:
            session.merge(
                LlmTraceRow(
                    id=trace.llm_trace_id,
                    tenant_id=trace.tenant_id,
                    conversation_id=trace.conversation_id,
                    message_id=trace.message_id,
                    task_id=trace.task_id,
                    request_id=trace.request_id,
                    provider=trace.provider,
                    model=trace.model,
                    prompt_version=trace.prompt_version,
                    prompt_hash=trace.prompt_hash,
                    call_type=trace.call_type,
                    status=trace.status,
                    error_code=trace.error_code,
                    input_redacted=trace.input_redacted,
                    output_redacted=trace.output_redacted,
                    input_tokens=trace.input_tokens,
                    output_tokens=trace.output_tokens,
                    latency_ms=trace.latency_ms,
                    fallback_used=trace.fallback_used,
                    fallback_reason=trace.fallback_reason,
                    agent_run_id=trace.agent_run_id,
                    budget_json=trace.budget,
                    allowed_citation_ids=trace.allowed_citation_ids,
                    created_at=trace.created_at,
                )
            )
        return trace

    def get_llm_trace(self, trace_id: str, tenant_id: str) -> LlmTrace | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(LlmTraceRow).where(
                    LlmTraceRow.id == trace_id, LlmTraceRow.tenant_id == tenant_id
                )
            )
            return self._llm_trace(row) if row else None

    def list_llm_traces(self, tenant_id: str) -> list[LlmTrace]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(LlmTraceRow)
                .where(LlmTraceRow.tenant_id == tenant_id)
                .order_by(LlmTraceRow.created_at.desc())
            )
            return [self._llm_trace(row) for row in rows]

    @staticmethod
    def _agent_run(row: AgentRunRow) -> AgentRun:
        return AgentRun(
            agent_run_id=row.id,
            tenant_id=row.tenant_id,
            conversation_id=row.conversation_id,
            message_id=row.message_id,
            task_id=row.task_id,
            request_id=row.request_id,
            provider=row.provider,
            model=row.model,
            prompt_version=row.prompt_version,
            graph_version=row.graph_version,
            input_hash=row.input_hash,
            intent=row.intent,
            risk=row.risk,
            result_kind=row.result_kind,
            steps=row.steps,
            llm_calls=row.llm_calls,
            tool_calls=row.tool_calls,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            latency_ms=row.latency_ms,
            fallback_used=row.fallback_used,
            fallback_reason=row.fallback_reason,
            error_code=row.error_code,
            retrieval_snapshot_id=row.retrieval_snapshot_id,
            action_plan_id=row.action_plan_id,
            handoff_id=row.handoff_id,
            estimated_cost=row.estimated_cost,
            input_redacted=row.input_redacted,
            output_redacted=row.output_redacted,
            created_at=row.created_at,
        )

    def save_agent_run(self, run: AgentRun) -> AgentRun:
        with self.session_factory.begin() as session:
            session.merge(
                AgentRunRow(
                    id=run.agent_run_id,
                    tenant_id=run.tenant_id,
                    conversation_id=run.conversation_id,
                    message_id=run.message_id,
                    task_id=run.task_id,
                    request_id=run.request_id,
                    provider=run.provider,
                    model=run.model,
                    prompt_version=run.prompt_version,
                    graph_version=run.graph_version,
                    input_hash=run.input_hash,
                    intent=run.intent,
                    risk=run.risk,
                    result_kind=run.result_kind,
                    steps=run.steps,
                    llm_calls=run.llm_calls,
                    tool_calls=run.tool_calls,
                    input_tokens=run.input_tokens,
                    output_tokens=run.output_tokens,
                    latency_ms=run.latency_ms,
                    fallback_used=run.fallback_used,
                    fallback_reason=run.fallback_reason,
                    error_code=run.error_code,
                    retrieval_snapshot_id=run.retrieval_snapshot_id,
                    action_plan_id=run.action_plan_id,
                    handoff_id=run.handoff_id,
                    estimated_cost=run.estimated_cost,
                    input_redacted=run.input_redacted,
                    output_redacted=run.output_redacted,
                    created_at=run.created_at,
                )
            )
        return run

    def get_agent_run(self, run_id: str, tenant_id: str) -> AgentRun | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id
                )
            )
            return self._agent_run(row) if row else None

    def list_agent_runs(self, tenant_id: str) -> list[AgentRun]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(AgentRunRow)
                .where(AgentRunRow.tenant_id == tenant_id)
                .order_by(AgentRunRow.created_at.desc())
            )
            return [self._agent_run(row) for row in rows]

    @staticmethod
    def _pilot_release(row: PilotReleaseRow) -> PilotRelease:
        return PilotRelease(
            pilot_id=row.id,
            tenant_id=row.tenant_id,
            version=row.version,
            status=row.status,
            git_sha=row.git_sha,
            image_digests=row.image_digests or {},
            llm_model=row.llm_model,
            prompt_version=row.prompt_version,
            graph_version=row.graph_version,
            retrieval_profile_id=row.retrieval_profile_id,
            policy_corpus_hash=row.policy_corpus_hash,
            dataset_version=row.dataset_version,
            dataset_hash=row.dataset_hash,
            connector_mode=row.connector_mode,
            alembic_revision=row.alembic_revision,
            input_cost_per_million=row.input_cost_per_million,
            output_cost_per_million=row.output_cost_per_million,
            config_hash=row.config_hash,
            created_by=row.created_by,
            created_at=row.created_at,
            frozen_at=row.frozen_at,
            closed_at=row.closed_at,
        )

    @staticmethod
    def _pilot_participant(row: PilotParticipantRow) -> PilotParticipant:
        return PilotParticipant(
            participant_id=row.id,
            tenant_id=row.tenant_id,
            pilot_id=row.pilot_id,
            participant_code=row.participant_code,
            role=row.role,
            subject=row.subject,
            active=row.active,
            consented_at=row.consented_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _pilot_task(row: PilotTaskAssignmentRow) -> PilotTaskAssignment:
        return PilotTaskAssignment(
            assignment_id=row.id,
            tenant_id=row.tenant_id,
            pilot_id=row.pilot_id,
            participant_code=row.participant_code,
            scenario=row.scenario,
            expected_outcome=row.expected_outcome,
            status=row.status,
            conversation_id=row.conversation_id,
            handoff_id=row.handoff_id,
            ticket_id=row.ticket_id,
            failure_category=row.failure_category,
            notes_redacted=row.notes_redacted,
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
        )

    @staticmethod
    def _pilot_feedback(row: PilotFeedbackRow) -> PilotFeedback:
        return PilotFeedback(
            feedback_id=row.id,
            tenant_id=row.tenant_id,
            pilot_id=row.pilot_id,
            assignment_id=row.assignment_id,
            conversation_id=row.conversation_id,
            participant_code=row.participant_code,
            completed=row.completed,
            rating=row.rating,
            confusion_tags=row.confusion_tags or [],
            comment_redacted=row.comment_redacted,
            created_at=row.created_at,
        )

    @staticmethod
    def _pilot_snapshot(row: PilotMetricSnapshotRow) -> PilotMetricSnapshot:
        return PilotMetricSnapshot(
            snapshot_id=row.id,
            tenant_id=row.tenant_id,
            pilot_id=row.pilot_id,
            metrics=row.metrics or {},
            safety=row.safety or {},
            created_at=row.created_at,
        )

    def create_pilot(
        self,
        release: PilotRelease,
        participants: list[PilotParticipant],
        tasks: list[PilotTaskAssignment],
    ) -> PilotRelease:
        try:
            with self.session_factory.begin() as session:
                session.add(
                    PilotReleaseRow(
                        id=release.pilot_id,
                        tenant_id=release.tenant_id,
                        version=release.version,
                        status=release.status,
                        git_sha=release.git_sha,
                        image_digests=release.image_digests,
                        llm_model=release.llm_model,
                        prompt_version=release.prompt_version,
                        graph_version=release.graph_version,
                        retrieval_profile_id=release.retrieval_profile_id,
                        policy_corpus_hash=release.policy_corpus_hash,
                        dataset_version=release.dataset_version,
                        dataset_hash=release.dataset_hash,
                        connector_mode=release.connector_mode,
                        alembic_revision=release.alembic_revision,
                        input_cost_per_million=release.input_cost_per_million,
                        output_cost_per_million=release.output_cost_per_million,
                        config_hash=release.config_hash,
                        created_by=release.created_by,
                        created_at=release.created_at,
                    )
                )
                session.add_all(
                    [
                        PilotParticipantRow(
                            id=item.participant_id,
                            tenant_id=item.tenant_id,
                            pilot_id=item.pilot_id,
                            participant_code=item.participant_code,
                            role=item.role,
                            subject=item.subject,
                            active=item.active,
                            consented_at=item.consented_at,
                            created_at=item.created_at,
                        )
                        for item in participants
                    ]
                )
                session.add_all(
                    [
                        PilotTaskAssignmentRow(
                            id=item.assignment_id,
                            tenant_id=item.tenant_id,
                            pilot_id=item.pilot_id,
                            participant_code=item.participant_code,
                            scenario=item.scenario,
                            expected_outcome=item.expected_outcome,
                            status=item.status,
                            conversation_id=item.conversation_id,
                            handoff_id=item.handoff_id,
                            ticket_id=item.ticket_id,
                            failure_category=item.failure_category,
                            notes_redacted=item.notes_redacted,
                            started_at=item.started_at,
                            completed_at=item.completed_at,
                            created_at=item.created_at,
                        )
                        for item in tasks
                    ]
                )
        except IntegrityError as exc:
            raise ValueError("Pilot version, participant or task already exists") from exc
        return release

    def get_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(PilotReleaseRow).where(
                    PilotReleaseRow.id == pilot_id,
                    PilotReleaseRow.tenant_id == tenant_id,
                )
            )
            return self._pilot_release(row) if row else None

    def list_pilot_participants(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotParticipant]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(PilotParticipantRow)
                .where(
                    PilotParticipantRow.pilot_id == pilot_id,
                    PilotParticipantRow.tenant_id == tenant_id,
                )
                .order_by(PilotParticipantRow.participant_code)
            )
            return [self._pilot_participant(row) for row in rows]

    def find_active_pilot_participant(
        self, tenant_id: str, subject: str
    ) -> PilotParticipant | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(PilotParticipantRow)
                .join(PilotReleaseRow, PilotReleaseRow.id == PilotParticipantRow.pilot_id)
                .where(
                    PilotParticipantRow.tenant_id == tenant_id,
                    PilotParticipantRow.subject == subject,
                    PilotParticipantRow.active.is_(True),
                    PilotReleaseRow.status == "frozen",
                )
                .order_by(PilotReleaseRow.frozen_at.desc())
            )
            return self._pilot_participant(row) if row else None

    def list_pilot_tasks(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotTaskAssignment]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(PilotTaskAssignmentRow)
                .where(
                    PilotTaskAssignmentRow.pilot_id == pilot_id,
                    PilotTaskAssignmentRow.tenant_id == tenant_id,
                )
                .order_by(
                    PilotTaskAssignmentRow.participant_code,
                    PilotTaskAssignmentRow.created_at,
                )
            )
            return [self._pilot_task(row) for row in rows]

    def freeze_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(PilotReleaseRow)
                .where(
                    PilotReleaseRow.id == pilot_id,
                    PilotReleaseRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status != "draft":
                raise ValueError("Only draft pilots can be frozen")
            row.status = "frozen"
            row.frozen_at = utc_now()
            session.flush()
            return self._pilot_release(row)

    def close_pilot(self, pilot_id: str, tenant_id: str) -> PilotRelease | None:
        with self.session_factory.begin() as session:
            row = session.scalar(
                select(PilotReleaseRow)
                .where(
                    PilotReleaseRow.id == pilot_id,
                    PilotReleaseRow.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if row is None:
                return None
            if row.status != "frozen":
                raise ValueError("Only frozen pilots can be closed")
            row.status = "closed"
            row.closed_at = utc_now()
            session.flush()
            return self._pilot_release(row)

    def record_pilot_feedback(self, feedback: PilotFeedback) -> PilotFeedback:
        try:
            with self.session_factory.begin() as session:
                if feedback.assignment_id:
                    task = session.scalar(
                        select(PilotTaskAssignmentRow)
                        .where(
                            PilotTaskAssignmentRow.id == feedback.assignment_id,
                            PilotTaskAssignmentRow.tenant_id == feedback.tenant_id,
                            PilotTaskAssignmentRow.pilot_id == feedback.pilot_id,
                            PilotTaskAssignmentRow.participant_code
                            == feedback.participant_code,
                        )
                        .with_for_update()
                    )
                    if task is None:
                        raise ValueError("Pilot task not found")
                    task.conversation_id = feedback.conversation_id
                    task.status = "completed" if feedback.completed else "failed"
                    task.started_at = task.started_at or feedback.created_at
                    task.completed_at = feedback.created_at
                    task.notes_redacted = feedback.comment_redacted
                session.add(
                    PilotFeedbackRow(
                        id=feedback.feedback_id,
                        tenant_id=feedback.tenant_id,
                        pilot_id=feedback.pilot_id,
                        assignment_id=feedback.assignment_id,
                        conversation_id=feedback.conversation_id,
                        participant_code=feedback.participant_code,
                        completed=feedback.completed,
                        rating=feedback.rating,
                        confusion_tags=feedback.confusion_tags,
                        comment_redacted=feedback.comment_redacted,
                        created_at=feedback.created_at,
                    )
                )
        except IntegrityError as exc:
            raise ValueError("Feedback already exists for this conversation") from exc
        return feedback

    def list_pilot_feedback(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotFeedback]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(PilotFeedbackRow)
                .where(
                    PilotFeedbackRow.pilot_id == pilot_id,
                    PilotFeedbackRow.tenant_id == tenant_id,
                )
                .order_by(PilotFeedbackRow.created_at)
            )
            return [self._pilot_feedback(row) for row in rows]

    def save_pilot_metric_snapshot(
        self, snapshot: PilotMetricSnapshot
    ) -> PilotMetricSnapshot:
        with self.session_factory.begin() as session:
            session.add(
                PilotMetricSnapshotRow(
                    id=snapshot.snapshot_id,
                    tenant_id=snapshot.tenant_id,
                    pilot_id=snapshot.pilot_id,
                    metrics=snapshot.metrics,
                    safety=snapshot.safety,
                    created_at=snapshot.created_at,
                )
            )
        return snapshot

    def list_pilot_metric_snapshots(
        self, pilot_id: str, tenant_id: str
    ) -> list[PilotMetricSnapshot]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(PilotMetricSnapshotRow)
                .where(
                    PilotMetricSnapshotRow.pilot_id == pilot_id,
                    PilotMetricSnapshotRow.tenant_id == tenant_id,
                )
                .order_by(PilotMetricSnapshotRow.created_at)
            )
            return [self._pilot_snapshot(row) for row in rows]

    def pilot_metrics(
        self, tenant_id: str, conversation_ids: set[str] | None = None
    ) -> dict:
        """Aggregate pilot KPIs in PostgreSQL without loading tenant history into API memory."""

        statement = text(
            """
            SELECT
              (SELECT count(*) FROM conversation WHERE tenant_id = :tenant_id AND (:all_rows OR id = ANY(:conversation_ids))) AS conversation_count,
              (SELECT count(*) FROM conversation WHERE tenant_id = :tenant_id AND (:all_rows OR id = ANY(:conversation_ids)) AND status IN ('resolved','closed')) AS resolved_count,
              (SELECT count(*) FROM conversation c WHERE c.tenant_id = :tenant_id
                 AND (:all_rows OR c.id = ANY(:conversation_ids))
                 AND c.status IN ('resolved','closed')
                 AND NOT EXISTS (SELECT 1 FROM handoff h WHERE h.tenant_id = c.tenant_id AND h.conversation_id = c.id)) AS self_resolved_count,
              (SELECT count(DISTINCT conversation_id) FROM handoff WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS handed_conversation_count,
              (SELECT count(*) FROM handoff WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS handoff_count,
              (SELECT count(*) FROM handoff WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND status = 'resolved') AS resolved_handoff_count,
              (SELECT count(*) FROM ticket WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS ticket_count,
              (SELECT count(*) FROM ticket WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND status = 'open') AS open_ticket_count,
              (SELECT coalesce(avg(extract(epoch FROM ((context_snapshot->>'first_response_at')::timestamptz - created_at))),0)
                 FROM handoff WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND context_snapshot->>'first_response_at' IS NOT NULL) AS average_first_response_seconds,
              (SELECT coalesce(avg(extract(epoch FROM ((context_snapshot->>'resolved_at')::timestamptz - created_at))),0)
                 FROM handoff WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND context_snapshot->>'resolved_at' IS NOT NULL) AS average_resolution_seconds,
              (SELECT count(*) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS agent_run_count,
              (SELECT coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms),0) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS agent_p95_latency_ms,
              (SELECT count(*) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND error_code IS NOT NULL) AS failed_agent_run_count,
              (SELECT coalesce(sum(input_tokens),0) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS input_tokens,
              (SELECT coalesce(sum(output_tokens),0) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS output_tokens,
              (SELECT coalesce(sum(estimated_cost),0) FROM agent_run WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS estimated_cost,
              (SELECT coalesce(percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms),0) FROM tool_call_log WHERE tenant_id = :tenant_id AND tool_name = 'get_order' AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS order_query_p95_latency_ms,
              (SELECT count(*) FROM action_execution e JOIN action_plan p ON p.id = e.action_plan_id WHERE e.tenant_id = :tenant_id AND (:all_rows OR p.conversation_id = ANY(:conversation_ids)) AND p.status IN ('draft','validated','approval_required')) AS unsafe_write_count,
              (SELECT count(*) FROM tool_call_log WHERE tenant_id = :tenant_id AND (:all_rows OR conversation_id = ANY(:conversation_ids)) AND tool_name IN ('refund','issue_coupon','apply_compensation') AND status = 'succeeded') AS automatic_high_risk_action_count,
              (SELECT coalesce(sum(item_count - 1),0) FROM (SELECT count(*) AS item_count FROM action_execution e JOIN action_plan p ON p.id = e.action_plan_id WHERE e.tenant_id = :tenant_id AND (:all_rows OR p.conversation_id = ANY(:conversation_ids)) GROUP BY e.idempotency_key HAVING count(*) > 1) duplicates) AS duplicate_execution_count,
              (SELECT count(*) FROM runtime_task WHERE tenant_id = :tenant_id AND status = 'dead_letter' AND (:all_rows OR conversation_id = ANY(:conversation_ids))) AS dead_letter_count
            """
        )
        with self.session_factory() as session:
            row = session.execute(
                statement,
                {
                    "tenant_id": tenant_id,
                    "all_rows": conversation_ids is None,
                    "conversation_ids": list(conversation_ids or {"__none__"}),
                },
            ).mappings().one()

        def ratio(numerator: int, denominator: int) -> float:
            return numerator / denominator if denominator else 0.0

        resolved = int(row["resolved_count"])
        conversations = int(row["conversation_count"])
        handoffs = int(row["handoff_count"])
        runs = int(row["agent_run_count"])
        return {
            "conversation_count": conversations,
            "resolved_count": resolved,
            "self_service_resolution_rate": ratio(int(row["self_resolved_count"]), resolved),
            "handoff_rate": ratio(int(row["handed_conversation_count"]), conversations),
            "first_contact_resolution_rate": ratio(
                int(row["resolved_handoff_count"]), handoffs
            ),
            "ticket_count": int(row["ticket_count"]),
            "open_ticket_count": int(row["open_ticket_count"]),
            "average_first_response_seconds": float(row["average_first_response_seconds"]),
            "average_resolution_seconds": float(row["average_resolution_seconds"]),
            "agent_run_count": runs,
            "agent_p95_latency_ms": int(row["agent_p95_latency_ms"]),
            "order_query_p95_latency_ms": int(row["order_query_p95_latency_ms"]),
            "tool_failure_rate": ratio(int(row["failed_agent_run_count"]), runs),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "estimated_cost": float(row["estimated_cost"]),
            "unsafe_write_count": int(row["unsafe_write_count"]),
            "automatic_high_risk_action_count": int(
                row["automatic_high_risk_action_count"]
            ),
            "duplicate_execution_count": int(row["duplicate_execution_count"]),
            "dead_letter_count": int(row["dead_letter_count"]),
        }
