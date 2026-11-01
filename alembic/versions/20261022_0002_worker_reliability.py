"""Worker reliability, replay, and reconciliation fields.

Revision ID: 20261022_0002
Revises: 20261020_0001
"""

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision = "20261022_0002"
down_revision = "20261020_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column("action_execution", sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.add_column("action_execution", sa.Column("reconciliation_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("action_execution", sa.Column("last_reconciled_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE action_execution SET updated_at = created_at WHERE updated_at IS NULL")
    op.alter_column("action_execution", "updated_at", nullable=False)

    op.add_column("runtime_task", sa.Column("replay_of_task_id", sa.String(80)))
    op.add_column("runtime_task", sa.Column("replayed_by", sa.String(160)))
    op.add_column("runtime_task", sa.Column("replay_reason", sa.String(500)))
    op.add_column("runtime_task", sa.Column("dead_lettered_at", sa.DateTime(timezone=True)))
    op.add_column("runtime_task", sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_runtime_task_replay_of",
        "runtime_task",
        "runtime_task",
        ["replay_of_task_id"],
        ["id"],
    )
    op.create_unique_constraint("uq_runtime_task_replay_once", "runtime_task", ["replay_of_task_id"])

    op.add_column("runtime_worker", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("runtime_worker", sa.Column("current_task_id", sa.String(80)))
    op.add_column("runtime_worker", sa.Column("hostname", sa.String(255), nullable=False, server_default=""))
    op.add_column("runtime_worker", sa.Column("process_id", sa.Integer(), nullable=False, server_default="0"))
    op.execute("UPDATE runtime_worker SET started_at = last_heartbeat_at WHERE started_at IS NULL")
    op.alter_column("runtime_worker", "started_at", nullable=False)

    op.create_index(
        "uq_message_task_sender",
        "message",
        ["tenant_id", "task_id", "sender_type"],
        unique=True,
        postgresql_where=sa.text("task_id IS NOT NULL"),
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_index("uq_message_task_sender", table_name="message")
    op.drop_column("runtime_worker", "process_id")
    op.drop_column("runtime_worker", "hostname")
    op.drop_column("runtime_worker", "current_task_id")
    op.drop_column("runtime_worker", "started_at")
    op.drop_constraint("uq_runtime_task_replay_once", "runtime_task", type_="unique")
    op.drop_constraint("fk_runtime_task_replay_of", "runtime_task", type_="foreignkey")
    op.drop_column("runtime_task", "cancel_requested_at")
    op.drop_column("runtime_task", "dead_lettered_at")
    op.drop_column("runtime_task", "replay_reason")
    op.drop_column("runtime_task", "replayed_by")
    op.drop_column("runtime_task", "replay_of_task_id")
    op.drop_column("action_execution", "last_reconciled_at")
    op.drop_column("action_execution", "reconciliation_attempts")
    op.drop_column("action_execution", "updated_at")
