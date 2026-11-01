"""Stable policy identity and metric v3 foundation.

Revision ID: 20261027_0006
Revises: 20261026_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261027_0006"
down_revision: str | None = "20261026_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column("policy_version", sa.Column("policy_id", sa.String(80), nullable=True))
    op.execute(
        """
        UPDATE policy_version
        SET policy_id = 'policy_family_' || md5(tenant_id || ':' || scope)
        WHERE policy_id IS NULL
        """
    )
    op.alter_column("policy_version", "policy_id", nullable=False)
    op.create_index("ix_policy_version_policy_id", "policy_version", ["policy_id"])
    op.create_index(
        "ix_policy_version_tenant_family",
        "policy_version",
        ["tenant_id", "policy_id", "created_at"],
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_index("ix_policy_version_tenant_family", table_name="policy_version")
    op.drop_index("ix_policy_version_policy_id", table_name="policy_version")
    op.drop_column("policy_version", "policy_id")
