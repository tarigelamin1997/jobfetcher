"""subscores — the per-factor score breakdown (JSONB) on `score` + `score_event`.

Additive over 0005: the Scorer now asks the LLM for the 7 per-factor subscores of the ATS
framework (ADR-0028-to-be) and computes a code-side weighted total (`FACTOR_WEIGHTS`,
`core/scorer.py`) in SHADOW mode — the LLM holistic `score` stays the product number. This
migration adds one nullable JSONB column to BOTH tables so `save_score` can persist the
blob (`{7 factors, code_total, llm_total}`) per write: on `score` (the current judgment)
and on `score_event` (self-contained per event) — the raw material for M7 calibration.

**Strictly additive, no backfill:** pre-0006 rows have no subscore data anywhere (the
prompt never asked for them), so their column stays NULL — as does any post-0006 write
where the LLM omitted a subscore (never a partial dict). Mirrors the SQLAlchemy Core
schema in `jobfetcher.db.tables`.

Revision ID: 0006_subscores
Revises: 0005_application_event
Create Date: 2026-07-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_subscores"
down_revision: Union[str, Sequence[str], None] = "0005_application_event"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("score", sa.Column("subscores", postgresql.JSONB()))
    op.add_column("score_event", sa.Column("subscores", postgresql.JSONB()))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("score_event", "subscores")
    op.drop_column("score", "subscores")
