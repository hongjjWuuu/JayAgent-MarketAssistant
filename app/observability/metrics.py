from __future__ import annotations

from collections import Counter as ValueCounter
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram

from app.core.config import settings

CONNECTOR_REQUESTS = Counter(
    "market_assistant_connector_requests_total",
    "Calls to the configured business connector",
    ["operation", "outcome"],
)
CONNECTOR_DURATION = Histogram(
    "market_assistant_connector_duration_seconds",
    "Business connector call duration",
    ["operation"],
)
LLM_FAILURES = Gauge(
    "market_assistant_llm_failures",
    "Persisted LLM failures by normalized error code",
    ["error_code"],
)
RUNTIME_TASKS = Gauge(
    "market_assistant_runtime_tasks", "Runtime tasks by durable status", ["status"]
)
WORKERS = Gauge("market_assistant_workers", "Workers by liveness state", ["state"])
DEAD_LETTERS = Gauge("market_assistant_dead_letters", "Dead-letter task count")
UNKNOWN_RECONCILIATIONS = Gauge(
    "market_assistant_unknown_reconciliations", "Executions awaiting reconciliation"
)
HANDOFFS = Gauge("market_assistant_handoffs", "Handoffs by state", ["status"])
WORKER_HEARTBEAT = Gauge(
    "market_assistant_worker_heartbeat_timestamp_seconds",
    "Most recent worker process heartbeat",
    ["worker_id"],
)
WORKER_TASKS_PROCESSED = Counter(
    "market_assistant_worker_tasks_processed_total", "Worker task outcomes", ["outcome"]
)


def refresh_repository_metrics(repository) -> None:
    """Refresh low-cardinality gauges without exposing tenant identifiers."""
    tenant_id = settings.metrics_tenant_id
    tasks = repository.list_tasks(tenant_id)
    task_counts = ValueCounter(str(item.status.value) for item in tasks)
    statuses = ("queued", "running", "retry_wait", "completed", "dead_letter", "cancelled")
    for status in statuses:
        RUNTIME_TASKS.labels(status=status).set(task_counts[status])
    DEAD_LETTERS.set(task_counts["dead_letter"])
    UNKNOWN_RECONCILIATIONS.set(len(repository.list_reconciliations(tenant_id)))

    handoff_counts = ValueCounter(item.status for item in repository.list_handoffs(tenant_id))
    for status in ("queued", "claimed", "resolved"):
        HANDOFFS.labels(status=status).set(handoff_counts[status])

    now = datetime.now(UTC)
    worker_counts = ValueCounter()
    for worker in repository.list_workers():
        heartbeat = worker.get("last_heartbeat_at")
        if isinstance(heartbeat, str):
            heartbeat = datetime.fromisoformat(heartbeat)
        state = "online" if heartbeat and (now - heartbeat).total_seconds() < 30 else "lost"
        if worker.get("current_task_id") and state == "online":
            state = "busy"
        worker_counts[state] += 1
    for state in ("online", "busy", "lost"):
        WORKERS.labels(state=state).set(worker_counts[state])

    traces = repository.list_llm_traces(tenant_id)
    failure_counts = ValueCounter(item.error_code for item in traces if item.error_code)
    codes = (
        "LLM_TIMEOUT", "LLM_RATE_LIMITED", "LLM_INVALID_JSON", "LLM_SCHEMA_INVALID",
        "LLM_CITATION_VIOLATION", "LLM_FACT_VIOLATION", "AGENT_BUDGET_EXCEEDED",
    )
    for code in codes:
        LLM_FAILURES.labels(error_code=code).set(failure_counts[code])
