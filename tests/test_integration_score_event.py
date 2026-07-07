"""score_event lineage + reassess age bound on a REAL local Postgres (migration 0004).

Proves the dual-write's DB-level properties that no fake can: (1) three scorings of one
cluster → THREE distinct `score_event` rows while `score` holds only current+previous;
(2) atomicity BOTH ways — a failure of the event insert rolls back the score upsert, and a
failure of the score upsert writes no event (one transaction, never divergent); (3) the
`get_scored_for_reassess` age filter — old postings excluded, recent included, NULL
`fetched_at` ALWAYS included (the safe default: `save_posting` never populates it), and
`max_age_days=0`/`None` = unbounded (the pre-0004 regression guard). SKIPS CLEANLY when no
Postgres is reachable (same harness as the sibling integration modules)."""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from jobfetcher.core.models import DissectedPosting, Skill
from jobfetcher.core.ports import RepositoryError

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- fixtures
def _alembic_upgrade(url: str) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["JOBFETCHER_DB_URL"] = url
    command.upgrade(cfg, "head")


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


@pytest.fixture(scope="module")
def repo(db_url: str):
    from jobfetcher.adapters.repository_postgres import PostgresRepository
    from jobfetcher.db.engine import make_engine

    _alembic_upgrade(db_url)
    return PostgresRepository.from_engine(make_engine(db_url))


