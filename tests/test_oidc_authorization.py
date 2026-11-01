from app.core.config import settings
from app.core.security import oidc_validator
from app.domain.models import Handoff

CUSTOMER_SUB = "11111111-1111-4111-8111-111111111111"


def oidc_claims(subject=CUSTOMER_SUB, tenant="tenant_demo", username="renamed-user"):
    return {
        "sub": subject,
        "tenant_id": tenant,
        "preferred_username": username,
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
    }


def use_oidc(monkeypatch, claims):
    monkeypatch.setattr(settings, "auth_mode", "oidc")
    monkeypatch.setattr(oidc_validator, "validate", lambda _token: claims)


def test_auth_me_uses_stable_subject_mapping_and_ignores_spoofed_headers(
    client, monkeypatch
):
    use_oidc(monkeypatch, oidc_claims())
    response = client.get(
        "/api/v1/auth/me",
        headers={
            "Authorization": "Bearer opaque-test-token",
            "X-Tenant-ID": "tenant_other",
            "X-User-ID": "admin_001",
            "X-Role": "tenant_admin",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "subject": CUSTOMER_SUB,
        "tenant_id": "tenant_demo",
        "role": "customer",
        "username": "renamed-user",
        "customer_id": "customer_001",
        "capabilities": [
            "conversation:self",
            "order:self",
            "ticket:self",
            "action:self",
        ],
    }


def test_oidc_requires_tenant_claim_and_customer_link(client, monkeypatch, store):
    claims = oidc_claims()
    claims.pop("tenant_id")
    use_oidc(monkeypatch, claims)
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x"}).status_code == 401

    store.members[("tenant_demo", "unlinked-customer")] = "customer"
    use_oidc(monkeypatch, oidc_claims(subject="unlinked-customer"))
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer x"}).status_code == 403


def test_agent_is_limited_to_authorized_queue(client, agent_headers, store):
    allowed = Handoff(
        handoff_id="handoff_allowed",
        tenant_id="tenant_demo",
        conversation_id="conv_allowed",
        queue_id="queue_general",
        reason="allowed",
        context_snapshot={},
    )
    denied = Handoff(
        handoff_id="handoff_denied",
        tenant_id="tenant_demo",
        conversation_id="conv_denied",
        queue_id="queue_vip",
        reason="denied",
        context_snapshot={},
    )
    store.handoffs[allowed.handoff_id] = allowed
    store.handoffs[denied.handoff_id] = denied
    listing = client.get("/api/v1/agent-console/handoffs", headers=agent_headers)
    assert [item["handoff_id"] for item in listing.json()] == ["handoff_allowed"]
    claim = client.post("/api/v1/handoffs/handoff_denied/claim", headers=agent_headers)
    assert claim.status_code == 409
    assert store.handoffs["handoff_denied"].status == "queued"


def test_request_id_cannot_turn_agent_into_internal_worker(
    client, customer_headers, agent_headers
):
    created = client.post(
        "/api/v1/conversations",
        headers=customer_headers,
        json={"customer_id": "customer_001", "channel": "web"},
    ).json()
    forged = {**agent_headers, "X-Request-ID": "worker:forged"}
    response = client.post(
        f"/api/v1/conversations/{created['conversation_id']}/handoff",
        headers=forged,
        json={"reason": "forged-worker"},
    )
    assert response.status_code == 403


def test_platform_admin_cannot_read_tenant_business(client, store):
    store.members[("tenant_platform", "platform_admin_001")] = "platform_admin"
    headers = {
        "X-Tenant-ID": "tenant_platform",
        "X-User-ID": "platform_admin_001",
        "X-Role": "platform_admin",
    }
    assert client.get("/api/v1/conversations", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/runtime-tasks", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/workers", headers=headers).status_code == 200
