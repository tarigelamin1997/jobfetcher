"""VG4 (the Step-7 headline): the single Lambda `handler` end-to-end on a REAL local Postgres
(`$JOBFETCHER_DB_URL` / a throwaway container) + moto S3 + moto SES + a `FakeLlm` for both
dissect and score + a fake JSearch source returning sample postings. The search spec + profile
are provided via env-path temp YAMLs (the handler reads $SEARCH_CONFIG_PATH / $PROFILE_PATH).

Asserts the positive path (bronze/posting/cluster/score rows + EXACTLY ONE email + the status
fields), then the VG4 idempotency property: re-invoking for the SAME run_date produces identical
DB state (no duplicate rows) and STILL exactly one email — the `run_log` send-once guard. SKIPS
CLEANLY without the DB or moto."""
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from jobfetcher.core.models import DissectedPosting, Skill  # noqa: F401 (kept parallel to siblings)

pytestmark = pytest.mark.integration

moto = pytest.importorskip("moto", reason="moto not installed (dev extra)")
from moto import mock_aws  # noqa: E402

RUN_DATE = date(2026, 6, 28)


# --------------------------------------------------------------------------- DB fixtures
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


def _truncate(repo) -> None:
    from sqlalchemy import text as _text

    with repo.engine.begin() as conn:
        conn.execute(_text(
            "TRUNCATE score, posting, cluster, bronze_posting, run_log, profile "
            "RESTART IDENTITY CASCADE"
        ))


def _count(repo, table: str) -> int:
    from sqlalchemy import text as _text

    with repo.engine.connect() as conn:
        return conn.execute(_text(f"SELECT count(*) FROM {table}")).scalar_one()


# --------------------------------------------------------------------------- fakes
class _FakeSource:
    """A `SourceAdapter` yielding two sample postings (Data Engineer in Riyadh matches the
    spec/profile targeting; the gold filter keeps it)."""

    def fetch(self, spec, *, run_id):  # noqa: ARG002
        yield {
            "job_id": "j1",
            "job_title": "Senior Data Engineer",
            "job_description": "Required: Python and SQL. Build ETL pipelines on AWS.",
            "job_city": "Riyadh",
            "job_country": "sa",
            "job_location": "Riyadh, SA",
            "employer_name": "Acme",
            "job_apply_link": "https://jobs.test/apply/j1",
        }
        yield {
            "job_id": "j2",
            "job_title": "Data Engineer",
            "job_description": "Required: Python, SQL, Spark. Data platform work in Riyadh.",
            "job_city": "Riyadh",
            "job_country": "sa",
            "job_location": "Riyadh, SA",
            "employer_name": "Beta",
            "job_apply_link": "https://jobs.test/apply/j2",
        }


class _FakeNotifier:
    """A `Notifier` that raises on its first `fail_first` sends, then succeeds. Used to force a
    send-side failure mid-run (VG4 resumability): the handler must return 500, leave `run_log`
    unmarked, and send no email — so a retry re-sends exactly once. `sent` counts real sends."""

    def __init__(self, *, fail_first: int = 1) -> None:
        from jobfetcher.core.ports import NotifierError

        self._NotifierError = NotifierError
        self._remaining_failures = fail_first
        self.sent = 0

    def send(self, *, subject, html_body, text_body, recipients):  # noqa: ARG002
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise self._NotifierError("injected send failure")
        self.sent += 1
        return "fake-message-id"


class _FakeLlm:
    """Scripted LLM: a valid dissection reply for the flash model, a valid score reply for the
    pro model. The handler builds two clients (by config.model), so we return per-model."""

    def __init__(self, model: str) -> None:
        self.config = type("C", (), {"model": model})()
        self._model = model

    def complete(self, *, system: str, user: str) -> str:  # noqa: ARG002
        if "score" in system.lower() or self._model.endswith("pro"):
            return json.dumps({
                "score": 90, "strengths": ["python", "sql"], "gaps": ["spark"],
                "strategic_assessment": "strong pipeline fit", "poster_type": "direct employer",
                "legitimacy_verified": True,
            })
        return json.dumps({
            "skills": [{"name": "Python", "level": "must", "evidence": "Required: Python and SQL"}],
            "sector": "fintech", "normalized_title": "Data Engineer",
        })


