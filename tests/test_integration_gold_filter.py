"""Step-4b gold filter integration on a REAL local Postgres (the running `jobfetcher-db` or
`$JOBFETCHER_DB_URL`). Seeds a few silver `posting` rows → runs `apply_gold_filter` with the
deterministic strategy (then a FakeLlm strategy) → asserts the fits got `status='gold_candidate'`
+ a 1:1 `cluster` row each + `posting.cluster_id` set, while non-fits stay `silver`.

Also seeds + reads a `profile` row (Repository.get_profile + Profile.from_jsonb).

SKIPS CLEANLY when no Postgres is reachable — same discipline as the C-2 / ingest integration
tests."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

from jobfetcher.adapters.filter_deterministic import DeterministicFilterStrategy
from jobfetcher.adapters.filter_llm import LlmFilterStrategy
from jobfetcher.core.ingest import apply_gold_filter
from jobfetcher.core.models import DissectedPosting, Skill
from jobfetcher.core.profile import Profile
from jobfetcher.core.search_spec import SearchSpec
from tests.helpers import FakeLlm

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


def _spec() -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch", "secret_name": "s", "aws_region": "us-east-1",
            "targeting": {"job_titles": ["Data Engineer"], "countries": ["sa"],
                          "cities": [], "states": []},
            "date_posted": "week", "language": "en", "employment_types": [],
            "remote": "off", "threshold": 60, "hard_floor": 50, "near_miss_band": 10,
            "reassess_max_age_days": 45, "digest_max_age_days": 90,
            "budget": {"max_pages_per_query": 1, "request_budget_per_run": 10},
        }
    )


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "name": "Tester",
            "skills": [{"name": "Python"}, {"name": "SQL"}],
            "preferences": {"target_titles": ["Data Engineer"],
                            "target_locations": ["Riyadh"], "avoid_keywords": []},
        }
    )


def _dissected(title: str, *, country="sa", city="Riyadh") -> DissectedPosting:
    return DissectedPosting(
        raw_title=title, language="en", location=city, city=city, country=country,
        seniority="mid", normalized_title=title, sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _seed_silver(repo, posting_id: str, dissected: DissectedPosting) -> None:
    """Land a bronze row + a silver posting (status='silver') the gold filter can read."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(
        bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
        raw_payload={"job_id": posting_id}, run_id="seed",
    )
    repo.save_posting(
        dissected, posting_id=posting_id, bronze_id=bronze_id, source="jsearch",
        source_job_id=posting_id, run_id="seed", status="silver",
    )


def _status_and_cluster(repo, posting_id: str) -> tuple[str, str | None]:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        row = conn.execute(
            select(tables.posting.c.status, tables.posting.c.cluster_id).where(
                tables.posting.c.posting_id == posting_id
            )
        ).mappings().first()
    return row["status"], row["cluster_id"]


def _cluster_count(repo, cluster_id: str) -> int:
    from sqlalchemy import func, select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(tables.cluster).where(
                tables.cluster.c.cluster_id == cluster_id
            )
        ).scalar_one()


# --------------------------------------------------------------------------- tests
def test_deterministic_gold_filter_marks_fits_and_clusters(repo):
    tag = uuid4().hex[:8]
    fit = f"fit-{tag}"
    drop = f"drop-{tag}"
    _seed_silver(repo, fit, _dissected("Data Engineer"))
    _seed_silver(repo, drop, _dissected("Registered Nurse", country="us", city="Austin"))

    summary = apply_gold_filter(
        _spec(), _profile(), strategy=DeterministicFilterStrategy(), repo=repo
    )
    # at least our two seeded rows are accounted for (the persistent DB may hold others)
    assert summary["silver"] >= 2
    assert summary["gold"] >= 1 and summary["dropped"] >= 1

    # the fit: promoted, attached to a 1:1 cluster
    status, cluster_id = _status_and_cluster(repo, fit)
    assert status == "gold_candidate"
    assert cluster_id == fit
    assert _cluster_count(repo, fit) == 1

    # the non-fit: untouched
    drop_status, drop_cluster = _status_and_cluster(repo, drop)
    assert drop_status == "silver"
    assert drop_cluster is None


def test_llm_gold_filter_includes_on_likely_fit(repo):
    tag = uuid4().hex[:8]
    pid = f"llm-{tag}"
    _seed_silver(repo, pid, _dissected("Data Engineer"))

    llm = FakeLlm(json.dumps({"likely_fit": True, "reason": "matches"}))
    apply_gold_filter(_spec(), _profile(), strategy=LlmFilterStrategy(llm), repo=repo)

    status, cluster_id = _status_and_cluster(repo, pid)
    assert status == "gold_candidate"
    assert cluster_id == pid


def test_gold_filter_is_idempotent_on_cluster(repo):
    # re-running the gold filter over an already-gold candidate must not error or duplicate the
    # 1:1 cluster (upsert_cluster is on_conflict_do_nothing).
    tag = uuid4().hex[:8]
    pid = f"idem-{tag}"
    _seed_silver(repo, pid, _dissected("Data Engineer"))
    apply_gold_filter(_spec(), _profile(), strategy=DeterministicFilterStrategy(), repo=repo)
    # second pass: the posting is now gold_candidate (not silver) so the filter won't re-touch
    # it; assert the cluster stayed 1:1 regardless.
    apply_gold_filter(_spec(), _profile(), strategy=DeterministicFilterStrategy(), repo=repo)
    assert _cluster_count(repo, pid) == 1


def test_get_profile_round_trips(repo):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jobfetcher.db import tables

    prof = _profile()
    user_id = f"user-{uuid4().hex[:8]}"
    stmt = pg_insert(tables.profile).values(
        user_id=user_id, profile=prof.model_dump(),
        threshold=60, hard_floor=50, near_miss_band=10,
    ).on_conflict_do_nothing(index_elements=["user_id"])
    with repo.engine.begin() as conn:
        conn.execute(stmt)

    row = repo.get_profile(user_id)
    assert row is not None
    assert row["threshold"] == 60 and row["hard_floor"] == 50 and row["near_miss_band"] == 10
    rebuilt = Profile.from_jsonb(row["profile"])
    assert rebuilt == prof
    # negative: an unknown user is None, not an error
    assert repo.get_profile("no-such-user") is None
