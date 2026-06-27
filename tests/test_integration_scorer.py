"""Step-5 scoring integration on a REAL local Postgres (the running `jobfetcher-db` or
`$JOBFETCHER_DB_URL`). Seeds a few `gold_candidate` postings (+ their 1:1 clusters) + a
`profile` row → runs `score_gold` with a FakeLlm → asserts `score` rows saved keyed on
`cluster_id`, `posting.status='scored'`, counts correct, and that a re-run is idempotent
(upsert, not duplicate). SKIPS CLEANLY when no Postgres is reachable."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

from jobfetcher.core.ingest import score_gold
from jobfetcher.core.models import DissectedPosting, Skill
from jobfetcher.core.profile import Profile
from jobfetcher.core.scorer import Scorer
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


def _profile() -> Profile:
    return Profile.model_validate(
        {
            "name": "Tester",
            "skills": [{"name": "Python"}, {"name": "SQL"}],
            "preferences": {"target_titles": ["Data Engineer"],
                            "target_locations": ["Riyadh"], "avoid_keywords": []},
        }
    )


def _dissected(title: str) -> DissectedPosting:
    return DissectedPosting(
        raw_title=title, language="en", location="Riyadh", city="Riyadh", country="sa",
        seniority="mid", normalized_title=title, sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _score_json(score: int) -> str:
    return json.dumps({
        "score": score, "strengths": ["python"], "gaps": ["spark"],
        "strategic_assessment": "play the pipeline project", "poster_type": "direct employer",
        "legitimacy_verified": True,
    })


def _seed_gold(repo, posting_id: str, dissected: DissectedPosting) -> str:
    """Land a bronze + silver posting, then promote it to a 1:1 gold candidate (like Step 4b)."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
                       raw_payload={"job_id": posting_id}, run_id="seed")
    repo.save_posting(dissected, posting_id=posting_id, bronze_id=bronze_id, source="jsearch",
                      source_job_id=posting_id, run_id="seed", status="silver")
    repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
    repo.set_posting_cluster(posting_id, posting_id)
    repo.mark_gold_candidate(posting_id)
    return posting_id


def _seed_profile(repo, user_id: str, threshold: int = 60) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jobfetcher.db import tables

    stmt = pg_insert(tables.profile).values(
        user_id=user_id, profile=_profile().model_dump(),
        threshold=threshold, hard_floor=50, near_miss_band=10,
    ).on_conflict_do_update(
        index_elements=["user_id"],
        set_={"threshold": threshold, "hard_floor": 50, "near_miss_band": 10},
    )
    with repo.engine.begin() as conn:
        conn.execute(stmt)


def _score_row(repo, cluster_id: str):
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return conn.execute(
            select(tables.score).where(tables.score.c.cluster_id == cluster_id)
        ).mappings().all()


def _status(repo, posting_id: str) -> str:
    from sqlalchemy import select

    from jobfetcher.db import tables

    with repo.engine.connect() as conn:
        return conn.execute(
            select(tables.posting.c.status).where(tables.posting.c.posting_id == posting_id)
        ).scalar_one()


# --------------------------------------------------------------------------- tests
def test_score_gold_saves_scores_and_marks_scored(repo):
    tag = uuid4().hex[:8]
    user = f"user-{tag}"
    high = _seed_gold(repo, f"high-{tag}", _dissected("Data Engineer"))
    low = _seed_gold(repo, f"low-{tag}", _dissected("Nurse"))
    _seed_profile(repo, user, threshold=60)

    scorer = Scorer(FakeLlm(_score_json(90), _score_json(30)))  # ordered by posting_id: high<low alpha?
    summary = score_gold(run_id="r", repo=repo, scorer=scorer, user_id=user)

    # both scored; only the >=60 one surfaced (the FakeLlm replies map by deterministic order)
    assert summary["gold"] >= 2
    assert summary["failed"] == 0

    for pid in (high, low):
        assert _status(repo, pid) == "scored"
        rows = _score_row(repo, pid)
        assert len(rows) == 1  # exactly one score row per cluster
        assert rows[0]["fit_category"] in {"strong_fit", "near_miss", "stretch", "misaligned"}
        assert rows[0]["strengths"] and rows[0]["gaps"]


def test_score_gold_is_idempotent_upsert(repo):
    tag = uuid4().hex[:8]
    user = f"user-{tag}"
    pid = _seed_gold(repo, f"idem-{tag}", _dissected("Data Engineer"))
    _seed_profile(repo, user, threshold=60)

    score_gold(run_id="r1", repo=repo, scorer=Scorer(FakeLlm(_score_json(70))), user_id=user)
    # re-promote so it's scoreable again, then re-score with a different value
    repo.mark_gold_candidate(pid)
    score_gold(run_id="r2", repo=repo, scorer=Scorer(FakeLlm(_score_json(85))), user_id=user)

    rows = _score_row(repo, pid)
    assert len(rows) == 1  # upsert: still exactly one row, not duplicated
    assert rows[0]["score"] == 85
    assert rows[0]["previous_score"] == 70  # the prior score carried forward