# --------------------------------------------------------------------------- env / config
def _write_config(tmp_path: Path) -> tuple[str, str]:
    search = tmp_path / "search.yml"
    search.write_text(
        "source: jsearch\n"
        "secret_name: jobfetcher/jsearch\n"
        "aws_region: us-east-1\n"
        "targeting:\n"
        "  job_titles: ['Data Engineer']\n"
        "  countries: ['sa']\n"
        "  cities: []\n"
        "  states: []\n"
        "date_posted: month\n"
        "language: en\n"
        "employment_types: []\n"
        "remote: 'off'\n"
        "threshold: 60\n"
        "budget:\n"
        "  max_pages_per_query: 1\n"
        "  request_budget_per_run: 5\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yml"
    profile.write_text(
        "name: Tester\n"
        "skills:\n"
        "  - name: Python\n"
        "  - name: SQL\n"
        "preferences:\n"
        "  target_titles: ['Data Engineer']\n"
        "  target_locations: ['Riyadh']\n"
        "  avoid_keywords: []\n",
        encoding="utf-8",
    )
    return str(search), str(profile)


@pytest.fixture
def patched(monkeypatch, repo, db_url):
    """Wire the handler's adapters to the fakes + moto, with the DB pointed at the live repo's
    engine. Returns nothing — the handler builds its own adapters; we patch the symbols it uses."""
    import jobfetcher.handlers.pipeline as pipe

    # DB: reuse the test repo's engine (so assertions read the same state the handler wrote).
    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: repo)  # noqa: ARG005
    # Source + LLM clients + S3 + SES.
    monkeypatch.setattr(pipe, "JSearchSourceAdapter", lambda: _FakeSource())
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: _FakeLlm(cfg.model))

    with mock_aws():
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="jobfetcher-test-bucket")
        ses = boto3.client("ses", region_name="us-east-1")
        ses.verify_email_identity(EmailAddress="from@jobfetcher.test")

        from jobfetcher.adapters.s3_raw import S3RawStore
        from jobfetcher.adapters.ses_notifier import SesNotifier

        monkeypatch.setattr(
            pipe, "S3RawStore", lambda: S3RawStore(bucket="jobfetcher-test-bucket", client=s3)
        )
        monkeypatch.setattr(
            pipe, "SesNotifier", lambda: SesNotifier(sender="from@jobfetcher.test", client=ses)
        )
        yield


def _sent_messages():
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.ses import ses_backends

    backend = ses_backends[DEFAULT_ACCOUNT_ID]["us-east-1"]
    return [
        m for m in backend.sent_messages
        if "to@jobfetcher.test" in (getattr(m, "destinations", {}) or {}).get("ToAddresses", [])
    ]


def _invoke(tmp_path: Path) -> dict[str, Any]:
    from jobfetcher.handlers.pipeline import handler

    search_path, profile_path = _write_config(tmp_path)
    os.environ["SEARCH_CONFIG_PATH"] = search_path
    os.environ["PROFILE_PATH"] = profile_path
    os.environ["RECIPIENT_EMAIL"] = "to@jobfetcher.test"
    return handler({"run_id": uuid4().hex[:8], "run_date": RUN_DATE.isoformat()}, None)


# --------------------------------------------------------------------------- tests
def test_handler_end_to_end_then_idempotent(repo, patched, tmp_path):
    _truncate(repo)

    # --- first run: full pipeline, exactly one email ---
    out = _invoke(tmp_path)
    assert out["statusCode"] == 200
    assert out["ingest"]["silvered"] == 2
    assert out["gold"]["gold"] == 2
    assert out["score"]["scored"] == 2
    assert out["score"]["surfaced"] == 2  # both score 90 >= 60
    assert out["notify"]["sent"] == 1

    bronze1, posting1, cluster1, score1 = (
        _count(repo, "bronze_posting"), _count(repo, "posting"),
        _count(repo, "cluster"), _count(repo, "score"),
    )
    assert bronze1 == 2 and posting1 == 2 and cluster1 == 2 and score1 == 2
    assert len(_sent_messages()) == 1  # exactly one email
    assert _count(repo, "run_log") == 1  # send guard recorded

    # --- second run, SAME run_date: identical DB state, STILL one email (VG4) ---
    out2 = _invoke(tmp_path)
    assert out2["statusCode"] == 200
    assert out2["notify"]["sent"] == 0  # the run_log guard skipped the send

    assert _count(repo, "bronze_posting") == bronze1
    assert _count(repo, "posting") == posting1
    assert _count(repo, "cluster") == cluster1
    assert _count(repo, "score") == score1
    assert _count(repo, "run_log") == 1
    assert len(_sent_messages()) == 1  # STILL exactly one email — no duplicate


