from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import (
    Base,
    ChannelIdentityRow,
    CustomerRow,
    OrderRow,
    ShipmentRow,
    SupportQueueMemberRow,
    SupportQueueRow,
    TenantMemberRow,
    TenantRow,
)
from app.repositories.postgres import PostgresRepository


def _integration_enabled() -> bool:
    return os.getenv("RUN_POSTGRES_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    if not _integration_enabled():
        pytest.skip("Set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests")
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+psycopg://market_agent:market_agent@localhost:5433/market_agent",
    )
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")

    schema = f"test_market_{uuid4().hex}"
    admin_engine = create_engine(database_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    with admin_engine.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": f"-csearch_path={schema},public"},
    )
    # The test search path also exposes public for the vector extension. With
    # checkfirst=True, PostgreSQL reports public tables as visible and
    # SQLAlchemy can incorrectly skip creating the isolated schema tables.
    # The schema is unique and empty, so force creation in its first path item.
    Base.metadata.create_all(engine, checkfirst=False)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture(scope="session")
def postgres_session_factory(postgres_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=postgres_engine, expire_on_commit=False, class_=Session)


@pytest.fixture(scope="session", autouse=True)
def seed_postgres(postgres_session_factory: sessionmaker[Session]) -> None:
    with postgres_session_factory.begin() as session:
        session.add(TenantRow(id="tenant_contract", name="Contract Test Tenant"))
        session.add(TenantRow(id="tenant_other", name="Other Tenant"))
        # These models intentionally avoid ORM relationships. Flush each
        # dependency level so SQLAlchemy cannot reorder rows across FK levels.
        session.flush()
        session.add(
            CustomerRow(
                id="customer_contract",
                tenant_id="tenant_contract",
                display_name="Contract Customer",
            )
        )
        session.add(
            TenantMemberRow(
                tenant_id="tenant_contract",
                subject="subject_contract",
                role="customer",
            )
        )
        session.add(
            SupportQueueRow(
                id="queue_contract", tenant_id="tenant_contract", name="Contract Queue"
            )
        )
        session.flush()
        session.add(
            ChannelIdentityRow(
                id="identity_contract",
                tenant_id="tenant_contract",
                customer_id="customer_contract",
                channel="oidc",
                channel_user_id="subject_contract",
            )
        )
        session.add(
            SupportQueueMemberRow(
                tenant_id="tenant_contract",
                queue_id="queue_contract",
                subject="subject_agent_contract",
            )
        )
        session.add(
            OrderRow(
                id="db_order_contract",
                tenant_id="tenant_contract",
                customer_id="customer_contract",
                external_order_id="order_9001",
                status="shipped",
                total_amount=88.0,
            )
        )
        session.flush()
        session.add(
            ShipmentRow(
                id="shipment_contract",
                tenant_id="tenant_contract",
                order_id="db_order_contract",
                status="in_transit",
                tracking_number="TRACK-9001",
                events=[],
            )
        )


@pytest.fixture
def postgres_repository(
    postgres_session_factory: sessionmaker[Session],
) -> PostgresRepository:
    return PostgresRepository(postgres_session_factory)
