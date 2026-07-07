"""Outcome tracking + score overrides on a REAL local Postgres (migration 0005).

Proves the DB-level properties no fake can: (1) the migration chain reaches 0005 and the
new table's CHECK — the real, migrated one — accepts EVERY member of the shared vocabulary
and rejects a status outside it at the DB layer (belt under the repository's braces);
(2) VG1 — two events (applied → interview) → the EXPORT's
latest-status LATERAL yields 'interview' with the second timestamp while BOTH rows survive
in `application_events`; (3) VG2 — an unknown posting_id → RepositoryError with ZERO rows
written; (4) VG5 — override 75 → `score.score_override`=75 AND a `score_event` lineage row
(`scoring_model='human-override'`, score=75, previous_score=the pre-override score), then a
SECOND override 60 → the column moves to 60 and BOTH override events remain (append-only —
a second override never erases the first); plus the negative: an unknown cluster writes
nothing. SKIPS CLEANLY when no Postgres is reachable (same harness as the siblings)."""
from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from jobfetcher.core.models import APPLICATION_STATUSES, DissectedPosting, Skill
from jobfetcher.core.ports import RepositoryError

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- fixtures
def _alembic_upgrade(url: str) -> None:
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


def _load_export():
    spec = importlib.util.spec_from_file_location(
        "export", Path(__file__).resolve().parents[1] / "scripts" / "export.py"
    )
    export = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export)
    return export


def _dissected(title: str) -> DissectedPosting:
    return DissectedPosting(
        raw_title=title, language="en", location="Riyadh", city="Riyadh", country="sa",
        seniority="mid", normalized_title=title, sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _seed_scored(repo, posting_id: str, *, score: int = 62) -> str:
    """Land bronze→silver→cluster→score→scored, like the pipeline would (one full posting)."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
                       raw_payload={"job_id": posting_id}, run_id="seed")
    repo.save_posting(_dissected("Data Engineer"), posting_id=posting_id, bronze_id=bronze_id,
                      source="jsearch", source_job_id=posting_id, run_id="seed",
                      company="Acme", status="silver")
    repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
    repo.set_posting_cluster(posting_id, posting_id)
    repo.save_score(cluster_id=posting_id, score=score, fit_category="strong_fit",
                    strengths=["python"], gaps=["spark"], strategic_assessment="x",
                    poster_type="direct employer", legitimacy_verified=True,
                    scoring_model="test-model", profile_hash="ph-seed", run_id="seed")
    repo.mark_scored(posting_id)
    return posting_id


def _app_events(repo, posting_id: str) -> list[dict]:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(tables.application_event)
            .where(tables.application_event.c.posting_id == posting_id)
            .order_by(tables.application_event.c.event_id)
        ).mappings().all()]


def _score_events(repo, cluster_id: str) -> list[dict]:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(tables.score_event)
            .where(tables.score_event.c.cluster_id == cluster_id)
            .order_by(tables.score_event.c.event_id)
        ).mappings().all()]


def _score_row(repo, cluster_id: str) -> dict:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return dict(conn.execute(
            select(tables.score).where(tables.score.c.cluster_id == cluster_id)
        ).mappings().one())


# --------------------------------------------------------------------------- migration chain
def test_migration_chain_reaches_0005_and_the_check_bites_at_the_db(repo):
    """The chain 0001→…→0005 lands: `application_event` exists, the REAL migrated CHECK
    accepts EVERY member of the shared vocabulary (one INSERT per status — the frozen
    migration literals cover the whole runtime tuple, not just the two statuses VG1 uses),
    and rejects a status outside it AT THE DB LAYER — the belt under the repository's
    validation braces (defense in depth, additive to this new table only)."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    pid = _seed_scored(repo, f"chk-{uuid4().hex[:8]}")
    with repo.engine.connect() as conn:
        assert conn.execute(
            text("SELECT to_regclass('application_event') IS NOT NULL")
        ).scalar_one()
    # positive: every allowed status passes the migrated CHECK — an added-but-unmigrated
    # status would fail here instead of failing in production
    with repo.engine.begin() as conn:
        for status in APPLICATION_STATUSES:
            conn.execute(text(
                "INSERT INTO application_event (posting_id, status) VALUES (:p, :s)"
            ), {"p": pid, "s": status})
    # negative: a status outside the vocabulary is rejected by the DB itself
    with pytest.raises(IntegrityError, match="ck_application_event_status"):
        with repo.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO application_event (posting_id, status) VALUES (:p, 'ghosted')"
            ), {"p": pid})
    # exactly the allowed set landed, in order; the rejected INSERT left nothing behind
    assert [e["status"] for e in _app_events(repo, pid)] == list(APPLICATION_STATUSES)