def _moto_notifier_factory():
    """Rebuild the moto-backed SesNotifier the `patched` fixture installs (same bucket/client),
    so a healthy re-invoke after an injected failure actually delivers through moto SES."""
    import boto3

    from jobfetcher.adapters.ses_notifier import SesNotifier

    ses = boto3.client("ses", region_name="us-east-1")
    return SesNotifier(sender="from@jobfetcher.test", client=ses)


def test_handler_crash_mid_run_then_resume_sends_once(repo, patched, tmp_path, monkeypatch):
    """VG4 resumability: a failure AT the notify stage on the first run must not corrupt state —
    ingest/gold/score rows exist, `run_log` is NOT marked, NO email goes out, and the handler
    returns 500. A healthy re-invoke for the same run_date then sends EXACTLY ONE email, marks
    `run_log`, and leaves identical DB row counts (the upserts make the resume idempotent)."""
    import jobfetcher.handlers.pipeline as pipe

    _truncate(repo)

    # --- first run: the notify stage raises (always-fail notifier) ---
    monkeypatch.setattr(pipe, "SesNotifier", lambda: _FakeNotifier(fail_first=1))
    out = _invoke(tmp_path)
    assert out["statusCode"] == 500
    assert out["error"].startswith("NotifierError")

    # the work BEFORE the failed send is committed (no corruption) ...
    bronze1, posting1, cluster1, score1 = (
        _count(repo, "bronze_posting"), _count(repo, "posting"),
        _count(repo, "cluster"), _count(repo, "score"),
    )
    assert bronze1 == 2 and posting1 == 2 and cluster1 == 2 and score1 == 2
    # ... but the send never happened: no email, no run_log mark (the guard is unwritten).
    assert len(_sent_messages()) == 0
    assert _count(repo, "run_log") == 0

    # --- re-invoke healthy: resumes, sends exactly once, marks run_log, no duplicate rows ---
    monkeypatch.setattr(pipe, "SesNotifier", _moto_notifier_factory)
    out2 = _invoke(tmp_path)
    assert out2["statusCode"] == 200
    assert out2["notify"]["sent"] == 1

    assert _count(repo, "bronze_posting") == bronze1
    assert _count(repo, "posting") == posting1
    assert _count(repo, "cluster") == cluster1
    assert _count(repo, "score") == score1
    assert len(_sent_messages()) == 1  # exactly one email across both invocations
    assert _count(repo, "run_log") == 1  # send guard now recorded


def test_handler_send_failure_not_double_marked_then_retry_sends_once(
    repo, patched, tmp_path, monkeypatch
):
    """A failed send must be RETRIED, not silently guarded: when the notifier raises on send,
    `run_log` stays unwritten and 0 emails go out (the mark happens only after a successful
    send). A retry with a working notifier then sends EXACTLY ONE email and marks `run_log` —
    proving the send-once guard never swallows a failure."""
    import jobfetcher.handlers.pipeline as pipe

    _truncate(repo)

    # --- first run: send raises NotifierError → 500, nothing marked, no email ---
    monkeypatch.setattr(pipe, "SesNotifier", lambda: _FakeNotifier(fail_first=1))
    out = _invoke(tmp_path)
    assert out["statusCode"] == 500
    assert out["error"].startswith("NotifierError")
    assert _count(repo, "run_log") == 0  # NOT marked — a failed send is not a sent digest
    assert len(_sent_messages()) == 0

    # --- retry with a working notifier: exactly one email, run_log now marked ---
    monkeypatch.setattr(pipe, "SesNotifier", _moto_notifier_factory)
    out2 = _invoke(tmp_path)
    assert out2["statusCode"] == 200
    assert out2["notify"]["sent"] == 1
    assert len(_sent_messages()) == 1  # the retry sent once — not guarded away
    assert _count(repo, "run_log") == 1
