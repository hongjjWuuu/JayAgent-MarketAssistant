from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4


class SimulatorStore:
    """Small durable third-party state store with database-enforced idempotency."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, customer_id TEXT NOT NULL,
                    status TEXT NOT NULL, total_amount REAL NOT NULL, shipment_status TEXT NOT NULL,
                    tracking_number TEXT NOT NULL, carrier TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, customer_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
                    priority TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS controls (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                """
            )
        self.reset()

    def reset(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM orders")
            connection.execute("DELETE FROM tickets")
            connection.execute("DELETE FROM controls")
            connection.execute("DELETE FROM counters")
            connection.execute(
                """INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "order_1001",
                    "tenant_demo",
                    "customer_001",
                    "shipped",
                    128.0,
                    "in_transit",
                    "TRACK-1001",
                    "demo-logistics",
                ),
            )

    def configure_fault(self, operation: str, mode: str, value: Any = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO controls(name, value) VALUES (?, ?)",
                (operation, json.dumps({"mode": mode, "value": value})),
            )

    def fault(self, operation: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM controls WHERE name IN (?, '*') ORDER BY name DESC LIMIT 1",
                (operation,),
            ).fetchone()
        return json.loads(row["value"]) if row else {"mode": "none", "value": None}

    def increment(self, name: str) -> int:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO counters(name, value) VALUES (?, 1) "
                "ON CONFLICT(name) DO UPDATE SET value=value+1",
                (name,),
            )
            return int(
                connection.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]
            )

    def get_order(self, order_id: str, tenant_id: str, customer_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE order_id=? AND tenant_id=? AND customer_id=?",
                (order_id, tenant_id, customer_id),
            ).fetchone()
        return dict(row) if row else None

    def create_ticket(self, payload: dict, idempotency_key: str) -> tuple[dict, bool]:
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload FROM tickets WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing:
                return json.loads(existing["payload"]), False
            ticket_id = str(payload.get("ticket_id") or f"external_ticket_{uuid4().hex}")
            result = {
                **payload,
                "ticket_id": ticket_id,
                "status": "open",
                "idempotency_key": idempotency_key,
                "connector_receipt_id": f"receipt_{uuid4().hex}",
            }
            connection.execute(
                """INSERT INTO tickets(
                    ticket_id, tenant_id, customer_id, conversation_id, title, description,
                    priority, status, idempotency_key, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticket_id,
                    payload["tenant_id"],
                    payload["customer_id"],
                    payload["conversation_id"],
                    payload["title"],
                    payload["description"],
                    payload.get("priority", "normal"),
                    "open",
                    idempotency_key,
                    json.dumps(result),
                ),
            )
            return result, True

    def get_ticket(self, ticket_id: str, tenant_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM tickets WHERE ticket_id=? AND tenant_id=?",
                (ticket_id, tenant_id),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def lookup(self, tenant_id: str, idempotency_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM tickets WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, idempotency_key),
            ).fetchone()
        if not row:
            return None
        value = json.loads(row["payload"])
        return {
            "external_id": value["ticket_id"],
            "status": "succeeded",
            "connector_receipt_id": value["connector_receipt_id"],
            "idempotency_key": idempotency_key,
        }

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            tickets = int(connection.execute("SELECT count(*) FROM tickets").fetchone()[0])
            rows = connection.execute("SELECT name, value FROM counters").fetchall()
            faults = connection.execute("SELECT name, value FROM controls").fetchall()
        return {
            "ticket_count": tickets,
            "counters": {row["name"]: row["value"] for row in rows},
            "faults": {row["name"]: json.loads(row["value"]) for row in faults},
        }