# --------------------------------------------------------------------------- VG1
def test_two_events_latest_status_is_the_second_and_both_survive(repo):
    """VG1: applied → interview on one posting → the export's latest-status LATERAL yields
    'interview' with the SECOND event's timestamp, and BOTH rows exist in the
    `application_events` snapshot table data (append-only — the trail survives)."""
    from sqlalchemy import text

    export = _load_export()
    pid = _seed_scored(repo, f"vg1-{uuid4().hex[:8]}")
    repo.track_application_event(posting_id=pid, status="applied", note="via portal")
    repo.track_application_event(posting_id=pid, status="interview")
    # make the timestamp ordering unambiguous: the applied row is strictly older
    with repo.engine.begin() as conn:
        conn.execute(text(
            "UPDATE application_event SET noted_at = noted_at - interval '1 hour' "
            "WHERE posting_id = :p AND status = 'applied'"), {"p": pid})

    rows = _app_events(repo, pid)
    assert [(r["status"], r["note"]) for r in rows] == [
        ("applied", "via portal"), ("interview", None)
    ]
    assert rows[0]["noted_at"] < rows[1]["noted_at"]

    data = export.read_data(repo.engine)  # the REAL export SQL against the real schema
    job = next(j for j in data["jobs"] if j["posting_id"] == pid)
    assert job["latest_application_status"] == "interview"
    assert job["application_noted_at"] == rows[1]["noted_at"].isoformat()  # the 2nd timestamp
    snapshot_events = [e for e in data["application_events"] if e["posting_id"] == pid]
    assert [e["status"] for e in snapshot_events] == ["applied", "interview"]


# --------------------------------------------------------------------------- VG2
def test_unknown_posting_writes_zero_rows(repo):
    """VG2: an unknown posting_id → RepositoryError and ZERO `application_event` rows —
    the existence check and the INSERT share one transaction, so nothing leaks."""
    from sqlalchemy import func, select

    from jobfetcher.db import tables

    ghost = f"ghost-{uuid4().hex[:8]}"
    with repo.engine.connect() as conn:
        before = conn.execute(
            select(func.count()).select_from(tables.application_event)
        ).scalar_one()
    with pytest.raises(RepositoryError, match="no posting"):
        repo.track_application_event(posting_id=ghost, status="applied")
    with repo.engine.connect() as conn:
        after = conn.execute(
            select(func.count()).select_from(tables.application_event)
        ).scalar_one()
    assert after == before  # zero rows written, table-wide


# --------------------------------------------------------------------------- VG5
def test_override_updates_column_and_appends_lineage_twice(repo):
    """VG5: override 75 → `score.score_override`=75 (the LLM `score` untouched) AND a
    `score_event` with scoring_model='human-override', score=75, previous_score=62 (the
    pre-override score). A SECOND override 60 → the column moves to 60 and BOTH override
    events remain — the log is append-only, a correction never erases a correction."""
    pid = _seed_scored(repo, f"vg5-{uuid4().hex[:8]}", score=62)

    repo.set_score_override(cluster_id=pid, score_override=75, fit_category="strong_fit",
                            profile_hash="ph-human", previous_score=62)
    row = _score_row(repo, pid)
    assert row["score_override"] == 75
    assert row["score"] == 62  # the LLM judgment column is NOT overwritten

    events = _score_events(repo, pid)
    assert len(events) == 2  # the seed LLM scoring + the human override
    ev = events[-1]
    assert ev["scoring_model"] == "human-override"
    assert ev["score"] == 75 and ev["previous_score"] == 62
    assert ev["fit_category"] == "strong_fit" and ev["profile_hash"] == "ph-human"
    assert ev["strengths"] == [] and ev["gaps"] == []  # honestly empty — no LLM narrative
    assert ev["strategic_assessment"] is None and ev["run_id"] is None

    repo.set_score_override(cluster_id=pid, score_override=60, fit_category="strong_fit",
                            profile_hash="ph-human", previous_score=62)
    assert _score_row(repo, pid)["score_override"] == 60  # the column follows the latest
    overrides = [e for e in _score_events(repo, pid) if e["scoring_model"] == "human-override"]
    assert [e["score"] for e in overrides] == [75, 60]  # BOTH remain — nothing erased


def test_override_unknown_cluster_writes_zero_rows(repo):
    """Negative: an unknown cluster → RepositoryError (rowcount check) and NO lineage event
    — the UPDATE and the APPEND share one transaction, so a miss writes nothing."""
    ghost = f"ghost-{uuid4().hex[:8]}"
    with pytest.raises(RepositoryError, match="no score row"):
        repo.set_score_override(cluster_id=ghost, score_override=75,
                                fit_category="strong_fit", profile_hash="ph",
                                previous_score=None)
    assert _score_events(repo, ghost) == []
