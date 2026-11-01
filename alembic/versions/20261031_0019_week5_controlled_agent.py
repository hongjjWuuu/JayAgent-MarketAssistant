"""Week 5 controlled agent facts and trace linkage.

Revision ID: 20261031_0019
Revises: 20261030_0018
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261031_0019"
down_revision: str | None = "20261030_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column("llm_trace", sa.Column("fallback_reason", sa.String(120), nullable=True))
    op.add_column("llm_trace", sa.Column("agent_run_id", sa.String(80), nullable=True))
    op.create_index("ix_llm_trace_agent_run_id", "llm_trace", ["agent_run_id"])
    op.create_table(
        "compensation_proposal",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("customer_id", sa.String(80), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("conversation_id", sa.String(80), nullable=False),
        sa.Column("retrieval_snapshot_id", sa.String(80), nullable=True),
        sa.Column("policy_version_id", sa.String(80), nullable=True),
        sa.Column("recommended_amount", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="proposed"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("recommended_amount > 0 AND recommended_amount <= 50", name="ck_compensation_proposal_amount"),
        sa.CheckConstraint("status IN ('proposed','submitted','expired','cancelled')", name="ck_compensation_proposal_status"),
    )
    for column in ("tenant_id", "customer_id", "order_id", "conversation_id", "expires_at"):
        op.create_index(f"ix_compensation_proposal_{column}", "compensation_proposal", [column])
    op.create_table(
        "agent_run",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("conversation_id", sa.String(80), nullable=True),
        sa.Column("message_id", sa.String(80), nullable=True),
        sa.Column("task_id", sa.String(80), nullable=True),
        sa.Column("request_id", sa.String(160), nullable=False, server_default=""),
        sa.Column("provider", sa.String(80), nullable=False, server_default=""),
        sa.Column("model", sa.String(160), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(80), nullable=False, server_default=""),
        sa.Column("graph_version", sa.String(80), nullable=False, server_default="week5-v1"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("intent", sa.String(40), nullable=False, server_default=""),
        sa.Column("risk", sa.String(40), nullable=False, server_default=""),
        sa.Column("result_kind", sa.String(80), nullable=False, server_default=""),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_reason", sa.String(120), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("retrieval_snapshot_id", sa.String(80), nullable=True),
        sa.Column("action_plan_id", sa.String(80), nullable=True),
        sa.Column("handoff_id", sa.String(80), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("input_redacted", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("output_redacted", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("tenant_id", "conversation_id", "task_id", "request_id"):
        op.create_index(f"ix_agent_run_{column}", "agent_run", [column])


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_table("agent_run")
    op.drop_table("compensation_proposal")
    op.drop_index("ix_llm_trace_agent_run_id", table_name="llm_trace")
    op.drop_column("llm_trace", "agent_run_id")
    op.drop_column("llm_trace", "fallback_reason")
