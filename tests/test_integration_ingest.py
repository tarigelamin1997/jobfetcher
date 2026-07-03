"""Step-4 ingestion integration: moto S3 + a REAL local Postgres + JSearch mocked + FakeLlm.

Proves the wiring end-to-end against real S3 (moto) + real Postgres (the running `jobfetcher-db`
or `$JOBFETCHER_DB_URL`): fetch_to_bronze lands S3 objects + bronze rows, a re-run adds no dup
bronze, land_silver writes a posting (with fingerprint), and full `ingest` produces the summary.

SKIPS CLEANLY when moto isn't installed or no Postgres is reachable — same discipline as the
C-2 DB integration test. A live JSearch/DeepSeek variant is intentionally out of scope here
(covered by the optional live test); JSearch is mocked and the LLM is the FakeLlm.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from jobfetcher.core.ingest import fetch_to_bronze, ingest, land_silver
from jobfetcher.core.search_spec import SearchSpec
from tests.helpers import CANNED_LLM_JSON, FakeLlm

pytestmark = pytest.mark.integration

moto = pytest.importorskip("moto", reason="moto not installed (dev extra)")
from moto import mock_aws  # noqa: E402

BUCKET = "jobfetcher-test-data"


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


@pytest.fixture
def raw_store() -> Iterator:
    from jobfetcher.adapters.s3_raw import S3RawStore

    with mock_aws():
        import boto3

        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield S3RawStore(bucket=BUCKET, client=boto3.client("s3", region_name="us-east-1"))


def _spec() -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch", "secret_name": "s", "aws_region": "us-east-1",
            "targeting": {"job_titles": ["de"], "countries": ["sa"], "cities": [], "states": []},
            "date_posted": "week", "language": "en", "employment_types": [],
            "remote": "off", "threshold": 60, "hard_floor": 50, "near_miss_band": 10,
            "budget": {"max_pages_per_query": 1, "request_budget_per_run": 10},
        }
    )


def _job(jid: str) -> dict:
    return {
        "job_id": jid,
        "job_title": "Senior Data Engineer",
        "job_description": (
            "Required: 3+ years with Python and SQL. Experience with Airflow is a plus. "
            "You will build ETL pipelines on AWS."
        ),
        "employer_name": "Acme",
        "job_apply_link": "https://x/apply",
        "job_location": "Riyadh", "job_city": "Riyadh", "job_country": "SA",
        "job_employment_type": "FULLTIME", "job_state": None,
    }


class FakeSource:
    def __init__(self, jobs: list[dict]) -> None:
        self._jobs = jobs

    def fetch(self, spec, *, run_id):
        yield from self._jobs


def _count_bronze(repo, bronze_id: str) -> int:
    from sqlalchemy import func, select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(tables.bronze_posting).where(
                tables.bronze_posting.c.bronze_id == bronze_id
            )
        ).scalar_one()


# --------------------------------------------------------------------------- tests
def test_fetch_to_bronze_lands_s3_and_rows(repo, raw_store):
    from datetime import date

    src = FakeSource([_job("ib1"), _job("ib2")])
    landed = fetch_to_bronze(
        _spec(), run_id="run-i", source="jsearch", source_adapter=src,
        raw_store=raw_store, repo=repo,
    )
    assert {bid for bid, _, _ in landed} == {"jsearch:ib1", "jsearch:ib2"}
    # S3 object exists at the medallion key raw/{source}/{date}/{id}.json
    key = f"raw/jsearch/{date.today().isoformat()}/ib1.json"
    body = raw_store._client.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    assert b"ib1" in body
    assert _count_bronze(repo, "jsearch:ib1") == 1


def test_rerun_adds_no_duplicate_bronze(repo, raw_store):
    src = FakeSource([_job("idup")])
    for _ in range(2):
        fetch_to_bronze(
            _spec(), run_id="run-i", source="jsearch", source_adapter=src,
            raw_store=raw_store, repo=repo,
        )
    assert _count_bronze(repo, "jsearch:idup") == 1  # idempotent on bronze_id


def test_land_silver_writes_posting_with_fingerprint(repo, raw_store):
    from jobfetcher.core.dissector import Dissector

    fetch_to_bronze(
        _spec(), run_id="run-i", source="jsearch",
        source_adapter=FakeSource([_job("isil")]), raw_store=raw_store, repo=repo,
    )
    pid = land_silver(
        "jsearch:isil", _job("isil"), run_id="run-i", source="jsearch", source_job_id="isil",
        dissector=Dissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model"), repo=repo,
    )
    assert pid == "jsearch:isil"
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        row = conn.execute(
            select(tables.posting.c.fingerprint, tables.posting.c.company).where(
                tables.posting.c.posting_id == "jsearch:isil"
            )
        ).mappings().first()
    assert row is not None and row["fingerprint"] and row["company"] == "Acme"


def test_full_ingest_end_to_end(repo, raw_store):
    from uuid import uuid4

    from jobfetcher.core.dissector import Dissector

    # Unique ids per run: the local DB is persistent, so fixed ids would (correctly, by C2) be
    # counted as `already` on a re-run. Fresh ids exercise the silvered path deterministically.
    run_tag = uuid4().hex[:8]
    id1, id2 = f"ie1-{run_tag}", f"ie2-{run_tag}"
    summary = ingest(
        _spec(), run_id=f"run-e2e-{run_tag}",
        source_adapter=FakeSource([_job(id1), _job(id2)]),
        raw_store=raw_store, repo=repo,
        dissector=Dissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model"),
    )
    assert summary == {"fetched": 2, "bronzed": 2, "silvered": 2, "skipped": 0, "already": 0, "deferred": 0}
    assert repo.get_posting(f"jsearch:{id1}") is not None


def test_full_ingest_rerun_counts_already(repo, raw_store):
    # C2 (integration): a second `ingest` over the same id against the REAL DB does not
    # re-silver — it is counted as `already` (no wasted LLM call on a re-run).
    from uuid import uuid4

    from jobfetcher.core.dissector import Dissector

    jid = f"iag-{uuid4().hex[:8]}"
    dissector = Dissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model")
    common = dict(raw_store=raw_store, repo=repo, dissector=dissector)
    first = ingest(_spec(), run_id="run-a1", source_adapter=FakeSource([_job(jid)]), **common)
    assert first["silvered"] == 1 and first["already"] == 0
    second = ingest(_spec(), run_id="run-a2", source_adapter=FakeSource([_job(jid)]), **common)
    assert second["silvered"] == 0 and second["already"] == 1
