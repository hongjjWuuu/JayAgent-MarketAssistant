from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.domain.models import CompensationProposal, RuntimeTask, RuntimeTaskStatus, utc_now
from app.runtime.worker import TaskWorker


def create_conversation(client, headers):
    response = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"customer_id": headers["X-User-ID"], "channel": "web"},
    )
    assert response.status_code == 201
    return response.json()["conversation_id"]


def test_health_and_metrics_do_not_require_identity(client):
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json()["status"] == "ready"
    assert client.get("/metrics").status_code == 200


def test_api_requires_identity_headers(client):
    response = client.post(
        "/api/v1/conversations", json={"customer_id": "customer_001", "channel": "web"}
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTHENTICATION_FAILED"


def test_development_role_cannot_be_forged(client):
    response = client.get(
        "/api/v1/conversations",
        headers={
            "X-Tenant-ID": "tenant_demo",
            "X-User-ID": "customer_001",
            "X-Role": "tenant_admin",
        },
    )
    assert response.status_code == 403


def test_message_is_queued_and_worker_returns_owned_order(client, customer_headers, store):
    conversation_id = create_conversation(client, customer_headers)
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=customer_headers,
        json={"content": "请查询 order_1001 的物流"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert TaskWorker(store, "test-worker").run_once()
    body = client.get(f"/api/v1/conversations/{conversation_id}", headers=customer_headers).json()
    assert body["messages"][-1]["facts"]["kind"] == "order_fact"
    assert body["messages"][-1]["facts"]["order"]["tracking_number"] == "TRACK-1001"


def test_cross_tenant_conversation_is_not_disclosed(client, customer_headers, store):
    conversation_id = create_conversation(client, customer_headers)
    store.members[("tenant_other", "customer_001")] = "customer"
    response = client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers={"X-Tenant-ID": "tenant_other", "X-User-ID": "customer_001", "X-Role": "customer"},
    )
    assert response.status_code == 404


def test_ticket_confirmation_is_atomic_and_worker_executes_once(client, customer_headers, store):
    conversation_id = create_conversation(client, customer_headers)
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "create_ticket",
            "payload": {"title": "商品破损", "description": "包装破损"},
            "idempotency_key": "ticket-order-1001-damaged",
        },
    ).json()

    def confirm():
        return client.post(
            f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
            headers=customer_headers,
            json={"confirmation": "confirm"},
        ).json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: confirm(), range(2)))
    assert first["execution_id"] == second["execution_id"]
    assert first["status"] == "queued"
    worker = TaskWorker(store, "action-worker")
    assert worker.run_once()
    execution = client.get(
        f"/api/v1/action-executions/{first['execution_id']}", headers=customer_headers
    ).json()
    assert execution["status"] == "succeeded"
    assert len(client.get("/api/v1/tickets", headers=customer_headers).json()) == 1


def test_handoff_claim_reply_resolve_flow(client, customer_headers, agent_headers):
    conversation_id = create_conversation(client, customer_headers)
    handoff = client.post(
        f"/api/v1/conversations/{conversation_id}/handoff",
        headers=customer_headers,
        json={"reason": "user_requested_human"},
    ).json()
    claim = client.post(f"/api/v1/handoffs/{handoff['handoff_id']}/claim", headers=agent_headers)
    assert claim.status_code == 200
    reply = client.post(
        f"/api/v1/handoffs/{handoff['handoff_id']}/reply",
        headers=agent_headers,
        json={"content": "您好，我已接管并看到完整上下文。"},
    )
    assert reply.status_code == 201
    resolved = client.post(
        f"/api/v1/handoffs/{handoff['handoff_id']}/resolve",
        headers=agent_headers,
        json={"resume_ai": False},
    )
    assert resolved.json()["status"] == "resolved"


def test_handoff_transfer_and_pilot_metrics(client, customer_headers, agent_headers, admin_headers):
    conversation_id = create_conversation(client, customer_headers)
    created = client.post(
        f"/api/v1/conversations/{conversation_id}/handoff",
        headers=customer_headers,
        json={"reason": "user_requested"},
    ).json()
    claimed = client.post(
        f"/api/v1/handoffs/{created['handoff_id']}/claim", headers=agent_headers
    )
    assert claimed.status_code == 200
    transferred = client.post(
        f"/api/v1/handoffs/{created['handoff_id']}/transfer",
        headers=agent_headers,
        json={"queue_id": "queue_general", "reason": "shift handover"},
    )
    assert transferred.status_code == 200
    assert transferred.json()["status"] == "queued"
    assert transferred.json()["context_snapshot"]["transfer_history"][0]["reason"] == (
        "shift handover"
    )
    metrics = client.get("/api/v1/admin/pilot-metrics", headers=admin_headers)
    assert metrics.status_code == 200
    assert metrics.json()["conversation_count"] == 1
    assert metrics.json()["handoff_rate"] == 1


