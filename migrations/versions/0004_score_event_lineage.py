"""score_event — the append-only scoring-lineage log (+ profile.profile_hash).

Additive over 0003: the `score` table is a 1:1 upsert per cluster, so every re-score
(reassess/replay, ADR-0023) OVERWRITES the prior judgment — only the latest score +
`previous_score` survive. This table records one immutable row per scoring event
(score/fit + the lineage that produced it: `scoring_model`, `profile_hash`, `run_id`),
so the full score history is queryable and a reassess never erases evidence. `save_score`
dual-writes it in the same transaction as the `score` upsert; the existing `score` read
paths are untouched. `profile.profile_hash` (nullable, additive) records the hash of the
profile+knobs the row was last synced from. Mirrors the SQLAlchemy Core schema in
`jobfetcher.db.tables` (`score_event`).

**Baseline backfill:** one synthetic event per existing `score` row (from its current
values, `scoring_model`/`profile_hash` = 'pre-0004', `run_id` NULL) rescues the scores
that predate the log — otherwise the next reassess would overwrite them with no trace.
Rows missing the event's NOT NULL fields (NULL cluster_id/score/fit_category — possible
under the v0 constraint-free `score` DDL) are skipped, and a NULL `scored_at` falls back
to now().

Revision ID: 0004_score_event_lineage
Revises: 0003_run_log_send_guard
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_score_event_lineage"
down_revision: Union[str, Sequence[str], None] = "0003_run_log_send_guard"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = postgresql.TIMESTAMP(timezone=True)
_JSONB = postgresql.JSONB


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "score_event",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "cluster_id", sa.Text(), sa.ForeignKey("cluster.cluster_id"), nullable=False
        ),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("fit_category", sa.Text(), nullable=False),
        sa.Column("strengths", _JSONB()),
        sa.Column("gaps", _JSONB()),
        sa.Column("strategic_assessment", sa.Text()),
        sa.Column("poster_type", sa.Text()),
        sa.Column("legitimacy_verified", sa.Boolean()),
        sa.Column("previous_score", sa.Integer()),  # what save_score received (self-contained)
        sa.Column("scoring_model", sa.Text(), nullable=False),
        sa.Column("profile_hash", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text()),
        sa.Column("scored_at", _TS, nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_score_event_cluster_id", "score_event", ["cluster_id"])
    op.create_index("ix_score_event_run_id", "score_event", ["run_id"])

    op.add_column("profile", sa.Column("profile_hash", sa.Text()))

    # Baseline backfill: rescue the current `score` rows into the log before the next
    # reassess overwrites them. Pure SQL — no app code, runs identically on local
    # Postgres and the Aurora Data API.
    op.execute(
        """
        INSERT INTO score_event (
            cluster_id, score, fit_category, strengths, gaps, strategic_assessment,
            poster_type, legitimacy_verified, previous_score, scoring_model,
            profile_hash, run_id, scored_at
        )
        SELECT
            cluster_id, score, fit_category, strengths, gaps, strategic_assessment,
            poster_type, legitimacy_verified, previous_score, 'pre-0004',
            'pre-0004', NULL, COALESCE(scored_at, now())
        FROM score
        WHERE cluster_id IS NOT NULL AND score IS NOT NULL AND fit_category IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_score_event_run_id", table_name="score_event")
    op.drop_index("ix_score_event_cluster_id", table_name="score_event")
    op.drop_table("score_event")
    op.drop_column("profile", "profile_hash")
