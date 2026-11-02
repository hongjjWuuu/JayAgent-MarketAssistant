"""M4 internal pilot evidence and feedback facts.

Revision ID: 20261102_0020
Revises: 20261031_0019
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261102_0020"
down_revision: str | None = "20261031_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.create_table(
        "pilot_release",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("git_sha", sa.String(40), nullable=False),
        sa.Column("image_digests", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("llm_model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("graph_version", sa.String(80), nullable=False),
        sa.Column("retrieval_profile_id", sa.String(80), nullable=False),
        sa.Column("policy_corpus_hash", sa.String(64), nullable=False),
        sa.Column("dataset_version", sa.String(80), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("connector_mode", sa.String(30), nullable=False),
        sa.Column("alembic_revision", sa.String(80), nullable=False),
        sa.Column("input_cost_per_million", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_cost_per_million", sa.Float(), nullable=False, server_default="0"),
        sa.Column("config_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "version", name="uq_pilot_release_tenant_version"),
        sa.CheckConstraint("status IN ('draft','frozen','closed')", name="ck_pilot_release_status"),
    )
    op.create_index("ix_pilot_release_tenant_id", "pilot_release", ["tenant_id"])
    op.create_index("ix_pilot_release_status", "pilot_release", ["status"])
    op.create_table(
        "pilot_participant",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("pilot_id", sa.String(80), sa.ForeignKey("pilot_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_code", sa.String(20), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("subject", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "pilot_id", "participant_code", name="uq_pilot_participant_code"),
        sa.UniqueConstraint("tenant_id", "pilot_id", "subject", name="uq_pilot_participant_subject"),
        sa.CheckConstraint("role IN ('customer','agent','tenant_admin')", name="ck_pilot_participant_role"),
    )
    op.create_index("ix_pilot_participant_tenant_id", "pilot_participant", ["tenant_id"])
    op.create_index("ix_pilot_participant_pilot_id", "pilot_participant", ["pilot_id"])
    op.create_table(
        "pilot_task_assignment",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("pilot_id", sa.String(80), sa.ForeignKey("pilot_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_code", sa.String(20), nullable=False),
        sa.Column("scenario", sa.String(40), nullable=False),
        sa.Column("expected_outcome", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="assigned"),
        sa.Column("conversation_id", sa.String(80), nullable=True),
        sa.Column("handoff_id", sa.String(80), nullable=True),
        sa.Column("ticket_id", sa.String(80), nullable=True),
        sa.Column("failure_category", sa.String(60), nullable=True),
        sa.Column("notes_redacted", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('assigned','in_progress','completed','failed','abandoned')", name="ck_pilot_task_status"),
    )
    for column in ("tenant_id", "pilot_id", "participant_code", "scenario", "status", "conversation_id"):
        op.create_index(f"ix_pilot_task_assignment_{column}", "pilot_task_assignment", [column])
    op.create_table(
        "pilot_feedback",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("pilot_id", sa.String(80), sa.ForeignKey("pilot_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignment_id", sa.String(80), sa.ForeignKey("pilot_task_assignment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("conversation_id", sa.String(80), nullable=False),
        sa.Column("participant_code", sa.String(20), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("confusion_tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("comment_redacted", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "pilot_id", "conversation_id", "participant_code", name="uq_pilot_feedback_conversation_participant"),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="ck_pilot_feedback_rating"),
    )
    for column in ("tenant_id", "pilot_id", "assignment_id", "conversation_id", "participant_code"):
        op.create_index(f"ix_pilot_feedback_{column}", "pilot_feedback", [column])
    op.create_table(
        "pilot_metric_snapshot",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("pilot_id", sa.String(80), sa.ForeignKey("pilot_release.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("safety", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pilot_metric_snapshot_tenant_id", "pilot_metric_snapshot", ["tenant_id"])
    op.create_index("ix_pilot_metric_snapshot_pilot_id", "pilot_metric_snapshot", ["pilot_id"])
    op.create_index("ix_pilot_metric_snapshot_created_at", "pilot_metric_snapshot", ["created_at"])


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_table("pilot_metric_snapshot")
    op.drop_table("pilot_feedback")
    op.drop_table("pilot_task_assignment")
    op.drop_table("pilot_participant")
    op.drop_table("pilot_release")