def test_only_published_current_policy_can_answer(client, customer_headers, admin_headers, store):
    draft = client.post(
        "/api/v1/admin/policies",
        headers=admin_headers,
        json={
            "scope": "returns",
            "version": "v1",
            "title": "退货政策",
            "content": "商品签收后七天内可以申请退货，破损商品需要提供照片。",
        },
    ).json()
    client.post(
        f"/api/v1/admin/policies/{draft['policy_version_id']}/publish", headers=admin_headers
    )
    conversation_id = create_conversation(client, customer_headers)
    client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers=customer_headers,
        json={"content": "商品签收后七天可以退货吗"},
    )
    TaskWorker(store, "policy-worker").run_once()
    conversation = client.get(
        f"/api/v1/conversations/{conversation_id}", headers=customer_headers
    ).json()
    assert conversation["messages"][-1]["facts"]["kind"] == "policy_answer"
    assert conversation["messages"][-1]["facts"]["citations"][0]["version"] == "v1"


def test_admin_evaluation_is_tenant_scoped(client, admin_headers):
    response = client.post("/api/v1/admin/evaluations/run", headers=admin_headers)
    assert response.status_code == 202
    run = response.json()
    assert run["tenant_id"] == "tenant_demo"
    assert run["quality_status"] == "pending"
    assert "cross_tenant_leakage" not in run["summary"]


def test_compensation_only_creates_human_approval_ticket(client, customer_headers, store):
    conversation_id = create_conversation(client, customer_headers)
    proposal = store.save_compensation_proposal(
        CompensationProposal(
            proposal_id="proposal_api_compensation",
            tenant_id="tenant_demo",
            customer_id="customer_001",
            order_id="order_1001",
            conversation_id=conversation_id,
            recommended_amount=20,
            reason="商品破损",
            expires_at=utc_now() + timedelta(minutes=30),
        )
    )
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "submit_compensation",
            "payload": {"proposal_id": proposal.proposal_id},
            "idempotency_key": "compensation-order-1001-v1",
        },
    ).json()
    execution = client.post(
        f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
        headers=customer_headers,
        json={"confirmation": "confirm"},
    ).json()
    TaskWorker(store, "compensation-worker").run_once()
    result = client.get(
        f"/api/v1/action-executions/{execution['execution_id']}", headers=customer_headers
    ).json()
    assert result["receipt"]["status"] == "pending_human_review"
    ticket = client.get("/api/v1/tickets", headers=customer_headers).json()[0]
    assert ticket["title"] == "补偿人工审批"
    assert ticket["priority"] == "high"


def test_external_write_timeout_requires_reconciliation(client, customer_headers, store):
    conversation_id = create_conversation(client, customer_headers)
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "create_ticket",
            "payload": {"description": "网络超时演练", "simulate_timeout": True},
            "idempotency_key": "timeout-reconciliation-v1",
        },
    ).json()
    execution = client.post(
        f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
        headers=customer_headers,
        json={"confirmation": "confirm"},
    ).json()
    TaskWorker(store, "timeout-worker").run_once()
    result = client.get(
        f"/api/v1/action-executions/{execution['execution_id']}", headers=customer_headers
    ).json()
    assert result["status"] == "unknown_needs_reconciliation"
    assert result["receipt"]["reconciliation"] == "required"
    assert client.get("/api/v1/tickets", headers=customer_headers).json() == []


def test_dead_letter_is_admin_visible_and_replay_is_immutable(client, admin_headers, store):
    original = RuntimeTask(
        task_id="task_dead_api",
        tenant_id="tenant_demo",
        task_type="unsupported",
        payload={"secret": "must-not-leak", "safe": "visible-in-detail"},
        max_attempts=1,
    )
    store.enqueue_task(original)
    claimed = store.claim_task("worker-dead", 30)
    store.fail_task(claimed, "permanent error", "worker-dead")

    listing = client.get("/api/v1/admin/runtime-tasks?status=dead_letter", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.json()[0]["payload"] == {"keys": ["safe", "secret"]}

    detail = client.get("/api/v1/admin/runtime-tasks/task_dead_api", headers=admin_headers).json()
    assert detail["payload"]["secret"] == "***"
    assert [event["event_type"] for event in detail["events"]] == [
        "queued",
        "claimed",
        "dead_letter",
    ]

    first = client.post(
        "/api/v1/admin/runtime-tasks/task_dead_api/replay",
        headers=admin_headers,
        json={"reason": "修复数据后重放"},
    )
    second = client.post(
        "/api/v1/admin/runtime-tasks/task_dead_api/replay",
        headers=admin_headers,
        json={"reason": "并发请求不得重复"},
    )
    assert first.status_code == 201
    assert first.json()["task_id"] == second.json()["task_id"]
    assert store.get_task("task_dead_api", "tenant_demo").status == RuntimeTaskStatus.DEAD_LETTER


def test_non_admin_cannot_manage_runtime_tasks(client, customer_headers):
    response = client.get("/api/v1/admin/runtime-tasks", headers=customer_headers)
    assert response.status_code == 403


def test_other_tenant_cannot_view_cancel_or_replay_runtime_task(client, store):
    store.members[("tenant_other", "other_admin")] = "tenant_admin"
    headers = {
        "X-Tenant-ID": "tenant_other",
        "X-User-ID": "other_admin",
        "X-Role": "tenant_admin",
    }
    store.enqueue_task(
        RuntimeTask(
            task_id="task_private_tenant",
            tenant_id="tenant_demo",
            task_type="private",
            payload={},
        )
    )
    assert (
        client.get("/api/v1/admin/runtime-tasks/task_private_tenant", headers=headers).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/admin/runtime-tasks/task_private_tenant/cancel", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/admin/runtime-tasks/task_private_tenant/replay",
            headers=headers,
            json={"reason": "越权请求必须失败"},
        ).status_code
        == 404
    )


