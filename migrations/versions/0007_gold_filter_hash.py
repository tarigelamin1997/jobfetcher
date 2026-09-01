"""gold_filter_hash — the decision context that rejected a posting at the gold filter.

Additive over 0006. Before this migration a posting the gold filter did not promote simply
stayed `status='silver'`, so every run re-read and re-filtered every posting ever rejected —
an O(all-history) read that crossed the RDS Data API's 1 MB result cap at ~1,022 rows and
killed 38 consecutive daily runs (ERR-010).

The naive fix — a terminal `status='rejected'` — would have thrown away a real property the
old behaviour provided by accident: because rejected rows stayed silver, editing
`targeting.job_titles` re-opened the entire rejected backlog on the next run. `reassess`
cannot substitute for that; it replays `status='scored'` rows, so a posting killed at the
gold filter never reaches it.

So a rejection records *why*: the sha256 over the profile, the spec, and the strategy name
that produced it. `get_silver_postings` re-opens a rejection only when the current hash
differs — the same profile_hash mechanism migration 0004 introduced for score lineage,
applied one layer earlier.

**Strictly additive, no backfill.** The column is NULL on every existing row, and
`IS DISTINCT FROM` treats NULL as needs-evaluation, so the pre-0007 backlog is simply
unjudged under the new scheme: the first run after deploy filters it once and stamps each
row. Self-healing, then cheap. `posting.status` has no CHECK constraint (the vocabulary
lives in a comment in `jobfetcher.db.tables`), so adding `rejected` needs no constraint
change here.

Revision ID: 0007_gold_filter_hash
Revises: 0006_subscores
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_gold_filter_hash"
down_revision: Union[str, Sequence[str], None] = "0006_subscores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("posting", sa.Column("gold_filter_hash", sa.Text()))


def downgrade() -> None:
    """Downgrade schema.

    Dropping the column loses every rejection's decision context, so a downgraded stack
    re-opens the whole rejected backlog on its next run. That is correct — it degrades to
    the pre-0007 "judge everything" behaviour rather than silently keeping rows closed under
    a context it can no longer read. Rows left at `status='rejected'` are NOT reverted to
    'silver' here: that is data, not schema, and `get_silver_postings` without a
    `filter_hash` reads only 'silver'. A deliberate downgrade should follow this with
    `UPDATE posting SET status='silver' WHERE status='rejected';`
    """
    op.drop_column("posting", "gold_filter_hash")
