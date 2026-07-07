"""Step-6 Notifier integration on a REAL local Postgres (`jobfetcher-db` / `$JOBFETCHER_DB_URL`)
+ moto SES. Seeds bronze→silver→gold→score rows (some >= threshold, some below) + a `profile`,
then runs `notify(real repo, SesNotifier over a moto ses client)` and asserts exactly one email
is sent whose body carries the surfaced jobs + the below count, with correct counts. Also the
zero-matches path (all below threshold → a "no matches" email still sent — VG5 negative).

Digest truthfulness (real SQL): the `get_scored_shortlist` age cutoff on LIVE-SHAPED rows
(`posting.fetched_at` NULL — the bronze landing time via the LEFT JOIN carries the age; unknown
age INCLUDED; `0`/`None` unbounded), the new `previous_score`/`fingerprint`/`fetched_at` fields
round-tripping, and `get_last_digest_sent_at` (no rows → None; MAX + user-scoped).
SKIPS CLEANLY without the DB or moto."""
from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
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
                 apply_url: str, fingerprint: str | None = None,
                 previous_score: int | None = None) -> None:
    """Land bronze→silver→gold→score so the posting is a full scored row the digest reads.
    `fingerprint`/`previous_score` seed the digest-truthfulness fields when a test needs them."""
    bronze_id = f"jsearch:{posting_id}"
    repo.upsert_bronze(bronze_id=bronze_id, source="jsearch", source_job_id=posting_id,
                       raw_payload={"job_id": posting_id}, run_id="seed")
    repo.save_posting(_dissected(title), posting_id=posting_id, bronze_id=bronze_id,
                      source="jsearch", source_job_id=posting_id, run_id="seed",
                      company=company, apply_url=apply_url, fingerprint=fingerprint,
                      status="silver")
    repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
    repo.set_posting_cluster(posting_id, posting_id)
    repo.mark_gold_candidate(posting_id)
    repo.save_score(cluster_id=posting_id, score=score,
                    fit_category="strong_fit" if score >= 60 else "misaligned",
                    strengths=[f"strong fit for {title}"], gaps=["no Spark"],
                    strategic_assessment="play it well", poster_type="direct employer",
                    legitimacy_verified=True, scoring_model="test-model",
                    profile_hash="ph-seed", run_id="seed", previous_score=previous_score)
    repo.mark_scored(posting_id)


def _set_bronze_fetched_at(repo, posting_id: str, when: datetime) -> None:
    """Backdate the bronze landing time — the LIVE age source: `posting.fetched_at` stays NULL
    on every real row (`save_posting` never writes it), so `COALESCE` falls to bronze."""
    from sqlalchemy import update

    from jobfetcher.db import tables

    with repo.engine.begin() as conn:
        conn.execute(
            update(tables.bronze_posting)
            .where(tables.bronze_posting.c.bronze_id == f"jsearch:{posting_id}")
            .values(fetched_at=when)
        )


def _orphan_bronze_link(repo, posting_id: str) -> None:
    """NULL out `posting.bronze_id` — the pathological LEFT-JOIN miss where even the bronze
    fallback yields no age (COALESCE → NULL = unknown, must be INCLUDED)."""
    from sqlalchemy import update

    from jobfetcher.db import tables

    with repo.engine.begin() as conn:
        conn.execute(
            update(tables.posting)
            .where(tables.posting.c.posting_id == posting_id)
            .values(bronze_id=None)
        )


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


