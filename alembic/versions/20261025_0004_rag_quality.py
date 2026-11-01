"""Policy lifecycle, retrieval snapshots and evaluation quality.

Revision ID: 20261025_0004
Revises: 20261024_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261025_0004"
down_revision: str | None = "20261024_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.add_column("policy_version", sa.Column("index_status", sa.String(40), nullable=False, server_default="pending"))
    op.add_column("policy_version", sa.Column("index_error", sa.Text(), nullable=True))
    op.add_column("policy_version", sa.Column("content_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("policy_version", sa.Column("acl", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("policy_version", sa.Column("published_by", sa.String(160), nullable=True))
    op.add_column("policy_version", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_index("ix_policy_version_index_status", "policy_version", ["index_status"])
    op.create_unique_constraint("uq_policy_scope_version", "policy_version", ["tenant_id", "scope", "version"])

    op.add_column("policy_chunk", sa.Column("ordinal", sa.Integer(), nullable=True))
    op.add_column("policy_chunk", sa.Column("title_path", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.add_column("policy_chunk", sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("policy_chunk", sa.Column("content_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("policy_chunk", sa.Column("embedding_model", sa.String(160), nullable=False, server_default="hash-embedding-v1"))
    op.add_column("policy_chunk", sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="1536"))
    op.execute("""
        WITH ranked AS (
            SELECT id, row_number() OVER (PARTITION BY policy_document_id ORDER BY id) - 1 AS value
            FROM policy_chunk
        )
        UPDATE policy_chunk pc SET ordinal = ranked.value FROM ranked WHERE pc.id = ranked.id
    """)
    op.alter_column("policy_chunk", "ordinal", nullable=False)
    op.create_unique_constraint("uq_policy_chunk_ordinal", "policy_chunk", ["tenant_id", "policy_document_id", "ordinal"])
    op.create_index("ix_policy_chunk_content_hash", "policy_chunk", ["content_hash"])
    op.create_index("ix_policy_chunk_tenant_document", "policy_chunk", ["tenant_id", "policy_document_id"])

    op.create_table(
        "retrieval_snapshot",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("conversation_id", sa.String(80), nullable=True),
        sa.Column("message_id", sa.String(80), nullable=True),
        sa.Column("query_redacted", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("retriever_version", sa.String(80), nullable=False),
        sa.Column("embedding_model", sa.String(160), nullable=False),
        sa.Column("gate_version", sa.String(80), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_retrieval_snapshot_tenant_id", "retrieval_snapshot", ["tenant_id"])
    op.create_index("ix_retrieval_snapshot_conversation_id", "retrieval_snapshot", ["conversation_id"])
    op.create_index("ix_retrieval_snapshot_query_hash", "retrieval_snapshot", ["query_hash"])
    op.create_index("ix_retrieval_snapshot_decision", "retrieval_snapshot", ["decision"])

    for name, column in (("dataset", sa.String(120)), ("dataset_version", sa.String(60)), ("case_type", sa.String(60))):
        op.add_column("evaluation_case", sa.Column(name, column, nullable=False, server_default="week4_gold" if name == "dataset" else "v1" if name == "dataset_version" else "answerable"))
    op.add_column("evaluation_case", sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.add_column("evaluation_case", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_unique_constraint("uq_evaluation_case_name", "evaluation_case", ["tenant_id", "dataset", "dataset_version", "name"])
    op.add_column("evaluation_run", sa.Column("dataset", sa.String(120), nullable=False, server_default="week4_gold"))
    op.add_column("evaluation_run", sa.Column("dataset_version", sa.String(60), nullable=False, server_default="v1"))
    op.add_column("evaluation_run", sa.Column("config_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_column("evaluation_run", "config_snapshot")
    op.drop_column("evaluation_run", "dataset_version")
    op.drop_column("evaluation_run", "dataset")
    op.drop_constraint("uq_evaluation_case_name", "evaluation_case", type_="unique")
    for column in ("active", "tags", "case_type", "dataset_version", "dataset"):
        op.drop_column("evaluation_case", column)
    op.drop_table("retrieval_snapshot")
    op.drop_index("ix_policy_chunk_tenant_document", table_name="policy_chunk")
    op.drop_index("ix_policy_chunk_content_hash", table_name="policy_chunk")
    op.drop_constraint("uq_policy_chunk_ordinal", "policy_chunk", type_="unique")
    for column in ("embedding_dimension", "embedding_model", "content_hash", "metadata", "title_path", "ordinal"):
        op.drop_column("policy_chunk", column)
    op.drop_constraint("uq_policy_scope_version", "policy_version", type_="unique")
    op.drop_index("ix_policy_version_index_status", table_name="policy_version")
    for column in ("updated_at", "published_by", "acl", "content_hash", "index_error", "index_status"):
        op.drop_column("policy_version", column)
