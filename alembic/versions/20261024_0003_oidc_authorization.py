"""OIDC identity mapping and support queue authorization.

Revision ID: 20261024_0003
Revises: 20261022_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261024_0003"
down_revision: str | None = "20261022_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.create_check_constraint(
        "ck_tenant_member_role",
        "tenant_member",
        "role IN ('customer', 'agent', 'tenant_admin', 'platform_admin')",
    )
    op.create_table(
        "support_queue_member",
        sa.Column("tenant_id", sa.String(length=80), nullable=False),
        sa.Column("queue_id", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["queue_id"], ["support_queue.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "queue_id", "subject"),
    )
    op.create_index(
        "ix_support_queue_member_subject",
        "support_queue_member",
        ["tenant_id", "subject", "active"],
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_index("ix_support_queue_member_subject", table_name="support_queue_member")
    op.drop_table("support_queue_member")
    op.drop_constraint("ck_tenant_member_role", "tenant_member", type_="check")
