"""ERR-010 regression: the gold-filter read must stay inside the RDS Data API's 1 MB result
cap, and a rejection must be recorded durably enough to keep it out of the next read.

**Why this file exists.** Every other repository test uses an in-memory fake, so nothing in
the suite ever exercised *result size*. On 2026-07-25 `get_silver_postings()` — which ran
`select(tables.posting)`, all 22 columns, for every silver row — crossed 1 MB against the
deployed Aurora and killed 38 consecutive daily runs with `UnsupportedResultException`. Local
tests and CI both run psycopg2, where 3 MB is a non-event, so nothing could have caught it.

These tests cannot reproduce the Data API's cap (that transport has no local equivalent —
the same detection gap ERR-004 named). What they CAN do is pin the property that keeps the
read under it: the payload must be a function of the columns the mapper consumes, not of the
whole row. Test A asserts that directly by making `description` enormous — under the old
`select(tables.posting)` the returned payload grew with it; under the projection it does not.

Every test owns only the rows it creates (a per-test `posting_id` prefix): the dev Postgres
is shared with the other integration modules, so a blanket `DELETE FROM posting` would both
destroy their fixtures and trip `application_event`'s foreign key.

SKIPS CLEANLY when no Postgres is reachable — same discipline as the other integration tests.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from jobfetcher.core.models import DissectedPosting, Skill
from jobfetcher.db import tables

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
def repo(db_url: str) -> Iterator:
    from jobfetcher.adapters.repository_postgres import PostgresRepository
    from jobfetcher.db.engine import make_engine

    _alembic_upgrade(db_url)
    engine = make_engine(db_url)
    yield PostgresRepository.from_engine(engine)
    # Dispose the pool when the module finishes. `test_integration_handler` cleans with
    # `TRUNCATE ... CASCADE`, which needs an ACCESS EXCLUSIVE lock on every named table — a
    # module-scoped engine left holding pooled connections for the rest of the session can
    # block it. Observed once as an intermittent failure in that module, on the run that
    # first introduced this file.
    engine.dispose()


@pytest.fixture()
def prefix(repo) -> Iterator[str]:
    """A per-test `posting_id` namespace, cleaned up afterwards. See the module docstring."""
    pfx = f"rsz-{uuid4().hex[:8]}"
    yield pfx
    with repo.engine.begin() as conn:
        conn.execute(tables.posting.delete().where(tables.posting.c.posting_id.like(f"{pfx}%")))
        conn.execute(
            tables.bronze_posting.delete().where(
                tables.bronze_posting.c.bronze_id.like(f"jsearch:{pfx}%")
            )
        )


def _mine(prefix: str):
    """The WHERE clause scoping a query to one test's rows."""
    return tables.posting.c.posting_id.like(f"{prefix}%")


def _ids(rows, prefix: str) -> list[str]:
    """The ids this test owns, out of a repository read that returns the whole table."""
    return sorted(pid for pid, _ in rows if pid.startswith(prefix))


def _dissected(title: str = "Data Engineer") -> DissectedPosting:
    return DissectedPosting(
        raw_title=title, language="en", location="Riyadh", city="Riyadh", country="sa",
        seniority="mid", normalized_title=title, sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _seed(repo, prefix: str, name: str, *, description: str = "", status: str = "silver") -> str:
    posting_id = f"{prefix}-{name}"
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(
        bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
        raw_payload={"job_id": posting_id}, run_id="seed",
    )
    repo.save_posting(
        _dissected(), posting_id=posting_id, bronze_id=bronze_id, source="jsearch",
        source_job_id=posting_id, run_id="seed", status=status, description=description,
    )
    return posting_id


# --------------------------------------------------------------------------- Test A: size
@contextmanager
def _capture_sql(engine) -> Iterator[list[str]]:
    """Record the SQL actually emitted to the driver.

    This has to hook the wire, not the return value. The first version of this test measured
    the returned `DissectedPosting` objects and passed against the *broken* code — of course
    it did: the mapper never reads `description`, so its output is small whatever the SELECT
    asked for. The bytes that blew the 1 MB cap were the ones the *server* sent back, which
    only the emitted statement can tell you about.
    """
    seen: list[tuple[str, object]] = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _before)


def test_bulk_read_does_not_select_the_unread_description_column(repo, prefix):
    """The outage in one assertion.

    `description` is written once and read nowhere — `_dissected_from_row` never touches it.
    On the live table it was 1,143 kB of the 3,110 kB that `select(tables.posting)` dragged
    across the Data API every morning, until the read crossed 1 MB and killed 38 consecutive
    daily runs. The gold filter's read must not ask for it.
    """
    fat = "x" * 200_000  # 200 KB of JD text per row — 1 MB across five rows
    for i in range(5):
        _seed(repo, prefix, f"p{i}", description=fat)

    with _capture_sql(repo.engine) as emitted:
        rows = repo.get_silver_postings()
    assert len(_ids(rows, prefix)) == 5

    stmt, params = next((s, p) for s, p in reversed(emitted) if "FROM posting" in s)
    assert "posting.description" not in stmt, (
        "get_silver_postings is selecting the description column again — this is ERR-010"
    )

    # And prove it in bytes: replay the exact statement the repository emitted (with its own
    # bound parameters) and weigh what the server sends back, against the same rows read whole.
    with repo.engine.connect() as conn:
        emitted_bytes = sum(
            len(str(r))
            for r in conn.exec_driver_sql(stmt, params).mappings().all()
            if prefix in str(r)
        )
        whole_row_bytes = sum(
            len(str(r))
            for r in conn.execute(select(tables.posting).where(_mine(prefix))).mappings().all()
        )

    assert whole_row_bytes > 1_000_000  # the fat rows really are in the table
    assert emitted_bytes < 100_000      # and the gold filter's read does not carry them


