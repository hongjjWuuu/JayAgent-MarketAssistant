from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime


def _pilot_payload(store) -> dict:
    store.members[("tenant_demo", "pilot_customer_02")] = "customer"
    store.members[("tenant_demo", "pilot_customer_03")] = "customer"
    participants = [
        {
            "participant_code": "P01",
            "role": "customer",
            "subject": "customer_001",
            "consented_at": datetime.now(UTC).isoformat(),
        },
        {
            "participant_code": "P02",
            "role": "customer",
            "subject": "pilot_customer_02",
            "consented_at": datetime.now(UTC).isoformat(),
        },
        {
            "participant_code": "P03",
            "role": "customer",
            "subject": "pilot_customer_03",
            "consented_at": datetime.now(UTC).isoformat(),
        },
        {
            "participant_code": "P06",
            "role": "agent",
            "subject": "agent_001",
            "consented_at": datetime.now(UTC).isoformat(),
        },
        {
            "participant_code": "P08",
            "role": "tenant_admin",
            "subject": "admin_001",
            "consented_at": datetime.now(UTC).isoformat(),
        },
    ]
    tasks = [
        {
            "participant_code": code,
            "scenario": scenario,
            "expected_outcome": f"{code} completes {scenario}",
        }
        for code in ("P01", "P02", "P03")
        for scenario in ("order", "shipment", "policy", "ticket", "handoff")
    ]
    empty_policy_hash = hashlib.sha256(
        json.dumps([], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return {
        "version": "v0.7.0-m4-rc1",
        "git_sha": "a" * 40,
        "image_digests": {"api": f"sha256:{'b' * 64}", "web": f"sha256:{'c' * 64}"},
        "llm_model": "deepseek-flash",
        "prompt_version": "market-agent-v1",
        "graph_version": "week5-v1",
        "retrieval_profile_id": "hash-v1",
        "policy_corpus_hash": empty_policy_hash,
        "dataset_version": "m4_pilot/v1",
        "dataset_hash": "d" * 64,
        "connector_mode": "http",
        "alembic_revision": "20261102_0020",
        "input_cost_per_million": 1.0,
        "output_cost_per_million": 2.0,
        "participants": participants,
        "tasks": tasks,
    }


def _create_pilot(client, admin_headers, store) -> str:
    response = client.post(
        "/api/v1/admin/pilots",
        headers=admin_headers,
        json=_pilot_payload(store),
    )
    assert response.status_code == 201, response.text
    return response.json()["release"]["pilot_id"]


def test_pilot_release_freezes_and_is_immutable(client, admin_headers, store):
    pilot_id = _create_pilot(client, admin_headers, store)
    frozen = client.post(f"/api/v1/admin/pilots/{pilot_id}/freeze", headers=admin_headers)
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "frozen"

    second = client.post(f"/api/v1/admin/pilots/{pilot_id}/freeze", headers=admin_headers)
    assert second.status_code == 409


def test_pilot_release_cannot_freeze_without_recorded_consent(
    client, admin_headers, store
):
    payload = _pilot_payload(store)
    payload["version"] = "v0.7.0-m4-no-consent"
    payload["participants"][0]["consented_at"] = None
    created = client.post("/api/v1/admin/pilots", headers=admin_headers, json=payload)
    assert created.status_code == 201
    pilot_id = created.json()["release"]["pilot_id"]

    frozen = client.post(f"/api/v1/admin/pilots/{pilot_id}/freeze", headers=admin_headers)

    assert frozen.status_code == 409
    assert "consent" in frozen.json()["message"].lower()


def test_customer_feedback_is_owned_redacted_and_completes_task(
    client, customer_headers, admin_headers, store
):
    pilot_id = _create_pilot(client, admin_headers, store)
    assert (
        client.post(f"/api/v1/admin/pilots/{pilot_id}/freeze", headers=admin_headers).status_code
        == 200
    )
    conversation = client.post(
        "/api/v1/conversations",
        headers=customer_headers,
        json={"customer_id": "customer_001", "channel": "web"},
    ).json()
    tasks = client.get(
        f"/api/v1/admin/pilots/{pilot_id}/tasks", headers=admin_headers
    ).json()
    assignment = next(
        item for item in tasks if item["participant_code"] == "P01" and item["scenario"] == "order"
    )
    response = client.post(
        f"/api/v1/conversations/{conversation['conversation_id']}/pilot-feedback",
        headers=customer_headers,
        json={
            "pilot_id": pilot_id,
            "assignment_id": assignment["assignment_id"],
            "completed": True,
            "rating": 5,
            "confusion_tags": [],
            "comment": "电话 13812345678，邮箱 person@example.com，地址：测试路99号。",
        },
    )
    assert response.status_code == 201, response.text
    assert "13812345678" not in response.json()["comment_redacted"]
    assert "person@example.com" not in response.json()["comment_redacted"]
    updated = client.get(
        f"/api/v1/admin/pilots/{pilot_id}/tasks", headers=admin_headers
    ).json()
    completed = next(item for item in updated if item["assignment_id"] == assignment["assignment_id"])
    assert completed["status"] == "completed"
    # Tenant history must not inflate a release-specific report.
    client.post(
        "/api/v1/conversations",
        headers=customer_headers,
        json={"customer_id": "customer_001", "channel": "unrelated"},
    )
    metrics = client.get(
        f"/api/v1/admin/pilots/{pilot_id}/metrics", headers=admin_headers
    ).json()["metrics"]
    assert metrics["conversation_count"] == 1
    assert metrics["unsafe_write_count"] == 0


def test_platform_and_other_tenant_cannot_read_pilot(client, admin_headers, store):
    pilot_id = _create_pilot(client, admin_headers, store)
    store.members[("tenant_other", "other_admin")] = "tenant_admin"
    response = client.get(
        f"/api/v1/admin/pilots/{pilot_id}",
        headers={
            "X-Tenant-ID": "tenant_other",
            "X-User-ID": "other_admin",
            "X-Role": "tenant_admin",
        },
    )
    assert response.status_code == 404


def test_m4_dataset_is_stable_and_has_30_owned_orders():
    from app.pilot_dataset import PILOT_IDENTITIES, build_m4_orders, m4_dataset_hash

    orders = build_m4_orders()
    assert len(orders) == 30
    assert len({item["order_id"] for item in orders}) == 30
    assert all(sum(item["customer_id"] == customer for item in orders) == 6 for _, customer, _ in PILOT_IDENTITIES)
    assert m4_dataset_hash() == m4_dataset_hash()
    assert len(m4_dataset_hash()) == 64
