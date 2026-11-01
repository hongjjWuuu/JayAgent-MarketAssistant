"""Complete LLM trace metadata.

Revision ID: 20261027_0008
Revises: 20261027_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261027_0008"
down_revision: str | None = "20261027_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    columns = (
        sa.Column("message_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("provider", sa.String(80), nullable=False, server_default=""),
        sa.Column("prompt_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("call_type", sa.String(80), nullable=False, server_default="classify_intent"),
        sa.Column("status", sa.String(30), nullable=False, server_default="succeeded"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("budget", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("allowed_citation_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    for column in columns:
        op.add_column("llm_trace", column)
    op.create_index("ix_llm_trace_task_id", "llm_trace", ["task_id"])
    op.create_index("ix_llm_trace_request_id", "llm_trace", ["request_id"])


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_index("ix_llm_trace_request_id", table_name="llm_trace")
    op.drop_index("ix_llm_trace_task_id", table_name="llm_trace")
    for name in (
        "allowed_citation_ids", "budget", "error_code", "status", "call_type",
        "prompt_hash", "provider", "request_id", "task_id", "message_id",
    ):
        op.drop_column("llm_trace", name)
