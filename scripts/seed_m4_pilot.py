from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

from app.persistence.database import get_session_factory
from app.persistence.models import (
    ChannelIdentityRow,
    CustomerRow,
    OrderItemRow,
    OrderRow,
    ShipmentRow,
    SupportQueueMemberRow,
    TenantMemberRow,
)
from app.pilot_dataset import (
    PILOT_ADMIN_IDENTITY,
    PILOT_AGENT_IDENTITIES,
    PILOT_DATASET_VERSION,
    PILOT_IDENTITIES,
    build_m4_orders,
    m4_dataset_hash,
)


def add_if_missing(session, model, values: dict, conflict_columns: list[str]) -> None:
    session.execute(
        insert(model)
        .values(**values)
        .on_conflict_do_nothing(index_elements=conflict_columns)
    )


def main() -> None:
    orders = build_m4_orders()
    factory = get_session_factory()
    with factory.begin() as session:
        for code, customer_id, subject in PILOT_IDENTITIES:
            add_if_missing(
                session,
                CustomerRow,
                {
                    "id": customer_id,
                    "tenant_id": "tenant_demo",
                    "display_name": f"Pilot {code}",
                    "profile": {"pilot_code": code, "synthetic": True},
                },
                ["id"],
            )
            add_if_missing(
                session,
                TenantMemberRow,
                {
                    "tenant_id": "tenant_demo",
                    "subject": subject,
                    "role": "customer",
                    "active": True,
                },
                ["tenant_id", "subject"],
            )
            add_if_missing(
                session,
                ChannelIdentityRow,
                {
                    "id": f"identity_oidc_{code.lower()}",
                    "tenant_id": "tenant_demo",
                    "customer_id": customer_id,
                    "channel": "oidc",
                    "channel_user_id": subject,
                },
                ["tenant_id", "channel", "channel_user_id"],
            )
        for _, _, subject in PILOT_AGENT_IDENTITIES:
            add_if_missing(
                session,
                TenantMemberRow,
                {
                    "tenant_id": "tenant_demo",
                    "subject": subject,
                    "role": "agent",
                    "active": True,
                },
                ["tenant_id", "subject"],
            )
            add_if_missing(
                session,
                SupportQueueMemberRow,
                {
                    "tenant_id": "tenant_demo",
                    "queue_id": "queue_general",
                    "subject": subject,
                    "active": True,
                },
                ["tenant_id", "queue_id", "subject"],
            )
        _, _, admin_subject = PILOT_ADMIN_IDENTITY
        add_if_missing(
            session,
            TenantMemberRow,
            {
                "tenant_id": "tenant_demo",
                "subject": admin_subject,
                "role": "tenant_admin",
                "active": True,
            },
            ["tenant_id", "subject"],
        )
        for value in orders:
            internal_order_id = f"m4_{value['order_id'].lower()}"
            add_if_missing(
                session,
                OrderRow,
                {
                    "id": internal_order_id,
                    "tenant_id": value["tenant_id"],
                    "customer_id": value["customer_id"],
                    "external_order_id": value["order_id"],
                    "status": value["status"],
                    "total_amount": value["total_amount"],
                    "currency": "CNY",
                },
                ["id"],
            )
            add_if_missing(
                session,
                OrderItemRow,
                {
                    "id": f"item_{internal_order_id}",
                    "tenant_id": value["tenant_id"],
                    "order_id": internal_order_id,
                    "sku": value["sku"],
                    "name": value["item_name"],
                    "quantity": 1,
                },
                ["id"],
            )
            add_if_missing(
                session,
                ShipmentRow,
                {
                    "id": f"shipment_{internal_order_id}",
                    "tenant_id": value["tenant_id"],
                    "order_id": internal_order_id,
                    "status": value["shipment_status"],
                    "tracking_number": value["tracking_number"],
                    "events": value["events"],
                },
                ["id"],
            )
    print(
        f"{PILOT_DATASET_VERSION} ready: {len(PILOT_IDENTITIES)} customers, "
        f"{len(orders)} orders, sha256={m4_dataset_hash()}"
    )


if __name__ == "__main__":
    main()
