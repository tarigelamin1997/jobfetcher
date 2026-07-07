"""application_event — the append-only application-outcome log.

Additive over 0004: the pipeline scores jobs but records nothing about what happens AFTER —
did Tarig apply, get an interview, an offer, a rejection? This table records one immutable
row per human status note (`applied` / `interview` / `offer` / `rejected` / `withdrawn`)
against a posting, written by `scripts/track.py`. Mirrors `score_event`'s append-only
discipline: "latest status" is a read-side query (newest row per posting), never an
overwrite, so the full applied→interview→… trail survives as calibration/outcome data.

The `status` CHECK lives on this NEW table only (additive — no constraint retrofitted onto
an existing table); the literal list is frozen here as migrations must be, and mirrors
`APPLICATION_STATUSES` in `jobfetcher.core.models` (the runtime source of truth the
SQLAlchemy Core schema in `jobfetcher.db.tables` builds its CHECK from).

**No backfill:** there is no prior outcome data anywhere — the table starts empty.

Revision ID: 0005_application_event
Revises: 0004_score_event_lineage
Create Date: 2026-07-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_application_event"
down_revision: Union[str, Sequence[str], None] = "0004_score_event_lineage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = postgresql.TIMESTAMP(timezone=True)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "application_event",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "posting_id", sa.Text(), sa.ForeignKey("posting.posting_id"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("noted_at", _TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("note", sa.Text()),
        sa.CheckConstraint(
            "status IN ('applied', 'interview', 'offer', 'rejected', 'withdrawn')",
            name="ck_application_event_status",
        ),
    )
    op.create_index("ix_application_event_posting_id", "application_event", ["posting_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_application_event_posting_id", table_name="application_event")
    op.drop_table("application_event")
