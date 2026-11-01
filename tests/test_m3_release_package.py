from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx

from app.connectors.business import HttpBusinessConnector, RepositoryBusinessConnector
from app.core.config import settings
from app.core.security import AuthContext
from app.repositories.memory import InMemoryStore
from app.tools.gateway import OrderLookupInput, ToolGateway
from simulator.store import SimulatorStore


def simulator_store() -> SimulatorStore:
    path = Path.cwd() / "output" / "test-data" / f"merchant-{uuid4().hex}.db"
    return SimulatorStore(str(path))


def test_simulator_concurrent_idempotency_creates_one_ticket():
    store = simulator_store()
    payload = {
        "ticket_id": "external_ticket_fixed",
        "tenant_id": "tenant_demo",
        "customer_id": "customer_001",
        "conversation_id": "conv_1",
        "title": "破损",
        "description": "商品破损",
        "priority": "normal",
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.create_ticket(payload, "idem-concurrent"), range(20)))

    assert {value[0]["ticket_id"] for value in results} == {"external_ticket_fixed"}
    assert sum(created for _, created in results) == 1
    assert store.stats()["ticket_count"] == 1


def test_simulator_enforces_order_tenant_and_customer_ownership():
    store = simulator_store()

    assert store.get_order("order_1001", "tenant_demo", "customer_001") is not None
    assert store.get_order("order_1001", "tenant_other", "customer_001") is None
    assert store.get_order("order_1001", "tenant_demo", "customer_other") is None


def test_repository_connector_and_gateway_return_strongly_typed_shipment():
    store = InMemoryStore()
    connector = RepositoryBusinessConnector(store)
    gateway = ToolGateway(store, connector)
    auth = AuthContext(
        "tenant_demo", "customer_001", "customer", "req_m3", "customer_001", "customer_001"
    )

    shipment = connector.get_shipment("tenant_demo", "customer_001", "order_1001")
    rendered = gateway.get_shipment(
        auth, OrderLookupInput(order_id="order_1001", customer_id="customer_001")
    )

    assert shipment is not None
    assert shipment.tracking_number == "TRACK-1001"
    assert rendered["status"] == "in_transit"


def test_http_connector_shipment_and_404(monkeypatch):
    monkeypatch.setattr(settings, "business_connector_base_url", "https://merchant.test")

    def request(method, url, **_kwargs):
        if url.endswith("/order_1001/shipment"):
            return httpx.Response(
                200,
                json={
                    "order_id": "order_1001",
                    "tenant_id": "tenant_demo",
                    "customer_id": "customer_001",
                    "status": "in_transit",
                    "tracking_number": "TRACK-1001",
                    "carrier": "demo-logistics",
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", request)
    connector = HttpBusinessConnector()
    shipment = connector.get_shipment("tenant_demo", "customer_001", "order_1001")

    assert shipment is not None and shipment.carrier == "demo-logistics"
    assert connector.get_shipment("tenant_demo", "customer_001", "order_9999") is None


def test_simulator_pending_reconciliation_only_changes_lookup_counter():
    store = simulator_store()
    store.configure_fault("lookup_write", "pending_then_succeeded", 2)
    payload = {
        "tenant_id": "tenant_demo",
        "customer_id": "customer_001",
        "conversation_id": "conv_1",
        "title": "破损",
        "description": "商品破损",
    }
    store.create_ticket(payload, "idem-lookup")

    assert store.increment("lookup:idem-lookup") == 1
    assert store.increment("lookup:idem-lookup") == 2
    assert store.lookup("tenant_demo", "idem-lookup")["status"] == "succeeded"
    assert store.stats()["ticket_count"] == 1
