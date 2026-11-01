from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.domain.models import Order, Shipment, Ticket
from app.observability.metrics import CONNECTOR_DURATION, CONNECTOR_REQUESTS
from app.repositories.contracts import MarketRepository


@dataclass(frozen=True)
class ConnectorReceipt:
    external_id: str
    status: str
    payload: dict[str, Any]


class BusinessConnector(Protocol):
    def get_order(self, tenant_id: str, customer_id: str, order_id: str) -> Order | None: ...
    def get_shipment(
        self, tenant_id: str, customer_id: str, order_id: str
    ) -> Shipment | None: ...
    def create_ticket(self, ticket: Ticket, idempotency_key: str) -> ConnectorReceipt: ...
    def get_ticket(self, tenant_id: str, ticket_id: str) -> ConnectorReceipt | None: ...
    def lookup_write(self, tenant_id: str, idempotency_key: str) -> ConnectorReceipt | None: ...


class RepositoryBusinessConnector:
    """Deterministic CI/default connector backed by the application repository."""

    def __init__(self, repository: MarketRepository) -> None:
        self.repository = repository

    def get_order(self, tenant_id: str, customer_id: str, order_id: str) -> Order | None:
        return self.repository.get_order(order_id, tenant_id, customer_id)

    def get_shipment(
        self, tenant_id: str, customer_id: str, order_id: str
    ) -> Shipment | None:
        order = self.get_order(tenant_id, customer_id, order_id)
        if order is None:
            return None
        return Shipment(
            order_id=order.order_id,
            tenant_id=order.tenant_id,
            customer_id=order.customer_id,
            status=order.shipment_status,
            tracking_number=order.tracking_number,
        )

    def create_ticket(self, ticket: Ticket, idempotency_key: str) -> ConnectorReceipt:
        self.repository.save_ticket(ticket)
        return ConnectorReceipt(
            external_id=ticket.ticket_id,
            status=ticket.status,
            payload={"idempotency_key": idempotency_key, "source": "repository"},
        )

    def get_ticket(self, tenant_id: str, ticket_id: str) -> ConnectorReceipt | None:
        value = self.repository.get_ticket(ticket_id, tenant_id, None)
        if value is None:
            return None
        return ConnectorReceipt(value.ticket_id, value.status, {"source": "repository"})

    def lookup_write(self, tenant_id: str, idempotency_key: str) -> ConnectorReceipt | None:
        execution = self.repository.execution_for_key(tenant_id, idempotency_key)
        if not execution or not execution.receipt:
            return None
        external_id = str(
            execution.receipt.get("ticket_id")
            or execution.receipt.get("approval_ticket_id")
            or execution.execution_id
        )
        return ConnectorReceipt(external_id, execution.status.value, execution.receipt)


class HttpBusinessConnector:
    """Thin adapter for a future merchant OMS/WMS/ticket API."""

    def __init__(self) -> None:
        if not settings.business_connector_base_url:
            raise RuntimeError("BUSINESS_CONNECTOR_BASE_URL is required for http mode")
        self.base_url = settings.business_connector_base_url.rstrip("/")
        self.headers = (
            {"Authorization": f"Bearer {settings.business_connector_api_key}"}
            if settings.business_connector_api_key
            else {}
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        operation = kwargs.pop("operation", path.strip("/").replace("/", "_"))
        request_headers = {**self.headers, **kwargs.pop("headers", {})}
        try:
            with CONNECTOR_DURATION.labels(operation=operation).time():
                response = httpx.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=request_headers,
                    timeout=httpx.Timeout(settings.business_connector_timeout_seconds),
                    **kwargs,
                )
                response.raise_for_status()
            CONNECTOR_REQUESTS.labels(operation=operation, outcome="success").inc()
            return response.json()
        except httpx.TimeoutException:
            CONNECTOR_REQUESTS.labels(operation=operation, outcome="timeout").inc()
            raise
        except httpx.HTTPStatusError as error:
            outcome = "server_error" if error.response.status_code >= 500 else "business_error"
            CONNECTOR_REQUESTS.labels(operation=operation, outcome=outcome).inc()
            raise
        except httpx.NetworkError:
            CONNECTOR_REQUESTS.labels(operation=operation, outcome="network_error").inc()
            raise

    def get_order(self, tenant_id: str, customer_id: str, order_id: str) -> Order | None:
        try:
            data = self._request(
                "GET",
                f"/v1/orders/{order_id}",
                params={"tenant_id": tenant_id, "customer_id": customer_id},
                operation="get_order",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        return Order.model_validate(data)

    def get_shipment(
        self, tenant_id: str, customer_id: str, order_id: str
    ) -> Shipment | None:
        try:
            data = self._request(
                "GET",
                f"/v1/orders/{order_id}/shipment",
                params={"tenant_id": tenant_id, "customer_id": customer_id},
                operation="get_shipment",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        return Shipment.model_validate(data)

    def create_ticket(self, ticket: Ticket, idempotency_key: str) -> ConnectorReceipt:
        data = self._request(
            "POST",
            "/v1/tickets",
            headers={**self.headers, "Idempotency-Key": idempotency_key},
            json=ticket.model_dump(mode="json"),
            operation="create_ticket",
        )
        return ConnectorReceipt(str(data["ticket_id"]), str(data["status"]), data)

    def get_ticket(self, tenant_id: str, ticket_id: str) -> ConnectorReceipt | None:
        try:
            data = self._request(
                "GET",
                f"/v1/tickets/{ticket_id}",
                params={"tenant_id": tenant_id},
                operation="get_ticket",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        return ConnectorReceipt(str(data["ticket_id"]), str(data["status"]), data)

    def lookup_write(self, tenant_id: str, idempotency_key: str) -> ConnectorReceipt | None:
        try:
            data = self._request(
                "GET",
                "/v1/write-receipts/by-idempotency-key",
                params={"tenant_id": tenant_id, "idempotency_key": idempotency_key},
                operation="lookup_write",
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        return ConnectorReceipt(str(data["external_id"]), str(data["status"]), data)


def build_business_connector(repository: MarketRepository) -> BusinessConnector:
    if settings.business_connector_mode == "http":
        return HttpBusinessConnector()
    return RepositoryBusinessConnector(repository)
