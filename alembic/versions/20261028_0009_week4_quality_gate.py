"""Week 4 quality gate configuration and immutable validation bindings.

Revision ID: 20261028_0009
Revises: 20261027_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261028_0009"
down_revision: str | None = "20261027_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column(
        "retrieval_profile",
        sa.Column("gate_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "retrieval_profile",
        sa.Column("quality_report", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column("retrieval_profile", sa.Column("rejected_reason", sa.Text(), nullable=True))
    op.add_column("retrieval_profile", sa.Column("parent_profile_id", sa.String(80), nullable=True))
    op.add_column("retrieval_profile", sa.Column("calibration_run_id", sa.String(80), nullable=True))
    op.add_column("retrieval_profile", sa.Column("holdout_run_id", sa.String(80), nullable=True))
    op.create_index(
        "ix_retrieval_profile_parent", "retrieval_profile", ["tenant_id", "parent_profile_id"]
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_hash_cosine "
        "ON policy_chunk USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL AND embedding_dimension = 1536"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_embedding_bge_v2_cosine "
        "ON policy_chunk_embedding USING hnsw "
        "((embedding::vector(1024)) vector_cosine_ops) "
        "WHERE retrieval_profile_id = 'bge-m3-v2' AND status = 'ready'"
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.execute("DROP INDEX IF EXISTS ix_policy_chunk_embedding_bge_v2_cosine")
    op.execute("DROP INDEX IF EXISTS ix_policy_chunk_hash_cosine")
    op.drop_index("ix_retrieval_profile_parent", table_name="retrieval_profile")
    for name in (
        "holdout_run_id",
        "calibration_run_id",
        "parent_profile_id",
        "rejected_reason",
        "quality_report",
        "gate_config",
    ):
        op.drop_column("retrieval_profile", name)
