"""v0 initial schema — bronze_posting, posting, cluster, score, profile.

The operational store for v0 (ADR-0014/0018), mirroring the docs/02-architecture.md ERD.
Kept consistent with the SQLAlchemy Core schema in `jobfetcher.db.tables`. `posting.jd_embedding`
(pgvector) is M2 and intentionally absent. Built via the aurora-data-api dialect on deploy,
plain Postgres locally — the same DDL on both.

Revision ID: 0001_v0_initial
Revises:
Create Date: 2026-06-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_v0_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = postgresql.TIMESTAMP(timezone=True)
_JSONB = postgresql.JSONB


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "bronze_posting",
        sa.Column("bronze_id", sa.Text(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("raw_payload", _JSONB(), nullable=False),
        sa.Column("s3_raw_key", sa.Text()),
        sa.Column("run_id", sa.Text()),
        sa.Column("fetched_at", _TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_table(
        "cluster",
        sa.Column("cluster_id", sa.Text(), primary_key=True),
        sa.Column("representative_posting_id", sa.Text()),
        sa.Column("posting_count", sa.Integer()),
        sa.Column("first_seen", _TS),
        sa.Column("last_seen", _TS),
    )
    op.create_table(
        "posting",
        sa.Column("posting_id", sa.Text(), primary_key=True),
        sa.Column("bronze_id", sa.Text(), sa.ForeignKey("bronze_posting.bronze_id")),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Text(), sa.ForeignKey("cluster.cluster_id")),
        sa.Column("title", sa.Text()),
        sa.Column("company", sa.Text()),
        sa.Column("location", sa.Text()),
        sa.Column("city", sa.Text()),
        sa.Column("state", sa.Text()),
        sa.Column("country", sa.Text()),
        sa.Column("apply_url", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("normalized_title", sa.Text()),
        sa.Column("sector", sa.Text()),
        sa.Column("seniority", sa.Text()),
        sa.Column("employment_type", sa.Text()),
        sa.Column("language", sa.Text()),
        sa.Column("skills", _JSONB()),
        sa.Column("dissection_model", sa.Text()),
        sa.Column("dropped_skill_count", sa.Integer()),
        sa.Column("pipeline_version", sa.Text()),
        # M2: jd_embedding (pgvector) — added by a later additive migration.
        sa.Column("fingerprint", sa.Text()),
        sa.Column("match_status", sa.Text()),
        sa.Column("match_confidence", sa.Float()),
        sa.Column("fetched_at", _TS),
        sa.Column("status", sa.Text()),
    )
    op.create_table(
        "score",
        sa.Column("cluster_id", sa.Text(), sa.ForeignKey("cluster.cluster_id")),
        sa.Column("score", sa.Integer()),
        sa.Column("fit_category", sa.Text()),
        sa.Column("strengths", _JSONB()),
        sa.Column("gaps", _JSONB()),
        sa.Column("strategic_assessment", sa.Text()),
        sa.Column("poster_type", sa.Text()),
        sa.Column("legitimacy_verified", sa.Boolean()),
        sa.Column("previous_score", sa.Integer()),
        sa.Column("scored_at", _TS),
        sa.Column("score_override", sa.Integer()),
    )
    op.create_table(
        "profile",
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("profile", _JSONB(), nullable=False),
        sa.Column("threshold", sa.Integer()),
        sa.Column("hard_floor", sa.Integer()),
        sa.Column("near_miss_band", sa.Integer()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("profile")
    op.drop_table("score")
    op.drop_table("posting")
    op.drop_table("cluster")
    op.drop_table("bronze_posting")
