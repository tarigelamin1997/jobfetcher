"""run_log — the once-per-day send guard (Step 7 / VG4).

Additive over 0002: the single Lambda handler is idempotent for a given run date — fetch /
silver / gold / score are already idempotent via their own upserts, but the *email* has no
natural dedup key, so a re-invocation for the same date could double-send. This table records
one row per `(run_date, user_id)` once the digest is sent; the handler checks it before sending
and writes it after, so at most one email goes out per day (VG4). Mirrors the SQLAlchemy Core
schema in `jobfetcher.db.tables` (`run_log`).

Revision ID: 0003_run_log_send_guard
Revises: 0002_score_cluster_unique
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_run_log_send_guard"
down_revision: Union[str, Sequence[str], None] = "0002_score_cluster_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "run_log",
        sa.Column("run_date", sa.Date(), primary_key=True),
        sa.Column("user_id", sa.Text(), primary_key=True),
        sa.Column("run_id", sa.Text()),
        sa.Column("digest_sent_at", _TS, nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("run_log")
