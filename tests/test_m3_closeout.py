from __future__ import annotations

import httpx
import pytest

from app.connectors.business import HttpBusinessConnector, RepositoryBusinessConnector
from app.core.config import settings
from app.core.security import AuthContext
from app.domain.models import ActionExecution, ActionPlan, ActionStatus, Ticket
from app.repositories.memory import InMemoryStore
from app.runtime.reconciliation import ConnectorReconciliationProvider
from app.services.market_service import MarketAssistantService


def auth(role: str, subject: str, customer_id: str | None = None) -> AuthContext:
    return AuthContext("tenant_demo", subject, role, "req_test", subject, customer_id)


def test_handoff_transfer_preserves_context_and_returns_to_queue():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    customer = auth("customer", "customer_001", "customer_001")
    agent = auth("agent", "agent_001")
    conversation = service.create_conversation(customer, "customer_001", "web")
    handoff = service.create_handoff(customer, conversation.conversation_id, "user_requested")
    claimed = service.claim_handoff(agent, handoff.handoff_id)

    transferred = service.transfer_handoff(
        agent, claimed.handoff_id, "queue_general", "shift handover"
    )

    assert transferred.status == "queued"
    assert transferred.assigned_agent_id is None
    assert transferred.context_snapshot["transfer_history"][0]["reason"] == "shift handover"
    assert store.get_conversation(conversation.conversation_id, "tenant_demo").owner_type == (
        "handoff_queue"
    )


def test_pilot_metrics_are_derived_from_persisted_handoff_timestamps():
    store = InMemoryStore()
    service = MarketAssistantService(store)
    customer = auth("customer", "customer_001", "customer_001")
    agent = auth("agent", "agent_001")
    admin = auth("tenant_admin", "admin_001")
    conversation = service.create_conversation(customer, "customer_001", "web")
    handoff = service.create_handoff(customer, conversation.conversation_id, "user_requested")
    service.claim_handoff(agent, handoff.handoff_id)
    service.handoff_reply(agent, handoff.handoff_id, "已接管并开始处理")
    service.resolve_handoff(agent, handoff.handoff_id, False)

    metrics = service.pilot_metrics(admin)

    assert metrics["conversation_count"] == 1
    assert metrics["handoff_rate"] == 1
    assert metrics["first_contact_resolution_rate"] == 1
    assert metrics["average_first_response_seconds"] >= 0


def test_repository_connector_is_idempotent_through_deterministic_ticket_id():
    store = InMemoryStore()
    connector = RepositoryBusinessConnector(store)
    ticket = Ticket(
        ticket_id="ticket_fixed",
        tenant_id="tenant_demo",
        customer_id="customer_001",
        conversation_id="conv_1",
        title="售后工单",
        description="破损",
    )

    first = connector.create_ticket(ticket, "idem-1")
    second = connector.create_ticket(ticket, "idem-1")

    assert first.external_id == second.external_id == "ticket_fixed"
    assert len(store.list_tickets("tenant_demo")) == 1


def test_http_connector_timeout_is_exposed_to_worker_as_unknown(monkeypatch):
    monkeypatch.setattr(settings, "business_connector_base_url", "https://merchant.invalid")

    def timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("response lost after write")

    monkeypatch.setattr(httpx, "request", timeout)
    connector = HttpBusinessConnector()
    ticket = Ticket(
        ticket_id="ticket_timeout",
        tenant_id="tenant_demo",
        customer_id="customer_001",
        conversation_id="conv_1",
        title="售后工单",
        description="破损",
    )
    with pytest.raises(httpx.ReadTimeout):
        connector.create_ticket(ticket, "idem-timeout")


def test_connector_reconciliation_only_looks_up_idempotency_key():
    class LookupConnector:
        def lookup_write(self, tenant_id, idempotency_key):
            from app.connectors.business import ConnectorReceipt

            assert tenant_id == "tenant_demo"
            assert idempotency_key == "idem-1"
            return ConnectorReceipt("external-1", "created", {"source": "merchant"})

    execution = ActionExecution(
        execution_id="execution_1",
        tenant_id="tenant_demo",
        action_plan_id="plan_1",
        idempotency_key="idem-1",
        status=ActionStatus.UNKNOWN_NEEDS_RECONCILIATION,
    )
    plan = ActionPlan(
        action_plan_id="plan_1",
        tenant_id="tenant_demo",
        customer_id="customer_001",
        conversation_id="conv_1",
        action_type="create_ticket",
        risk_level="medium",
        payload={},
        preview="preview",
        idempotency_key="idem-1",
    )

    result = ConnectorReconciliationProvider(LookupConnector()).lookup(execution, plan)

    assert result.outcome == "succeeded"
    assert result.receipt["external_id"] == "external-1"

