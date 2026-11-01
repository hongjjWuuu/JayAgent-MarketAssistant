from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.oidc_integration


def _enabled() -> bool:
    return os.getenv("RUN_OIDC_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.fixture(scope="module")
def endpoints():
    if not _enabled():
        pytest.skip("Set RUN_OIDC_TESTS=1 to run real Keycloak integration tests")
    return (
        os.getenv("KEYCLOAK_URL", "http://localhost:8080"),
        os.getenv("API_URL", "http://localhost:8000"),
    )


def access_token(keycloak_url: str, username: str) -> str:
    response = httpx.post(
        f"{keycloak_url}/realms/market-assistant/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "market-assistant-web",
            "username": username,
            "password": "demo1234",
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["access_token"]


@pytest.mark.parametrize(
    ("username", "role", "tenant"),
    [
        ("customer_001", "customer", "tenant_demo"),
        ("agent_001", "agent", "tenant_demo"),
        ("admin_001", "tenant_admin", "tenant_demo"),
        ("platform_admin_001", "platform_admin", "tenant_platform"),
    ],
)
def test_real_keycloak_identities(endpoints, username, role, tenant):
    keycloak_url, api_url = endpoints
    token = access_token(keycloak_url, username)
    response = httpx.get(
        f"{api_url}/api/v1/auth/me",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": "attacker-tenant",
            "X-Role": "platform_admin",
        },
        timeout=20,
    )
    assert response.status_code == 200, response.text
    assert response.json()["role"] == role
    assert response.json()["tenant_id"] == tenant


def test_platform_admin_has_no_tenant_business_access(endpoints):
    keycloak_url, api_url = endpoints
    headers = {"Authorization": f"Bearer {access_token(keycloak_url, 'platform_admin_001')}"}
    assert httpx.get(f"{api_url}/api/v1/admin/workers", headers=headers).status_code == 200
    assert httpx.get(f"{api_url}/api/v1/conversations", headers=headers).status_code == 403
    assert httpx.get(f"{api_url}/api/v1/admin/runtime-tasks", headers=headers).status_code == 403
