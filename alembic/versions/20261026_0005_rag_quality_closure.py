"""Evaluation dataset freezing and RAG quality closure.

Revision ID: 20261026_0005
Revises: 20261025_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.persistence.migration_bootstrap import is_current_schema_bootstrap

revision: str = "20261026_0005"
down_revision: str | None = "20261025_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.create_table(
        "evaluation_dataset",
        sa.Column("tenant_id", sa.String(80), nullable=False),
        sa.Column("dataset", sa.String(120), nullable=False),
        sa.Column("version", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft', 'frozen')", name="ck_evaluation_dataset_status"),
        sa.PrimaryKeyConstraint("tenant_id", "dataset", "version"),
    )
    op.execute(
        """
        INSERT INTO evaluation_dataset
            (tenant_id, dataset, version, status, case_count, content_hash, created_at, frozen_at)
        SELECT tenant_id, dataset, dataset_version, 'frozen', count(*), '', now(), now()
        FROM evaluation_case
        WHERE dataset = 'week4_gold' AND dataset_version = 'v1'
        GROUP BY tenant_id, dataset, dataset_version
        ON CONFLICT DO NOTHING
        """
    )
    op.add_column(
        "retrieval_snapshot",
        sa.Column("embedding_dimension", sa.Integer(), nullable=False, server_default="1536"),
    )
    op.add_column(
        "retrieval_snapshot",
        sa.Column(
            "normalization_version",
            sa.String(80),
            nullable=False,
            server_default="query-normalizer-v1",
        ),
    )
    op.add_column(
        "retrieval_snapshot",
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "evaluation_run",
        sa.Column("quality_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column(
        "evaluation_run",
        sa.Column("quality_failures", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.add_column(
        "evaluation_run",
        sa.Column("metric_schema_version", sa.String(20), nullable=False, server_default="v2"),
    )


def downgrade() -> None:
    if is_current_schema_bootstrap(op.get_bind()):
        return
    op.drop_column("evaluation_run", "metric_schema_version")
    op.drop_column("evaluation_run", "quality_failures")
    op.drop_column("evaluation_run", "quality_status")
    op.drop_column("retrieval_snapshot", "diagnostics")
    op.drop_column("retrieval_snapshot", "normalization_version")
    op.drop_column("retrieval_snapshot", "embedding_dimension")
    op.drop_table("evaluation_dataset")
