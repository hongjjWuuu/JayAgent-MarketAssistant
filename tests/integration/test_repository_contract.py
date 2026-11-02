from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from app.domain.models import (
    ActionExecution,
    ActionPlan,
    ActionStatus,
    Conversation,
    ConversationEvent,
    Message,
    RuntimeTask,
    Ticket,
)
from app.repositories.postgres import PostgresRepository


def test_oidc_customer_and_agent_queue_resolution(postgres_repository):
    assert (
        postgres_repository.resolve_channel_customer(
            "tenant_contract", "oidc", "subject_contract"
        )
        == "customer_contract"
    )
    assert postgres_repository.resolve_channel_customer(
        "tenant_other", "oidc", "subject_contract"
    ) is None
    assert postgres_repository.list_agent_queue_ids(
        "tenant_contract", "subject_agent_contract"
    ) == {"queue_contract"}


def test_pilot_metrics_use_postgresql_aggregation(postgres_repository):
    metrics = postgres_repository.pilot_metrics("tenant_contract")

    assert metrics["conversation_count"] >= 0
    assert metrics["agent_p95_latency_ms"] >= 0
    assert 0 <= metrics["handoff_rate"] <= 1
    assert "estimated_cost" in metrics


def test_pilot_metrics_can_be_scoped_to_release_conversations(postgres_repository):
    selected = Conversation(
        conversation_id=unique("conv_pilot_selected"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    unrelated = Conversation(
        conversation_id=unique("conv_pilot_unrelated"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    postgres_repository.save_conversation(selected)
    postgres_repository.save_conversation(unrelated)

    metrics = postgres_repository.pilot_metrics(
        "tenant_contract", {selected.conversation_id}
    )

    assert metrics["conversation_count"] == 1
    assert metrics["unsafe_write_count"] == 0
    assert metrics["duplicate_execution_count"] == 0

pytestmark = pytest.mark.integration


def unique(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def test_conversation_message_task_and_event_are_atomic(
    postgres_repository: PostgresRepository,
) -> None:
    conversation = Conversation(
        conversation_id=unique("conv"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    postgres_repository.save_conversation(conversation)
    task = RuntimeTask(
        task_id=unique("task"),
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.conversation_id,
        task_type="agent_message",
        payload={"content": "查询订单"},
    )
    message = Message(
        message_id=unique("msg"),
        sender_type="customer",
        content="查询 order_9001",
        task_id=task.task_id,
    )
    conversation.messages.append(message)
    event = ConversationEvent(
        event_id=unique("evt"),
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.conversation_id,
        event_type="task.queued",
        data={"task_id": task.task_id},
    )

    postgres_repository.enqueue_message_atomic(conversation, task, event)

    persisted = postgres_repository.get_conversation(
        conversation.conversation_id, conversation.tenant_id
    )
    assert persisted is not None
    assert persisted.messages[-1].message_id == message.message_id
    assert postgres_repository.get_task(task.task_id, conversation.tenant_id) is not None
    events = postgres_repository.list_events(conversation.conversation_id, conversation.tenant_id)
    assert [item.event_id for item in events] == [event.event_id]


def test_tenant_scope_is_enforced_by_every_read(
    postgres_repository: PostgresRepository,
) -> None:
    conversation = Conversation(
        conversation_id=unique("conv"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    postgres_repository.save_conversation(conversation)
    assert (
        postgres_repository.get_conversation(conversation.conversation_id, "tenant_other") is None
    )
    assert postgres_repository.get_order("order_9001", "tenant_other", "customer_contract") is None


def test_two_concurrent_confirmations_create_one_execution(
    postgres_repository: PostgresRepository,
) -> None:
    conversation = Conversation(
        conversation_id=unique("conv"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    postgres_repository.save_conversation(conversation)
    plan = ActionPlan(
        action_plan_id=unique("plan"),
        tenant_id=conversation.tenant_id,
        customer_id=conversation.customer_id,
        conversation_id=conversation.conversation_id,
        action_type="create_ticket",
        risk_level="medium",
        payload={"title": "并发测试"},
        preview="创建并发测试工单",
        idempotency_key=unique("idem"),
        status=ActionStatus.APPROVAL_REQUIRED,
    )
    postgres_repository.save_action_plan(plan)

    def confirm(index: int) -> ActionExecution:
        task = RuntimeTask(
            task_id=unique(f"task{index}"),
            tenant_id=plan.tenant_id,
            conversation_id=plan.conversation_id,
            task_type="execute_action",
            payload={"action_plan_id": plan.action_plan_id},
        )
        execution = ActionExecution(
            execution_id=unique(f"exec{index}"),
            tenant_id=plan.tenant_id,
            action_plan_id=plan.action_plan_id,
            idempotency_key=plan.idempotency_key,
            status=ActionStatus.QUEUED,
            task_id=task.task_id,
        )
        return postgres_repository.confirm_action_atomic(
            plan.model_copy(deep=True), task, execution
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(confirm, (1, 2)))

    assert first.execution_id == second.execution_id
    assert first.idempotency_key == plan.idempotency_key


def test_new_repository_instance_recovers_persisted_business_facts(
    postgres_repository: PostgresRepository,
) -> None:
    conversation = Conversation(
        conversation_id=unique("conv"),
        tenant_id="tenant_contract",
        customer_id="customer_contract",
    )
    postgres_repository.save_conversation(conversation)
    ticket = Ticket(
        ticket_id=unique("ticket"),
        tenant_id=conversation.tenant_id,
        customer_id=conversation.customer_id,
        conversation_id=conversation.conversation_id,
        title="重启恢复测试",
        description="新的 Repository 实例仍应读取本记录",
    )
    postgres_repository.save_ticket(ticket)

    restarted_repository = PostgresRepository(postgres_repository.session_factory)
    recovered_conversation = restarted_repository.get_conversation(
        conversation.conversation_id, conversation.tenant_id
    )
    recovered_ticket = restarted_repository.get_ticket(
        ticket.ticket_id, ticket.tenant_id, ticket.customer_id
    )
    assert recovered_conversation is not None
    assert recovered_ticket is not None
    assert recovered_ticket.description == ticket.description


def test_two_workers_never_claim_the_same_task_and_expired_lease_recovers(
    postgres_repository: PostgresRepository,
) -> None:
    # Earlier contract tests intentionally enqueue tasks. Finish them first so
    # this test measures two workers competing for one specific ready row.
    with postgres_repository.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE runtime_task SET status = 'completed' "
                "WHERE status IN ('queued', 'retry_wait', 'running')"
            )
        )
    task = RuntimeTask(
        task_id=unique("task_claim"),
        tenant_id="tenant_contract",
        task_type="agent_message",
        payload={"content": "并发领取"},
    )
    postgres_repository.enqueue_task(task)

    def claim(worker_id: str):
        repository = PostgresRepository(postgres_repository.session_factory)
        return repository.claim_task(worker_id, 30)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-a", "worker-b")))

    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    first_worker = claimed[0].lease_owner
    second_worker = "worker-b" if first_worker == "worker-a" else "worker-a"
    assert postgres_repository.claim_task(second_worker, 30) is None

    with postgres_repository.session_factory.begin() as session:
        session.execute(
            text(
                "UPDATE runtime_task SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :task_id"
            ),
            {"task_id": task.task_id},
        )
    recovered = postgres_repository.claim_task(second_worker, 30)
    assert recovered is not None
    assert recovered.task_id == task.task_id
    assert recovered.lease_owner == second_worker
    assert recovered.attempts == 2


def test_required_unique_and_partial_indexes_exist(postgres_engine: Engine) -> None:
    required = {
        "uq_order_external",
        "uq_execution_idempotency",
        "uq_handoff_active_conversation",
        "uq_policy_current_scope",
        "ix_conversation_tenant_customer_updated",
        "ix_message_conversation_created",
        "ix_handoff_queue_order",
        "ix_ticket_tenant_customer_created",
        "ix_runtime_task_claim_ready",
        "ix_runtime_task_recover_lease",
        "uq_message_task_sender",
        "uq_runtime_task_replay_once",
        "ix_support_queue_member_subject",
    }
    with postgres_engine.connect() as connection:
        indexes = set(
            connection.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = ANY(current_schemas(false))"
                )
            ).scalars()
        )
    assert required <= indexes
    with postgres_engine.connect() as connection:
        constraints = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'tenant_member'::regclass"
                )
            ).scalars()
        )
    assert "ck_tenant_member_role" in constraints


def test_order_lookup_uses_composite_tenant_index(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        # A one-row table gives both indexes identical estimated selectivity,
        # so PostgreSQL may validly choose the smaller tenant-only index. Add a
        # realistic tenant distribution before asserting the production plan.
        connection.execute(
            text(
                "INSERT INTO orders "
                "(id, tenant_id, customer_id, external_order_id, status, "
                "total_amount, currency, updated_at) "
                "SELECT 'plan_order_' || n, 'tenant_contract', "
                "'customer_contract', 'plan_external_' || n, 'created', "
                "1.0, 'CNY', now() FROM generate_series(1, 500) AS n"
            )
        )
        connection.execute(text("ANALYZE orders"))
        connection.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            connection.execute(
                text(
                    "EXPLAIN SELECT * FROM orders "
                    "WHERE tenant_id = 'tenant_contract' "
                    "AND external_order_id = 'order_9001'"
                )
            ).scalars()
        )
    assert "uq_order_external" in plan
