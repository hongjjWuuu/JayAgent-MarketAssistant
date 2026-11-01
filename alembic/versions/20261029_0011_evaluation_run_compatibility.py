"""Compatibility bridge for Gate-v4 evaluation version columns.

Revision ID: 20261029_0011
Revises: 20261029_0010

The columns are created conditionally because early local 0010 installations
preceded the evaluation-run metadata addition. Fresh databases already receive
them from 0010; upgraded databases receive them here.
"""

from collections.abc import Sequence

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261029_0011"
down_revision: str | None = "20261029_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.execute("ALTER TABLE evaluation_run ADD COLUMN IF NOT EXISTS query_analyzer_version varchar(40) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE evaluation_run ADD COLUMN IF NOT EXISTS scope_match_version varchar(40) NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE evaluation_run ADD COLUMN IF NOT EXISTS threshold_grid_version varchar(80) NOT NULL DEFAULT ''")


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    # 0010 may own these columns on fresh installations, so the compatibility
    # bridge intentionally leaves them in place during a downgrade to 0010.
