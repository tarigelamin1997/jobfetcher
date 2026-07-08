"""Migration 0006 (subscores) on a REAL local Postgres: step the schema to 0005, seed a
pre-0006 `score` row + `score_event` row (the state of the live scores/events today), then
`upgrade head` and assert the additive contract — the `subscores` JSONB column lands on BOTH
tables, every pre-0006 row stays NULL (**no backfill** — the prompt never asked for subscores
before 0006, so there is nothing honest to backfill), and the pre-existing column values are
untouched. Also the negative: at 0005 the column exists on NEITHER table (the chain is
really additive, not a re-created table).

The downgrade→upgrade dance is safe here: the integration DB is a throwaway test DB, and
every sibling module's fixture runs `upgrade head` (a no-op once this test restores it).
SKIPS CLEANLY when no Postgres is reachable."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


def _alembic_cfg(url: str):
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["JOBFETCHER_DB_URL"] = url  # env.py reads this
    return cfg


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    explicit = os.environ.get("JOBFETCHER_DB_URL")
    if explicit and explicit.strip():
        yield explicit.strip()
        return
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed and $JOBFETCHER_DB_URL unset")
    try:
        with PostgresContainer("postgres:16-alpine") as pg:
            yield pg.get_connection_url()
    except Exception as e:
        pytest.skip(f"no local Postgres available (Docker?): {type(e).__name__}: {e}")


def _has_subscores_column(conn, table: str) -> bool:
    from sqlalchemy import text

    return bool(conn.execute(text(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = 'subscores'"), {"t": table}
    ).scalar_one())


def test_upgrade_0006_adds_nullable_subscores_to_both_tables_no_backfill(db_url):
    from alembic import command
    from sqlalchemy import text

    from jobfetcher.db.engine import make_engine

    cfg = _alembic_cfg(db_url)
    # Land exactly at 0005 regardless of the DB's starting point (both no-op when already
    # there) — the pre-0006 schema the live stack runs today.
    command.upgrade(cfg, "0005_application_event")
    command.downgrade(cfg, "0005_application_event")

    engine = make_engine(db_url)
    tag = uuid4().hex[:8]
    cid = f"pre0006-{tag}"
    with engine.begin() as conn:
        # the negative: pre-0006, the column exists on NEITHER table
        assert not _has_subscores_column(conn, "score")
        assert not _has_subscores_column(conn, "score_event")
        # seed a pre-0006 score row + its lineage event (the live data shape today)
        conn.execute(text(
            "INSERT INTO cluster (cluster_id, representative_posting_id, posting_count) "
            "VALUES (:c, :c, 1)"), {"c": cid})
        conn.execute(text(
            "INSERT INTO score (cluster_id, score, fit_category, strategic_assessment, "
            "scored_at) VALUES (:c, 72, 'strong_fit', 'x', now())"), {"c": cid})
        conn.execute(text(
            "INSERT INTO score_event (cluster_id, score, fit_category, scoring_model, "
            "profile_hash, scored_at) VALUES (:c, 72, 'strong_fit', 'test-model', 'ph', "
            "now())"), {"c": cid})

    command.upgrade(cfg, "head")  # 0006: ADD COLUMN subscores JSONB on score + score_event

    with engine.connect() as conn:
        assert _has_subscores_column(conn, "score")
        assert _has_subscores_column(conn, "score_event")
        # strictly additive, NO backfill: the pre-0006 rows stay NULL, values untouched
        row = conn.execute(text(
            "SELECT score, fit_category, subscores FROM score WHERE cluster_id = :c"),
            {"c": cid}).mappings().one()
        assert row["subscores"] is None
        assert row["score"] == 72 and row["fit_category"] == "strong_fit"
        ev = conn.execute(text(
            "SELECT score, scoring_model, subscores FROM score_event "
            "WHERE cluster_id = :c"), {"c": cid}).mappings().one()
        assert ev["subscores"] is None
        assert ev["score"] == 72 and ev["scoring_model"] == "test-model"
