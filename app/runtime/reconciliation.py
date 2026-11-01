from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from app.connectors.business import BusinessConnector
from app.domain.models import ActionExecution, ActionPlan

ReconciliationOutcome = Literal["succeeded", "failed", "compensation_required", "pending"]


@dataclass(frozen=True)
class ReconciliationResult:
    outcome: ReconciliationOutcome
    receipt: dict


class ReconciliationProvider(Protocol):
    def lookup(self, execution: ActionExecution, plan: ActionPlan) -> ReconciliationResult: ...


class SimulatedReconciliationProvider:
    """Lookup-only deterministic adapter for the pilot connector."""

    def lookup(self, execution: ActionExecution, plan: ActionPlan) -> ReconciliationResult:
        raw_outcome = str(plan.payload.get("reconciliation_outcome", "pending"))
        if raw_outcome not in {"succeeded", "failed", "compensation_required", "pending"}:
            raw_outcome = "pending"
        return ReconciliationResult(
            outcome=cast(ReconciliationOutcome, raw_outcome),
            receipt={
                "lookup_only": True,
                "idempotency_key": execution.idempotency_key,
                "provider": "simulated",
            },
        )


class ConnectorReconciliationProvider:
    """Query-only reconciliation adapter; it never repeats the original write."""

    def __init__(self, connector: BusinessConnector) -> None:
        self.connector = connector

    def lookup(self, execution: ActionExecution, plan: ActionPlan) -> ReconciliationResult:
        receipt = self.connector.lookup_write(execution.tenant_id, execution.idempotency_key)
        if receipt is None or receipt.status in {"pending", "processing", "unknown"}:
            return ReconciliationResult("pending", {"lookup_only": True})
        outcome: ReconciliationOutcome = (
            "succeeded" if receipt.status in {"succeeded", "created", "open"} else "failed"
        )
        return ReconciliationResult(
            outcome,
            {
                "lookup_only": True,
                "external_id": receipt.external_id,
                "external_status": receipt.status,
                "connector": receipt.payload,
            },
        )
