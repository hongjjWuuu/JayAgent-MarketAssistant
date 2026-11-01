from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.connectors.business import BusinessConnector, build_business_connector
from app.core.security import AuthContext
from app.repositories.contracts import MarketRepository


class OrderLookupInput(BaseModel):
    order_id: str = Field(pattern=r"^order[_-]?\d+$")
    customer_id: str


class TicketPreviewInput(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=4000)


class CompensationInput(BaseModel):
    amount: float = Field(gt=0, le=50)
    reason: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    risk: str
    roles: tuple[str, ...]
    timeout_seconds: int
    requires_confirmation: bool
    retry_writes: bool
    reconciliation: str


TOOL_POLICIES = {
    "get_order": ToolPolicy(
        "get_order",
        "sensitive_read",
        ("customer", "agent", "tenant_admin"),
        3,
        False,
        False,
        "none",
    ),
    "get_shipment": ToolPolicy(
        "get_shipment",
        "sensitive_read",
        ("customer", "agent", "tenant_admin"),
        3,
        False,
        False,
        "none",
    ),
    "search_policy": ToolPolicy(
        "search_policy", "read_only", ("customer", "agent", "tenant_admin"), 4, False, False, "none"
    ),
    "preview_ticket": ToolPolicy(
        "preview_ticket", "pure", ("customer", "agent", "tenant_admin"), 1, False, False, "none"
    ),
    "create_ticket": ToolPolicy(
        "create_ticket",
        "medium_write",
        ("customer", "agent"),
        5,
        True,
        False,
        "idempotency_receipt",
    ),
    "propose_compensation": ToolPolicy(
        "propose_compensation", "pure", ("agent", "tenant_admin"), 1, False, False, "none"
    ),
    "submit_compensation": ToolPolicy(
        "submit_compensation",
        "high_write",
        ("agent", "tenant_admin"),
        5,
        True,
        False,
        "human_approval_only",
    ),
}


class ToolGateway:
    def __init__(
        self, repository: MarketRepository, connector: BusinessConnector | None = None
    ) -> None:
        self.repository = repository
        self.connector = connector or build_business_connector(repository)

    def get_order(self, auth: AuthContext, data: OrderLookupInput) -> dict[str, Any]:
        self._authorize(auth, "get_order")
        customer_id = auth.customer_id if auth.role == "customer" else data.customer_id
        if not customer_id:
            raise HTTPException(status_code=403, detail="Customer identity is required")
        order = self.connector.get_order(
            auth.tenant_id, customer_id, data.order_id.replace("-", "_")
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return order.model_dump(mode="json")

    def get_shipment(self, auth: AuthContext, data: OrderLookupInput) -> dict[str, Any]:
        self._authorize(auth, "get_shipment")
        customer_id = auth.customer_id if auth.role == "customer" else data.customer_id
        if not customer_id:
            raise HTTPException(status_code=403, detail="Customer identity is required")
        shipment = self.connector.get_shipment(
            auth.tenant_id, customer_id, data.order_id.replace("-", "_")
        )
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return shipment.model_dump(mode="json")

    @staticmethod
    def preview_ticket(auth: AuthContext, data: TicketPreviewInput) -> dict[str, Any]:
        ToolGateway._authorize(auth, "preview_ticket")
        return {"title": data.title, "description": data.description, "requires_confirmation": True}

    @staticmethod
    def propose_compensation(auth: AuthContext, data: CompensationInput) -> dict[str, Any]:
        ToolGateway._authorize(auth, "propose_compensation")
        amount = min(data.amount, 50.0)
        return {
            "recommended_amount": amount,
            "reason": data.reason,
            "requires_human_approval": True,
        }

    @staticmethod
    def _authorize(auth: AuthContext, tool_name: str) -> None:
        policy = TOOL_POLICIES[tool_name]
        if auth.role not in policy.roles:
            raise HTTPException(status_code=403, detail=f"Role is not allowed to call {tool_name}")


def tool_catalog() -> list[dict[str, Any]]:
    return [policy.__dict__ for policy in TOOL_POLICIES.values()]
