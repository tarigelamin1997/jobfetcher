"""Unique constraint on score.cluster_id — score is 1:1 with cluster.

Additive over 0001: `score` had no key in v0, so the upsert was a hand-rolled
read→delete→insert. This unique key on the natural key (`cluster_id`) lets the
adapter collapse `save_score` to a single `on_conflict_do_update` (idempotent
re-score in one statement). Mirrors the SQLAlchemy Core schema in
`jobfetcher.db.tables` (`uq_score_cluster_id`).

Revision ID: 0002_score_cluster_unique
Revises: 0001_v0_initial
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_score_cluster_unique"
down_revision: Union[str, Sequence[str], None] = "0001_v0_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint("uq_score_cluster_id", "score", ["cluster_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_score_cluster_id", "score", type_="unique")