# --------------------------------------------------------------------------- digest truthfulness
def test_shortlist_age_cutoff_drops_old_on_live_shaped_rows(repo):
    """A3: the `max_age_days` cutoff over real SQL on LIVE-SHAPED rows — `posting.fetched_at`
    stays NULL (as `save_posting` leaves it), so the age comes from the bronze landing time via
    the LEFT JOIN. 100-day-old bronze → DROPPED (from the surfaced list AND the below count);
    10-day-old → kept; unknown age (orphaned bronze link, COALESCE'd NULL) → KEPT, never
    silently dropped; `0`/`None` = unbounded and identical (the regression guard)."""
    _truncate_pipeline(repo)
    tag = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    old, recent = f"old-{tag}", f"recent-{tag}"
    orphan, old_low = f"orphan-{tag}", f"oldlow-{tag}"
    _seed_scored(repo, old, "Old DE", 90, company="OldCo", apply_url="https://jobs.test/o")
    _seed_scored(repo, recent, "Recent DE", 85, company="RecCo", apply_url="https://jobs.test/r")
    _seed_scored(repo, orphan, "Orphan DE", 80, company="OrphCo", apply_url="https://jobs.test/p")
    _seed_scored(repo, old_low, "Old Analyst", 40, company="LowCo", apply_url="https://jobs.test/l")
    _set_bronze_fetched_at(repo, old, now - timedelta(days=100))
    _set_bronze_fetched_at(repo, old_low, now - timedelta(days=100))
    _set_bronze_fetched_at(repo, recent, now - timedelta(days=10))
    _orphan_bronze_link(repo, orphan)

    items, below = repo.get_scored_shortlist(threshold=60, max_age_days=45)
    ids = {i.posting_id for i in items}
    assert old not in ids     # negative: the bound BITES on live-shaped data
    assert recent in ids      # within the window
    assert orphan in ids      # unknown age → INCLUDED, never silently dropped
    assert below == 0         # the aged-out below-threshold row vanished from the count too

    # unbounded regression guard: 0 and None keep the old rows and behave identically
    items_none, below_none = repo.get_scored_shortlist(threshold=60)
    items_zero, below_zero = repo.get_scored_shortlist(threshold=60, max_age_days=0)
    assert {i.posting_id for i in items_none} == {i.posting_id for i in items_zero}
    assert old in {i.posting_id for i in items_none}
    assert below_none == below_zero == 1


def test_shortlist_carries_previous_score_fingerprint_and_age(repo):
    """The three new ShortlistItem fields round-trip from real rows: `previous_score` from the
    score row, `fingerprint` from `save_posting`, and `fetched_at` = the bronze landing time
    (the COALESCE — `posting.fetched_at` is NULL on live rows, pinned here)."""
    from sqlalchemy import select

    from jobfetcher.db import tables

    _truncate_pipeline(repo)
    tag = uuid4().hex[:8]
    pid = f"fields-{tag}"
    _seed_scored(repo, pid, "DE", 75, company="C", apply_url="https://jobs.test/a",
                 fingerprint="fp-abc", previous_score=55)

    # pin the live shape first: the posting row itself has NO fetched_at
    with repo.engine.connect() as conn:
        raw = conn.execute(
            select(tables.posting.c.fetched_at).where(tables.posting.c.posting_id == pid)
        ).scalar_one()
    assert raw is None

    items, _ = repo.get_scored_shortlist(threshold=60)
    (item,) = [i for i in items if i.posting_id == pid]
    assert item.previous_score == 55
    assert item.fingerprint == "fp-abc"
    assert item.fetched_at is not None  # the bronze landing time via the LEFT JOIN


def test_get_last_digest_sent_at_none_then_max_and_user_scoped(repo):
    """N7 at the DB level: no `run_log` rows for the user → None (NULL-safe, not an error).
    With two sends, MAX(digest_sent_at) wins — and another user's fresher row never leaks."""
    from sqlalchemy import update

    from jobfetcher.db import tables

    tag = uuid4().hex[:8]
    user, other = f"u-{tag}", f"other-{tag}"
    assert repo.get_last_digest_sent_at(user_id=user) is None  # no digest ever sent

    repo.mark_digest_sent(user_id=user, run_date=date(2026, 7, 1), run_id="r1")
    repo.mark_digest_sent(user_id=user, run_date=date(2026, 7, 2), run_id="r2")
    repo.mark_digest_sent(user_id=other, run_date=date(2026, 7, 3), run_id="r3")
    # Backdate BOTH of the user's rows (the inserts defaulted to now()): the answer must be
    # the NEWER of the user's two (MAX), and must NOT be the other user's still-fresh row.
    now = datetime.now(timezone.utc)
    t_older, t_newer = now - timedelta(days=30), now - timedelta(days=20)
    for run_date, when in ((date(2026, 7, 1), t_older), (date(2026, 7, 2), t_newer)):
        with repo.engine.begin() as conn:
            conn.execute(
                update(tables.run_log)
                .where((tables.run_log.c.user_id == user)
                       & (tables.run_log.c.run_date == run_date))
                .values(digest_sent_at=when)
            )

    got = repo.get_last_digest_sent_at(user_id=user)
    assert got is not None
    assert abs((got - t_newer).total_seconds()) < 1  # MAX of the user's rows...
    assert (now - got).total_seconds() > timedelta(days=19).total_seconds()  # ...not other's
