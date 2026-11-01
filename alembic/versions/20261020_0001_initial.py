"""Initial market assistant schema.

Revision ID: 20261020_0001
"""
from alembic import op
from app.persistence.models import Base

revision = "20261020_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    # The original migration used live ORM metadata. Keep fresh installs safe by
    # marking that the current head schema has already been materialized; legacy
    # databases that applied 0001 in the past do not have this marker and still
    # execute every incremental migration normally.
    op.execute(
        "CREATE TABLE IF NOT EXISTS market_schema_bootstrap "
        "(id integer PRIMARY KEY, schema_version varchar(32) NOT NULL)"
    )
    op.execute(
        "INSERT INTO market_schema_bootstrap (id, schema_version) VALUES (1, '0019') "
        "ON CONFLICT (id) DO UPDATE SET schema_version = EXCLUDED.schema_version"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_hash_cosine "
        "ON policy_chunk USING hnsw (embedding vector_cosine_ops) "
        "WHERE embedding IS NOT NULL AND embedding_dimension = 1536"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_policy_chunk_embedding_bge_v10_cosine "
        "ON policy_chunk_embedding USING hnsw "
        "((embedding::vector(1024)) vector_cosine_ops) "
        "WHERE retrieval_profile_id = 'bge-m3-v10' AND status = 'ready'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_schema_bootstrap")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