def test_projection_still_returns_every_mapped_field(repo, prefix):
    """negative twin for Test A: a projection that dropped a column the mapper needs would
    make the read small and the data wrong. Every `DissectedPosting` field must survive."""
    posting_id = _seed(repo, prefix, "full")
    dissected = next(d for pid, d in repo.get_silver_postings() if pid == posting_id)

    assert dissected.raw_title == "Data Engineer"
    assert dissected.normalized_title == "Data Engineer"
    assert dissected.language == "en"
    assert dissected.location == "Riyadh"
    assert dissected.city == "Riyadh"
    assert dissected.country == "sa"
    assert dissected.seniority == "mid"
    assert dissected.sector == "fintech"
    assert dissected.model == "test-model"
    assert [s.name for s in dissected.skills] == ["Python"]


# ------------------------------------------------- Tests B/C: rejection lineage over real SQL
def test_rejection_stamp_round_trips_and_excludes_the_row(repo, prefix):
    """Test C over real SQL: a stamped rejection leaves the candidate set."""
    keep = _seed(repo, prefix, "keep")
    drop = _seed(repo, prefix, "drop")

    repo.mark_gold_rejected(drop, filter_hash="hash-A")

    assert _ids(repo.get_silver_postings(filter_hash="hash-A"), prefix) == [keep]

    with repo.engine.connect() as conn:
        row = conn.execute(
            select(tables.posting.c.status, tables.posting.c.gold_filter_hash).where(
                tables.posting.c.posting_id == drop
            )
        ).mappings().one()
    assert row["status"] == "rejected"
    assert row["gold_filter_hash"] == "hash-A"


def test_a_changed_hash_reopens_the_rejection(repo, prefix):
    """Test B over real SQL: the `IS DISTINCT FROM` predicate re-opens a rejection made under
    a context that no longer applies."""
    drop = _seed(repo, prefix, "drop")
    repo.mark_gold_rejected(drop, filter_hash="hash-A")

    assert _ids(repo.get_silver_postings(filter_hash="hash-A"), prefix) == []
    assert _ids(repo.get_silver_postings(filter_hash="hash-B"), prefix) == [drop]


def test_null_stamp_counts_as_unjudged(repo, prefix):
    """The `IS DISTINCT FROM` (not `!=`) case, which a NULL would silently swallow.

    A row rejected before migration 0007 — or written by an older build — has a NULL stamp.
    Under `!=` that comparison is NULL, the row never matches, and it disappears from every
    future read: the exact permanent-loss failure this design exists to avoid.
    """
    legacy = _seed(repo, prefix, "legacy")
    with repo.engine.begin() as conn:
        conn.execute(
            tables.posting.update()
            .where(tables.posting.c.posting_id == legacy)
            .values(status="rejected", gold_filter_hash=None)
        )

    assert _ids(repo.get_silver_postings(filter_hash="hash-A"), prefix) == [legacy]


def test_without_a_hash_rejected_rows_stay_out(repo, prefix):
    # negative: the legacy call shape reads only 'silver' — it must not accidentally sweep in
    # rejected rows, or a pre-0007 caller would re-do all of history.
    silver = _seed(repo, prefix, "silver")
    drop = _seed(repo, prefix, "drop")
    repo.mark_gold_rejected(drop, filter_hash="hash-A")

    assert _ids(repo.get_silver_postings(), prefix) == [silver]


# ------------------------------------------------------- pagination (ERR-013)
def test_read_returns_every_row_across_page_boundaries(repo, prefix):
    """The bug the first fix missed: column projection alone was not enough.

    The 1 MB cap applies to the SERIALIZED RESPONSE, but the projection was sized on
    `pg_column_size()` — the COMPRESSED ON-DISK size. Measured live: the `skills` JSONB was
    537 kB on disk and **1,291 kB as text**, so the "fixed" read was still ~1.4 MB and still
    died in production. The read is now keyset-paginated, which makes it bounded by page size
    rather than by table size.

    This seeds more rows than one page holds and asserts every one comes back — a loop that
    stops after the first page, or one that never terminates, both fail here.
    """
    from jobfetcher.adapters.repository_postgres import _SILVER_PAGE_SIZE

    n = _SILVER_PAGE_SIZE + 37  # deliberately not a multiple of the page size
    for i in range(n):
        _seed(repo, prefix, f"p{i:04d}")

    got = _ids(repo.get_silver_postings(), prefix)
    assert len(got) == n
    assert len(set(got)) == n            # no row served twice by an off-by-one keyset
    assert got == sorted(got)            # ordering is what makes the keyset correct


def test_limit_is_honoured_across_pages(repo, prefix):
    # negative twin: `limit` must still cap the total, not the page — otherwise a caller asking
    # for 10 rows gets a full table walk, which is the failure mode in reverse.
    from jobfetcher.adapters.repository_postgres import _SILVER_PAGE_SIZE

    for i in range(_SILVER_PAGE_SIZE + 10):
        _seed(repo, prefix, f"p{i:04d}")

    # Count the WHOLE result, not the prefix-filtered slice: `limit` is a global cap, and
    # other modules' rows may sort ahead of this test's on a shared database.
    assert len(repo.get_silver_postings(limit=5)) == 5
    # and a limit larger than one page still walks past the boundary
    assert len(repo.get_silver_postings(limit=_SILVER_PAGE_SIZE + 3)) == _SILVER_PAGE_SIZE + 3