def test_worker_details_require_platform_admin(client, admin_headers, store):
    TaskWorker(store, "visible-platform-worker")
    assert client.get("/api/v1/admin/workers", headers=admin_headers).status_code == 403
    store.members[("tenant_demo", "platform_001")] = "platform_admin"
    response = client.get(
        "/api/v1/admin/workers",
        headers={
            "X-Tenant-ID": "tenant_demo",
            "X-User-ID": "platform_001",
            "X-Role": "platform_admin",
        },
    )
    assert response.status_code == 200
    assert response.json()[0]["status"] == "online"
    summary = client.get("/api/v1/admin/runtime-summary", headers=admin_headers)
    assert summary.status_code == 200
    assert summary.json()["workers"]["online"] == 1


def test_reconciliation_lookup_marks_external_success(
    client, customer_headers, admin_headers, store
):
    conversation_id = create_conversation(client, customer_headers)
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "create_ticket",
            "payload": {
                "description": "响应丢失但外部已经成功",
                "simulate_timeout": True,
                "reconciliation_outcome": "succeeded",
            },
            "idempotency_key": "timeout-reconciliation-success-v1",
        },
    ).json()
    execution = client.post(
        f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
        headers=customer_headers,
        json={"confirmation": "confirm"},
    ).json()
    worker = TaskWorker(store, "reconciliation-worker")
    assert worker.run_once()
    reconcile_task = next(
        task for task in store.runtime_tasks.values() if task.task_type == "reconcile_execution"
    )
    reconcile_task.available_at = utc_now()
    assert worker.run_once()

    result = client.get(
        f"/api/v1/action-executions/{execution['execution_id']}", headers=customer_headers
    ).json()
    assert result["status"] == "succeeded"
    assert result["receipt"]["lookup_only"] is True
    assert client.get("/api/v1/admin/reconciliations", headers=admin_headers).json() == []


def test_action_failure_transitions_to_retrying_then_succeeds(
    client, customer_headers, store, monkeypatch
):
    conversation_id = create_conversation(client, customer_headers)
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "create_ticket",
            "payload": {"description": "首次 Connector 故障"},
            "idempotency_key": "action-retry-transition-v1",
        },
    ).json()
    execution = client.post(
        f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
        headers=customer_headers,
        json={"confirmation": "confirm"},
    ).json()
    original_save = store.save_ticket

    def fail_once(_ticket):
        raise RuntimeError("connector unavailable")

    monkeypatch.setattr(store, "save_ticket", fail_once)
    worker = TaskWorker(store, "retry-worker")
    assert worker.run_once()
    failed_once = store.execution_for_key("tenant_demo", "action-retry-transition-v1")
    assert failed_once.status == "retrying"

    monkeypatch.setattr(store, "save_ticket", original_save)
    queued = store.get_task(execution["task_id"], "tenant_demo")
    queued.available_at = utc_now()
    assert worker.run_once()
    recovered = store.execution_for_key("tenant_demo", "action-retry-transition-v1")
    assert recovered.status == "succeeded"


def test_pending_reconciliation_dead_letters_without_replaying_write(
    client, customer_headers, store
):
    conversation_id = create_conversation(client, customer_headers)
    plan = client.post(
        "/api/v1/action-plans",
        headers=customer_headers,
        json={
            "conversation_id": conversation_id,
            "action_type": "create_ticket",
            "payload": {"simulate_timeout": True, "reconciliation_outcome": "pending"},
            "idempotency_key": "pending-reconciliation-no-write-v1",
        },
    ).json()
    execution = client.post(
        f"/api/v1/action-plans/{plan['action_plan_id']}/confirm",
        headers=customer_headers,
        json={"confirmation": "confirm"},
    ).json()
    worker = TaskWorker(store, "pending-reconcile-worker")
    assert worker.run_once()
    reconcile_task = next(
        task for task in store.runtime_tasks.values() if task.task_type == "reconcile_execution"
    )
    for _ in range(reconcile_task.max_attempts):
        reconcile_task.available_at = utc_now()
        assert worker.run_once()
        assert store.list_tickets("tenant_demo", "customer_001") == []
    assert reconcile_task.status == RuntimeTaskStatus.DEAD_LETTER
    persisted = store.execution_for_key("tenant_demo", "pending-reconciliation-no-write-v1")
    assert persisted.execution_id == execution["execution_id"]
    assert persisted.status == "unknown_needs_reconciliation"
