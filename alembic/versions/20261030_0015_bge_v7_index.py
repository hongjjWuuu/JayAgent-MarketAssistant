"""BGE-M3 v7 profile-specific vector index.

Revision ID: 20261030_0015
Revises: 20261030_0014
"""
from collections.abc import Sequence

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261030_0015"
down_revision: str | None = "20261030_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_embedding_bge_v7_cosine "
        "ON policy_chunk_embedding USING hnsw "
        "((embedding::vector(1024)) vector_cosine_ops) "
        "WHERE retrieval_profile_id = 'bge-m3-v7' AND status = 'ready'"
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.execute("DROP INDEX IF EXISTS ix_policy_chunk_embedding_bge_v7_cosine")
