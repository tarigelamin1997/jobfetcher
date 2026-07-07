"""Migration 0004's baseline backfill on a REAL local Postgres: step the schema to 0003,
seed a pre-0004 `score` row (the state of the ~180 live scores), then `upgrade head` and
assert the backfill rescued it — one synthetic `score_event` per score row, carrying the
row's values with `scoring_model`/`profile_hash` = 'pre-0004' and `run_id` NULL. Also the
negative: a `score` row missing the event's NOT NULL fields is SKIPPED, not a crash.

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


def test_upgrade_0004_backfills_existing_scores_into_the_event_log(db_url):
    from alembic import command
    from sqlalchemy import text

    from jobfetcher.db.engine import make_engine

    cfg = _alembic_cfg(db_url)
    # Land exactly at 0003 regardless of the DB's starting point: build up to it on a fresh
    # DB, step back down to it if a prior module already migrated to head (both no-op when
    # already there). This is the pre-0004 schema the live stack ran.
    command.upgrade(cfg, "0003_run_log_send_guard")
    command.downgrade(cfg, "0003_run_log_send_guard")

    engine = make_engine(db_url)
    tag = uuid4().hex[:8]
    good, hollow = f"bf-good-{tag}", f"bf-hollow-{tag}"
    with engine.begin() as conn:
        assert not conn.execute(text(
            "SELECT to_regclass('score_event') IS NOT NULL"
        )).scalar_one()  # pre-0004: the log does not exist yet
        for cid in (good, hollow):
            conn.execute(text(
                "INSERT INTO cluster (cluster_id, representative_posting_id, posting_count) "
                "VALUES (:c, :c, 1)"), {"c": cid})
        # the row the backfill must rescue — a real pre-0004 score with a previous_score
        conn.execute(text(
            "INSERT INTO score (cluster_id, score, fit_category, strengths, gaps, "
            "strategic_assessment, poster_type, legitimacy_verified, previous_score, scored_at) "
            "VALUES (:c, 72, 'strong_fit', '[\"python\"]'::jsonb, '[\"spark\"]'::jsonb, "
            "'x', 'direct employer', true, 55, now())"), {"c": good})
        # the negative: a hollow row (NULL score/fit — legal under the v0 constraint-free DDL)
        # must be SKIPPED by the backfill, not crash the migration
        conn.execute(text(
            "INSERT INTO score (cluster_id, score, fit_category) VALUES (:c, NULL, NULL)"),
            {"c": hollow})

    command.upgrade(cfg, "head")  # 0004: create score_event + backfill

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT cluster_id, score, fit_category, previous_score, scoring_model, "
            "profile_hash, run_id, scored_at FROM score_event "
            "WHERE cluster_id IN (:g, :h) ORDER BY cluster_id"), {"g": good, "h": hollow}
        ).mappings().all()
        profile_hash_col = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'profile' AND column_name = 'profile_hash'"
        )).scalar_one()

    assert len(rows) == 1  # the good row rescued; the hollow row skipped (no crash, no event)
    ev = rows[0]
    assert ev["cluster_id"] == good
    assert ev["score"] == 72 and ev["fit_category"] == "strong_fit"
    assert ev["previous_score"] == 55  # carried as-is — the event is self-contained
    assert ev["scoring_model"] == "pre-0004" and ev["profile_hash"] == "pre-0004"
    assert ev["run_id"] is None
    assert ev["scored_at"] is not None
    assert profile_hash_col == 1  # the additive profile column landed too
