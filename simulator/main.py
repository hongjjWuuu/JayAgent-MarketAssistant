from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from simulator.store import SimulatorStore

app = FastAPI(title="M3 Business Simulator", version="1.0")
store = SimulatorStore(os.getenv("SIMULATOR_DB_PATH", "/data/simulator.db"))
control_token = os.getenv("SIMULATOR_CONTROL_TOKEN", "m3-drill-control")


class FaultRequest(BaseModel):
    operation: str = "*"
    mode: str
    value: int | float | str | None = None


async def apply_fault(operation: str, *, after_commit: bool = False) -> None:
    fault = store.fault(operation)
    mode = fault["mode"]
    value = fault.get("value")
    if mode == "latency":
        await asyncio.sleep(float(value or 1))
    elif mode == "429":
        raise HTTPException(status_code=429, detail="simulated rate limit")
    elif mode == "500":
        raise HTTPException(status_code=500, detail="simulated upstream failure")
    elif after_commit and mode == "commit_then_500":
        raise HTTPException(status_code=500, detail="write committed; response lost")
    elif after_commit and mode == "commit_then_timeout":
        await asyncio.sleep(float(value or 30))


def require_control(token: str | None) -> None:
    if token != control_token:
        raise HTTPException(status_code=404, detail="Not found")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready"}


@app.get("/v1/orders/{order_id}")
async def get_order(order_id: str, tenant_id: str, customer_id: str):
    store.increment("get_order")
    await apply_fault("get_order")
    value = store.get_order(order_id, tenant_id, customer_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return value


@app.get("/v1/orders/{order_id}/shipment")
async def get_shipment(order_id: str, tenant_id: str, customer_id: str):
    store.increment("get_shipment")
    await apply_fault("get_shipment")
    value = store.get_order(order_id, tenant_id, customer_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return {
        "order_id": order_id,
        "tenant_id": tenant_id,
        "customer_id": customer_id,
        "status": value["shipment_status"],
        "tracking_number": value["tracking_number"],
        "carrier": value["carrier"],
    }


@app.post("/v1/tickets")
async def create_ticket(request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
    store.increment("create_ticket")
    fault = store.fault("create_ticket")
    if fault["mode"] not in {"commit_then_500", "commit_then_timeout"}:
        await apply_fault("create_ticket")
    payload = await request.json()
    result, created = store.create_ticket(payload, idempotency_key)
    result["idempotent_replay"] = not created
    await apply_fault("create_ticket", after_commit=True)
    return JSONResponse(result, status_code=201 if created else 200)


@app.get("/v1/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, tenant_id: str):
    store.increment("get_ticket")
    await apply_fault("get_ticket")
    value = store.get_ticket(ticket_id, tenant_id)
    if value is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return value


@app.get("/v1/write-receipts/by-idempotency-key")
async def lookup_write(tenant_id: str, idempotency_key: str):
    attempt = store.increment(f"lookup:{idempotency_key}")
    fault = store.fault("lookup_write")
    if fault["mode"] == "pending_then_succeeded" and attempt <= int(fault.get("value") or 1):
        return {"external_id": "pending", "status": "pending", "idempotency_key": idempotency_key}
    await apply_fault("lookup_write")
    value = store.lookup(tenant_id, idempotency_key)
    if value is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return value


@app.post("/__control/reset")
def reset(x_simulator_control: str | None = Header(default=None)):
    require_control(x_simulator_control)
    store.reset()
    return {"status": "reset"}


@app.post("/__control/faults")
def faults(data: FaultRequest, x_simulator_control: str | None = Header(default=None)):
    require_control(x_simulator_control)
    store.configure_fault(data.operation, data.mode, data.value)
    return {"status": "configured", **data.model_dump()}


@app.get("/__control/stats")
def stats(x_simulator_control: str | None = Header(default=None)):
    require_control(x_simulator_control)
    return store.stats()
