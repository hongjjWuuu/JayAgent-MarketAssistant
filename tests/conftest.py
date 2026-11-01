import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.memory import store


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()


@pytest.fixture
def store_fixture():
    return store


@pytest.fixture(name="store")
def named_store():
    return store


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def customer_headers():
    return {
        "X-Tenant-ID": "tenant_demo",
        "X-User-ID": "customer_001",
        "X-Role": "customer",
    }


@pytest.fixture
def agent_headers():
    return {"X-Tenant-ID": "tenant_demo", "X-User-ID": "agent_001", "X-Role": "agent"}


@pytest.fixture
def admin_headers():
    return {
        "X-Tenant-ID": "tenant_demo",
        "X-User-ID": "admin_001",
        "X-Role": "tenant_admin",
    }
