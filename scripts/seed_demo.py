from sqlalchemy.dialects.postgresql import insert

from app.persistence.database import get_session_factory
from app.persistence.models import (
    ChannelIdentityRow,
    CustomerRow,
    OrderItemRow,
    OrderRow,
    ShipmentRow,
    SupportQueueMemberRow,
    SupportQueueRow,
    TenantMemberRow,
    TenantRow,
)


def add_if_missing(session, model, values: dict, conflict_columns: list[str]) -> None:
    statement = insert(model).values(**values).on_conflict_do_nothing(index_elements=conflict_columns)
    session.execute(statement)


def main() -> None:
    factory = get_session_factory()
    with factory.begin() as session:
        add_if_missing(
            session,
            TenantRow,
            {"id": "tenant_demo", "name": "Jay Demo 商城", "active": True},
            ["id"],
        )
        add_if_missing(
            session,
            TenantRow,
            {"id": "tenant_platform", "name": "JayAgent 平台运维", "active": True},
            ["id"],
        )
        add_if_missing(
            session,
            CustomerRow,
            {
                "id": "customer_001",
                "tenant_id": "tenant_demo",
                "display_name": "演示客户",
                "profile": {},
            },
            ["id"],
        )
        for subject, role in (
            # Development-header identities used by local API examples/tests.
            ("customer_001", "customer"),
            ("agent_001", "agent"),
            ("admin_001", "tenant_admin"),
            # Stable Keycloak subjects imported by keycloak/realm-export.json.
            ("11111111-1111-4111-8111-111111111111", "customer"),
            ("22222222-2222-4222-8222-222222222222", "agent"),
            ("33333333-3333-4333-8333-333333333333", "tenant_admin"),
        ):
            add_if_missing(
                session,
                TenantMemberRow,
                {
                    "tenant_id": "tenant_demo",
                    "subject": subject,
                    "role": role,
                    "active": True,
                },
                ["tenant_id", "subject"],
            )
        add_if_missing(
            session,
            TenantMemberRow,
            {
                "tenant_id": "tenant_platform",
                "subject": "44444444-4444-4444-8444-444444444444",
                "role": "platform_admin",
                "active": True,
            },
            ["tenant_id", "subject"],
        )
        add_if_missing(
            session,
            SupportQueueRow,
            {"id": "queue_general", "tenant_id": "tenant_demo", "name": "综合客服"},
            ["id"],
        )
        for subject in (
            "agent_001",
            "22222222-2222-4222-8222-222222222222",
        ):
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
        add_if_missing(
            session,
            ChannelIdentityRow,
            {
                "id": "identity_oidc_customer_001",
                "tenant_id": "tenant_demo",
                "customer_id": "customer_001",
                "channel": "oidc",
                "channel_user_id": "11111111-1111-4111-8111-111111111111",
            },
            ["tenant_id", "channel", "channel_user_id"],
        )
        add_if_missing(
            session,
            OrderRow,
            {
                "id": "db_order_1001",
                "tenant_id": "tenant_demo",
                "customer_id": "customer_001",
                "external_order_id": "order_1001",
                "status": "shipped",
                "total_amount": 128.0,
                "currency": "CNY",
            },
            ["id"],
        )
        add_if_missing(
            session,
            OrderItemRow,
            {
                "id": "item_1001",
                "tenant_id": "tenant_demo",
                "order_id": "db_order_1001",
                "sku": "SKU-DEMO-1",
                "name": "演示商品",
                "quantity": 1,
            },
            ["id"],
        )
        add_if_missing(
            session,
            ShipmentRow,
            {
                "id": "shipment_1001",
                "tenant_id": "tenant_demo",
                "order_id": "db_order_1001",
                "status": "in_transit",
                "tracking_number": "TRACK-1001",
                "events": [{"status": "picked_up", "message": "包裹已揽收"}],
            },
            ["id"],
        )
    print("Demo data is present; rerunning this script is safe")


if __name__ == "__main__":
    main()
