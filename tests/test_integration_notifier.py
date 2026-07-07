"""Step-6 Notifier integration on a REAL local Postgres (`jobfetcher-db` / `$JOBFETCHER_DB_URL`)
+ moto SES. Seeds bronze→silver→gold→score rows (some >= threshold, some below) + a `profile`,
then runs `notify(real repo, SesNotifier over a moto ses client)` and asserts exactly one email
is sent whose body carries the surfaced jobs + the below count, with correct counts. Also the
zero-matches path (all below threshold → a "no matches" email still sent — VG5 negative).
SKIPS CLEANLY without the DB or moto."""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest

from jobfetcher.core.ingest import notify
from jobfetcher.core.models import DissectedPosting, Skill

pytestmark = pytest.mark.integration

moto = pytest.importorskip("moto", reason="moto not installed (dev extra)")
from moto import mock_aws  # noqa: E402


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


def _seed_scored(repo, posting_id: str, title: str, score: int, *, company: str,
                 apply_url: str) -> None:
    """Land bronze→silver→gold→score so the posting is a full scored row the digest reads."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
                       raw_payload={"job_id": posting_id}, run_id="seed")
    repo.save_posting(_dissected(title), posting_id=posting_id, bronze_id=bronze_id,
                      source="jsearch", source_job_id=posting_id, run_id="seed",
                      company=company, apply_url=apply_url, status="silver")
    repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
    repo.set_posting_cluster(posting_id, posting_id)
    repo.mark_gold_candidate(posting_id)
    repo.save_score(cluster_id=posting_id, score=score,
                    fit_category="strong_fit" if score >= 60 else "misaligned",
                    strengths=[f"strong fit for {title}"], gaps=["no Spark"],
                    strategic_assessment="play it well", poster_type="direct employer",
                    legitimacy_verified=True, scoring_model="test-model",
                    profile_hash="ph-seed", run_id="seed")
    repo.mark_scored(posting_id)


def _seed_profile(repo, user_id: str, threshold: int = 60) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from jobfetcher.db import tables

    stmt = pg_insert(tables.profile).values(
        user_id=user_id, profile={"name": "Tester"},
        threshold=threshold, hard_floor=50, near_miss_band=10,
    ).on_conflict_do_update(
        index_elements=["user_id"],
        set_={"threshold": threshold, "hard_floor": 50, "near_miss_band": 10},
    )
    with repo.engine.begin() as conn:
        conn.execute(stmt)


def _truncate_pipeline(repo) -> None:
    """Reset the pipeline tables so the GLOBAL shortlist join (`get_scored_shortlist` reads the
    whole `score`↔`posting` set — single-user v0) sees only THIS test's seed. Other integration
    modules share the same DB and leave scored rows behind."""
    from sqlalchemy import text as _text

    with repo.engine.begin() as conn:
        conn.execute(_text(
            "TRUNCATE score, posting, cluster, bronze_posting RESTART IDENTITY CASCADE"
        ))


@pytest.fixture
def ses() -> Iterator:
    """A moto SES client with the sender identity verified (sandbox send needs it)."""
    with mock_aws():
        import boto3

        client = boto3.client("ses", region_name="us-east-1")
        client.verify_email_identity(EmailAddress="from@jobfetcher.test")
        yield client


# --------------------------------------------------------------------------- tests
def test_notify_sends_digest_with_surfaced_jobs_and_below_count(repo, ses):
    from jobfetcher.adapters.ses_notifier import SesNotifier

    _truncate_pipeline(repo)
    tag = uuid4().hex[:8]
    user = f"user-{tag}"
    _seed_profile(repo, user, threshold=60)
    _seed_scored(repo, f"hi-{tag}", "Senior Data Engineer", 92,
                 company="Acme", apply_url="https://jobs.test/apply/hi")
    _seed_scored(repo, f"mid-{tag}", "Data Engineer", 70,
                 company="Beta", apply_url="https://jobs.test/apply/mid")
    _seed_scored(repo, f"lo-{tag}", "Junior Analyst", 40,
                 company="Gamma", apply_url="https://jobs.test/apply/lo")

    notifier = SesNotifier(sender="from@jobfetcher.test", client=ses)
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@jobfetcher.test",
                 user_id=user, run_date=date(2026, 6, 27))

    assert out["surfaced"] == 2  # 92 and 70 clear 60
    assert out["below_threshold"] == 1  # 40 is below
    assert out["sent"] == 1

    # moto records the send; assert exactly one email with the right content.
    mine = [m for m in _moto_sent_messages() if "to@jobfetcher.test" in m["destinations"]]
    assert len(mine) == 1
    body = mine[0]["body"]  # moto exposes the HTML part as .body
    assert "Senior Data Engineer" in body and "Data Engineer" in body
    assert "Acme" in body and "Beta" in body
    assert "https://jobs.test/apply/hi" in body
    assert "+1 more scored below your threshold of 60" in body  # v0.6.0 footer phrasing


def test_notify_zero_matches_still_sends_no_matches_email(repo, ses):
    from jobfetcher.adapters.ses_notifier import SesNotifier

    _truncate_pipeline(repo)
    tag = uuid4().hex[:8]
    user = f"user-{tag}"
    _seed_profile(repo, user, threshold=60)
    # all below threshold → nothing surfaces
    _seed_scored(repo, f"lo1-{tag}", "Junior Analyst", 30,
                 company="Gamma", apply_url="https://jobs.test/apply/lo1")
    _seed_scored(repo, f"lo2-{tag}", "Receptionist", 20,
                 company="Delta", apply_url="https://jobs.test/apply/lo2")

    notifier = SesNotifier(sender="from@jobfetcher.test", client=ses)
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="zero@jobfetcher.test",
                 user_id=user, run_date=date(2026, 6, 27))

    assert out["surfaced"] == 0
    assert out["below_threshold"] == 2
    assert out["sent"] == 1

    mine = [m for m in _moto_sent_messages() if "zero@jobfetcher.test" in m["destinations"]]
    assert len(mine) == 1
    assert "no matches" in mine[0]["subject"].lower()
    assert "2 scored" in mine[0]["body"]


def _moto_sent_messages():
    """Read moto's recorded SES sends (the in-memory backend). moto's SESMessage exposes
    `.subject`, `.destinations` (a dict like `{'ToAddresses': [...]}`), and `.body` (the HTML
    part)."""
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.ses import ses_backends

    backend = ses_backends[DEFAULT_ACCOUNT_ID]["us-east-1"]
    out = []
    for msg in backend.sent_messages:
        dest = getattr(msg, "destinations", {}) or {}
        to = dest.get("ToAddresses", []) if isinstance(dest, dict) else list(dest)
        out.append({
            "destinations": list(to),
            "subject": getattr(msg, "subject", "") or "",
            "body": getattr(msg, "body", "") or "",
        })
    return out
