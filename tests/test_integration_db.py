"""DB round-trip on a REAL local Postgres — the WAIT-FOR from build-plan Step 2 (ADR-0018).

LocalStack can't mock the Aurora Data API, so the storage layer is tested on a real Postgres:
either `$JOBFETCHER_DB_URL` (a Postgres you point at) or a throwaway container that
testcontainers spins up. SKIPS CLEANLY (like `load_probe`) when neither is available — no
Docker, no testcontainers, no DB URL. This proves: `alembic upgrade head` builds the schema,
and a `DissectedPosting` saved → read back is equal (skills survive the JSONB hop).

Restricted network (Docker Hub pulls 403-blocked / no Ryuk image)? Skip testcontainers and
point `$JOBFETCHER_DB_URL` at a Postgres from any cached image::

    docker run -d -e POSTGRES_PASSWORD=postgres -p 5433:5432 postgres:14
    JOBFETCHER_DB_URL=postgresql+psycopg2://postgres:postgres@localhost:5433/postgres pytest -m integration
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from jobfetcher.adapters.repository_postgres import PostgresRepository
from jobfetcher.core.models import DissectedPosting, RequirementLevel, Skill
from jobfetcher.db.engine import make_engine

pytestmark = pytest.mark.integration


def _alembic_upgrade(url: str) -> None:
    """Build the schema with the same Alembic config the deploy uses."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    os.environ["JOBFETCHER_DB_URL"] = url  # env.py reads this
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    """A live local Postgres URL: an explicit $JOBFETCHER_DB_URL, else a testcontainer.
    Skips the whole module cleanly if no DB is reachable."""
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
            yield pg.get_connection_url()  # psycopg2 driver URL
    except Exception as e:  # Docker not running / image pull failure
        pytest.skip(f"no local Postgres available (Docker?): {type(e).__name__}: {e}")


@pytest.fixture(scope="module")
def repo(db_url: str) -> PostgresRepository:
    _alembic_upgrade(db_url)
    return PostgresRepository.from_engine(make_engine(db_url))


def _sample_dissected() -> DissectedPosting:
    return DissectedPosting(
        raw_title="Senior Data Engineer",
        language="en",
        location="Riyadh, Saudi Arabia",
        city="Riyadh",
        country="SA",
        employment_type="Full-time",
        seniority="senior",
        normalized_title="Data Engineer",
        sector="fintech",
        skills=[
            Skill(name="Python", level="must", evidence="3+ years with Python"),
            Skill(name="Airflow", level="nice", evidence="Airflow is a plus"),
        ],
        model="deepseek-v4-flash",
        dropped_skill_count=1,
    )


def test_alembic_built_all_tables(repo: PostgresRepository):
    from sqlalchemy import inspect

    names = set(inspect(repo.engine).get_table_names())
    assert {"bronze_posting", "posting", "cluster", "score", "profile"} <= names


def test_round_trip_dissected_posting(repo: PostgresRepository):
    repo.upsert_bronze(
        bronze_id="b-1",
        source="jsearch",
        source_job_id="job-1",
        raw_payload={"job_title": "Senior Data Engineer"},
        run_id="run-1",
        s3_raw_key="raw/jsearch/2026-06-27/job-1.json",
    )
    d = _sample_dissected()
    repo.save_posting(
        d,
        posting_id="p-1",
        bronze_id="b-1",
        source="jsearch",
        source_job_id="job-1",
        run_id="run-1",
        company="Acme",
        apply_url="https://example.com/apply",
        description="We build pipelines.",
        state=None,
        pipeline_version="v0",
    )
    got = repo.get_posting("p-1")
    assert got == d  # full contract round-trips equal, including skills (JSONB)
    assert got is not None and got.skills[1].level is RequirementLevel.nice


def test_get_missing_posting_returns_none(repo: PostgresRepository):
    # negative: an unknown id is None, not an error.
    assert repo.get_posting("does-not-exist") is None


def test_upsert_bronze_is_idempotent(repo: PostgresRepository):
    # re-fetching the same source id the same day must not duplicate the immutable bronze row.
    from sqlalchemy import func, select

    from jobfetcher.db import tables

    for _ in range(2):
        repo.upsert_bronze(
            bronze_id="b-dup",
            source="jsearch",
            source_job_id="job-dup",
            raw_payload={"k": "v"},
            run_id="run-1",
        )
    with repo.engine.connect() as conn:
        count = conn.execute(
            select(func.count()).select_from(tables.bronze_posting).where(
                tables.bronze_posting.c.bronze_id == "b-dup"
            )
        ).scalar_one()
    assert count == 1


def test_save_posting_is_idempotent(repo: PostgresRepository):
    # re-saving the same posting_id updates in place (no duplicate silver row).
    from sqlalchemy import func, select

    from jobfetcher.db import tables

    d = _sample_dissected()
    for _ in range(2):
        repo.save_posting(
            d,
            posting_id="p-dup",
            bronze_id="b-1",
            source="jsearch",
            source_job_id="job-1",
            run_id="run-1",
        )
    with repo.engine.connect() as conn:
        count = conn.execute(
            select(func.count()).select_from(tables.posting).where(
                tables.posting.c.posting_id == "p-dup"
            )
        ).scalar_one()
    assert count == 1