def _dissected(title: str) -> DissectedPosting:
    return DissectedPosting(
        raw_title=title, language="en", location="Riyadh", city="Riyadh", country="sa",
        seniority="mid", normalized_title=title, sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _seed_scored(repo, posting_id: str, *, score: int = 55) -> str:
    """Land bronze→silver→cluster→score→scored, like the pipeline would (one full posting)."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
                       raw_payload={"job_id": posting_id}, run_id="seed")
    repo.save_posting(_dissected("Data Engineer"), posting_id=posting_id, bronze_id=bronze_id,
                      source="jsearch", source_job_id=posting_id, run_id="seed", status="silver")
    repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
    repo.set_posting_cluster(posting_id, posting_id)
    repo.save_score(cluster_id=posting_id, score=score, fit_category="near_miss",
                    strengths=["python"], gaps=["spark"], strategic_assessment="x",
                    poster_type="direct employer", legitimacy_verified=True,
                    scoring_model="test-model", profile_hash="ph-seed", run_id="seed")
    repo.mark_scored(posting_id)
    return posting_id


def _set_fetched_at(repo, posting_id: str, when: datetime | None) -> None:
    """Set `posting.fetched_at` directly — `save_posting` never writes it (see the NULL test)."""
    from sqlalchemy import update

    from jobfetcher.db import tables

    with repo.engine.begin() as conn:
        conn.execute(
            update(tables.posting)
            .where(tables.posting.c.posting_id == posting_id)
            .values(fetched_at=when)
        )


def _events(repo, cluster_id: str) -> list[dict]:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(tables.score_event)
            .where(tables.score_event.c.cluster_id == cluster_id)
            .order_by(tables.score_event.c.event_id)
        ).mappings().all()]


def _score_rows(repo, cluster_id: str) -> list[dict]:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(tables.score).where(tables.score.c.cluster_id == cluster_id)
        ).mappings().all()]


def _save(repo, cluster_id: str, score: int, *, profile_hash: str, run_id: str,
          scoring_model: str = "test-model", previous_score: int | None = None) -> None:
    repo.save_score(cluster_id=cluster_id, score=score, fit_category="near_miss",
                    strengths=["python"], gaps=["spark"], strategic_assessment="x",
                    poster_type="direct employer", legitimacy_verified=True,
                    scoring_model=scoring_model, profile_hash=profile_hash,
                    run_id=run_id, previous_score=previous_score)


# --------------------------------------------------------------------------- the append-only log
def test_three_scorings_three_events_score_holds_current_plus_previous(repo):
    """Three scorings of ONE cluster → THREE distinct event rows (nothing overwritten), while
    the `score` table still holds exactly one row with only current + previous — the exact
    information the log exists to stop losing."""
    pid = _seed_scored(repo, f"three-{uuid4().hex[:8]}", score=50)  # scoring #1
    _save(repo, pid, 70, profile_hash="ph-2", run_id="r2", previous_score=50)  # #2 (reassess)
    _save(repo, pid, 90, profile_hash="ph-3", run_id="r3", previous_score=70)  # #3 (reassess)

    events = _events(repo, pid)
    assert len(events) == 3
    assert [e["score"] for e in events] == [50, 70, 90]
    assert [e["previous_score"] for e in events] == [None, 50, 70]  # each self-contained
    assert [e["profile_hash"] for e in events] == ["ph-seed", "ph-2", "ph-3"]  # distinct lineage
    assert len({e["event_id"] for e in events}) == 3  # three distinct rows, not an upsert

    rows = _score_rows(repo, pid)
    assert len(rows) == 1  # the current-judgment table is still 1:1
    assert rows[0]["score"] == 90 and rows[0]["previous_score"] == 70  # only current + previous


# --------------------------------------------------------------------------- atomicity
def test_failed_event_insert_rolls_back_the_score_upsert(repo):
    """Dual-write atomicity, direction 1: the event insert fails (scoring_model=None violates
    its NOT NULL) → RepositoryError AND the `score` row is UNTOUCHED — the upsert that ran
    first in the same transaction rolled back with it."""
    pid = _seed_scored(repo, f"atomic1-{uuid4().hex[:8]}", score=50)
    before_rows = _score_rows(repo, pid)
    before_events = _events(repo, pid)

    with pytest.raises(RepositoryError, match="save_score failed"):
        _save(repo, pid, 99, profile_hash="ph-x", run_id="rx",
              scoring_model=None)  # type: ignore[arg-type] — the forced event-side failure

    assert _score_rows(repo, pid) == before_rows  # the score row did NOT move to 99
    assert _events(repo, pid) == before_events  # and no event leaked


def test_failed_score_upsert_writes_no_event(repo):
    """Dual-write atomicity, direction 2: the `score` upsert fails first (an unknown
    cluster_id violates its FK) → RepositoryError AND no `score_event` row exists for it."""
    ghost = f"ghost-{uuid4().hex[:8]}"  # no cluster row — the score insert violates the FK
    with pytest.raises(RepositoryError, match="save_score failed"):
        _save(repo, ghost, 80, profile_hash="ph-x", run_id="rx")

    assert _score_rows(repo, ghost) == []
    assert _events(repo, ghost) == []


# --------------------------------------------------------------------------- reassess age bound
def test_age_filter_excludes_old_includes_recent_and_null(repo):
    """The reassess age bound over real SQL: with `max_age_days=45`, a 100-day-old posting is
    EXCLUDED, a 10-day-old posting is INCLUDED, and a NULL-`fetched_at` posting is INCLUDED
    (the landmine: `save_posting` never writes `fetched_at`, so NULL rows must never be
    silently dropped from reassess)."""
    tag = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    old = _seed_scored(repo, f"old-{tag}")
    recent = _seed_scored(repo, f"recent-{tag}")
    unknown = _seed_scored(repo, f"unknown-{tag}")
    _set_fetched_at(repo, old, now - timedelta(days=100))
    _set_fetched_at(repo, recent, now - timedelta(days=10))

    # First, PIN the landmine finding: a freshly save_posting'd row has NULL fetched_at.
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        fetched_at = conn.execute(
            select(tables.posting.c.fetched_at)
            .where(tables.posting.c.posting_id == unknown)
        ).scalar_one()
    assert fetched_at is None  # save_posting does not populate it — the OR IS NULL is required

    ids = {t[0] for t in repo.get_scored_for_reassess(max_age_days=45)}
    assert old not in ids       # negative: beyond the bound → excluded
    assert recent in ids        # within the bound → included
    assert unknown in ids       # NULL fetched_at → included (never silently dropped)


def test_age_filter_zero_and_none_are_unbounded(repo):
    """Regression: `max_age_days=0` and `None` return EVERY scored posting — identical sets,
    old ones included (the pre-0004 behavior is the documented default)."""
    tag = uuid4().hex[:8]
    old = _seed_scored(repo, f"unbounded-{tag}")
    _set_fetched_at(repo, old, datetime.now(timezone.utc) - timedelta(days=300))

    ids_none = {t[0] for t in repo.get_scored_for_reassess()}
    ids_zero = {t[0] for t in repo.get_scored_for_reassess(max_age_days=0)}
    assert old in ids_none and old in ids_zero
    assert ids_none == ids_zero
