"""Gate v4 profile identity and BGE-M3 v3 index.

Revision ID: 20261029_0010
Revises: 20261028_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261029_0010"
down_revision: str | None = "20261028_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column("evaluation_run", sa.Column("query_analyzer_version", sa.String(40), nullable=False, server_default=""))
    op.add_column("evaluation_run", sa.Column("scope_match_version", sa.String(40), nullable=False, server_default=""))
    op.add_column("evaluation_run", sa.Column("threshold_grid_version", sa.String(80), nullable=False, server_default=""))
    op.add_column(
        "retrieval_profile",
        sa.Column("calibration_dataset", sa.String(200), nullable=False, server_default=""),
    )
    op.add_column(
        "retrieval_profile",
        sa.Column(
            "validation_datasets",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "retrieval_profile",
        sa.Column("config_hash", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_retrieval_profile_config_hash", "retrieval_profile", ["config_hash"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_embedding_bge_v3_cosine "
        "ON policy_chunk_embedding USING hnsw "
        "((embedding::vector(1024)) vector_cosine_ops) "
        "WHERE retrieval_profile_id = 'bge-m3-v3' AND status = 'ready'"
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.execute("DROP INDEX IF EXISTS ix_policy_chunk_embedding_bge_v3_cosine")
    op.drop_index("ix_retrieval_profile_config_hash", table_name="retrieval_profile")
    op.drop_column("retrieval_profile", "config_hash")
    op.drop_column("retrieval_profile", "validation_datasets")
    op.drop_column("retrieval_profile", "calibration_dataset")
    op.drop_column("evaluation_run", "threshold_grid_version")
    op.drop_column("evaluation_run", "scope_match_version")
    op.drop_column("evaluation_run", "query_analyzer_version")
