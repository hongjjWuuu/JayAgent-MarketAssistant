"""Multi-model retrieval profiles and non-destructive embeddings.

Revision ID: 20261027_0007
Revises: 20261027_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261027_0007"
down_revision: str | None = "20261027_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.create_table(
        "retrieval_profile",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("embedding_model", sa.String(160), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("retriever_version", sa.String(80), nullable=False, server_default="hybrid-rrf-v3"),
        sa.Column("gate_version", sa.String(80), nullable=False, server_default="v2"),
        sa.Column("normalization_version", sa.String(80), nullable=False, server_default="query-normalizer-v2"),
        sa.Column("status", sa.String(20), nullable=False, server_default="building"),
        sa.Column("indexed_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_chunks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evaluation_run_id", sa.String(80), nullable=True),
        sa.Column("activated_by", sa.String(160), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("status IN ('building','ready','active','rejected','archived')", name="ck_retrieval_profile_status"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_retrieval_profile_tenant_id"),
    )
    op.create_index("ix_retrieval_profile_tenant_id", "retrieval_profile", ["tenant_id"])
    op.create_index("ix_retrieval_profile_status", "retrieval_profile", ["status"])
    op.create_index(
        "uq_retrieval_profile_active_tenant",
        "retrieval_profile",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "policy_chunk_embedding",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("chunk_id", sa.String(80), sa.ForeignKey("policy_chunk.id", ondelete="CASCADE"), nullable=False),
        sa.Column("retrieval_profile_id", sa.String(80), sa.ForeignKey("retrieval_profile.id", ondelete="CASCADE"), nullable=False),
        sa.Column("embedding_model", sa.String(160), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ready"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "chunk_id", "retrieval_profile_id", name="uq_chunk_embedding_profile"),
    )
    op.create_index("ix_policy_chunk_embedding_tenant_id", "policy_chunk_embedding", ["tenant_id"])
    op.create_index("ix_policy_chunk_embedding_chunk_id", "policy_chunk_embedding", ["chunk_id"])
    op.create_index("ix_policy_chunk_embedding_profile", "policy_chunk_embedding", ["retrieval_profile_id"])
    op.execute(
        """
        INSERT INTO retrieval_profile
            (id, tenant_id, provider, embedding_model, embedding_dimension, status,
             indexed_chunks, total_chunks, created_at, updated_at)
        SELECT 'hash-v1:' || tenant_id, tenant_id, 'hash', 'hash-embedding-v1', 1536,
               'active', count(*), count(*), now(), now()
        FROM policy_chunk GROUP BY tenant_id
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO policy_chunk_embedding
            (id, tenant_id, chunk_id, retrieval_profile_id, embedding_model,
             embedding_dimension, content_hash, embedding, status, created_at, updated_at)
        SELECT 'pce_' || md5(pc.tenant_id || chr(58) || pc.id || chr(58) || 'hash-v1'),
               pc.tenant_id, pc.id,
               'hash-v1:' || pc.tenant_id, pc.embedding_model, pc.embedding_dimension,
               pc.content_hash, pc.embedding, 'ready', now(), now()
        FROM policy_chunk pc
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_table("policy_chunk_embedding")
    op.drop_index("uq_retrieval_profile_active_tenant", table_name="retrieval_profile")
    op.drop_table("retrieval_profile")
