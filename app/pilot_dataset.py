from __future__ import annotations

import hashlib
import json
from typing import Any

PILOT_DATASET_VERSION = "m4_pilot/v1"

PILOT_IDENTITIES = (
    ("P01", "customer_001", "11111111-1111-4111-8111-111111111111"),
    ("P02", "pilot_customer_02", "55555555-5555-4555-8555-555555555555"),
    ("P03", "pilot_customer_03", "66666666-6666-4666-8666-666666666666"),
    ("P04", "pilot_customer_04", "77777777-7777-4777-8777-777777777777"),
    ("P05", "pilot_customer_05", "88888888-8888-4888-8888-888888888888"),
)

PILOT_AGENT_IDENTITIES = (
    ("P06", "agent_001", "22222222-2222-4222-8222-222222222222"),
    ("P07", "pilot_agent_002", "99999999-9999-4999-8999-999999999999"),
)

PILOT_ADMIN_IDENTITY = (
    "P08",
    "admin_001",
    "33333333-3333-4333-8333-333333333333",
)

_SCENARIOS = (
    ("pending_fulfillment", "paid", "label_created"),
    ("in_transit", "shipped", "in_transit"),
    ("delayed", "shipped", "delayed"),
    ("delivered", "delivered", "delivered"),
    ("damaged_item", "delivered", "delivered"),
    ("return_window_expired", "delivered", "delivered"),
)


def build_m4_orders() -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    for participant_index, (code, customer_id, _) in enumerate(PILOT_IDENTITIES, start=1):
        for scenario_index, (scenario, status, shipment_status) in enumerate(
            _SCENARIOS, start=1
        ):
            order_id = f"{code}-ORDER-{scenario_index:02d}"
            orders.append(
                {
                    "dataset_version": PILOT_DATASET_VERSION,
                    "participant_code": code,
                    "order_id": order_id,
                    "tenant_id": "tenant_demo",
                    "customer_id": customer_id,
                    "scenario": scenario,
                    "status": status,
                    "total_amount": float(40 + participant_index * 10 + scenario_index * 7),
                    "shipment_status": shipment_status,
                    "tracking_number": f"M4-{participant_index:02d}-{scenario_index:02d}",
                    "carrier": "m4-demo-logistics",
                    "sku": f"M4-SKU-{scenario_index:02d}",
                    "item_name": f"M4 仿真商品 {scenario_index:02d}",
                    "events": [
                        {
                            "status": shipment_status,
                            "message": f"仿真轨迹：{scenario}",
                        }
                    ],
                }
            )
    return orders


def m4_dataset_hash() -> str:
    payload = json.dumps(
        build_m4_orders(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()
