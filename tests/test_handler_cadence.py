"""Handler-level gate for the JSearch fetch cadence (ERR-017 / INV-003), and for the two env
knobs the handler resolves for it.

**Why this file exists.** `tests/test_ingest.py` proves `ingest()` skips the sweep on a non-fetch
day. Nothing proved the *handler* ever gave it a real `run_date` — the only handler-level tests
(`test_db_resume.py`) stub `ingest` out entirely, and the integration suite pins
`JOBFETCHER_FETCH_EVERY_N_DAYS=1`, so the skip branch was never taken there either. A fresh
Examiner demonstrated the consequence by mutation: **delete the cadence wiring and ruff, all 499
unit tests and all 67 integration tests stay green** — i.e. the pipeline silently returns to
sweeping daily, ~450 requests against a 200/month quota, which is ERR-017 verbatim.

So these tests run the REAL `ingest` (gold/score are stubbed; notify is skipped) against a source
that raises if touched. The assertion is behavioral — *were requests made?* — not "was a keyword
argument passed".
"""
from __future__ import annotations

import logging

import pytest

from tests.test_db_resume import _PROFILE_YML, _SEARCH_YML


@pytest.fixture
def pkg_logger_restored():
    """Restore the `jobfetcher` package logger level after a real-handler invoke — the handler's
    `configure_log_level` sets it, and this repo was already bitten once by logger-state order
    dependence. (A local copy: the original lives in `test_db_resume.py`, and pytest fixtures are
    module-scoped unless they sit in a conftest.)"""
    logger = logging.getLogger("jobfetcher")
    before = logger.level
    yield
    logger.setLevel(before)

# `date(2026, 9, 5).toordinal() % 3 == 1` → a non-fetch day; 2026-09-07 → `% 3 == 0` → a fetch day.
NON_FETCH_DAY = "2026-09-05"
FETCH_DAY = "2026-09-07"


class _ExplodingSource:
    """Any request at all is a test failure — this is the whole point of a non-fetch day."""

    def fetch(self, spec, *, run_id):  # noqa: ARG002
        raise AssertionError("the JSearch sweep ran on a day it must not — quota is being spent")


class _CountingSource:
    def __init__(self) -> None:
        self.sweeps = 0
        self.last_stop_reason = None
        self.last_failed_queries = 0

    def fetch(self, spec, *, run_id):  # noqa: ARG002
        self.sweeps += 1
        return iter(())


def _wire(monkeypatch, tmp_path, source):
    """Stub everything except the cadence decision itself. `ingest` runs for real."""
    import jobfetcher.handlers.pipeline as pipe

    class _FakeRepo:
        engine = object()

        def upsert_profile(self, **kw):  # noqa: ARG002
            pass

        def get_profile(self, user_id):  # noqa: ARG002
            from jobfetcher.core.profile import Profile

            return {"profile": Profile.from_yaml_text(_PROFILE_YML).model_dump()}

        def was_digest_sent(self, **kw):  # noqa: ARG002
            return True  # notify is not under test here

        def get_last_digest_sent_at(self, **kw):  # noqa: ARG002
            return None

    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: _FakeRepo())  # noqa: ARG005
    monkeypatch.setattr(pipe, "wait_for_db_resume", lambda engine: None)  # noqa: ARG005
    monkeypatch.setattr(pipe, "JSearchSourceAdapter", lambda: source)
    monkeypatch.setattr(pipe, "S3RawStore", lambda: object())
    monkeypatch.setattr(pipe, "S3ReportStore", lambda: object())
    monkeypatch.setattr(pipe, "S3AuditStore", lambda **kw: None)  # noqa: ARG005
    monkeypatch.setattr(pipe, "SesNotifier", lambda: object())
    monkeypatch.setattr(
        pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: object()  # noqa: ARG005
    )
    # gold + score are stubbed; `ingest` is NOT — the cadence decision must run for real.
    monkeypatch.setattr(pipe, "apply_gold_filter", lambda *a, **kw: {})
    monkeypatch.setattr(pipe, "score_gold", lambda *a, **kw: {})

    search = tmp_path / "search.yml"
    search.write_text(_SEARCH_YML, encoding="utf-8")
    profile = tmp_path / "profile.yml"
    profile.write_text(_PROFILE_YML, encoding="utf-8")
    monkeypatch.setenv("SEARCH_CONFIG_PATH", str(search))
    monkeypatch.setenv("PROFILE_PATH", str(profile))
    monkeypatch.setenv("RECIPIENT_EMAIL", "to@jobfetcher.test")
    monkeypatch.setenv("JOBFETCHER_DB_URL", "postgresql://u:p@localhost:5433/jobfetcher")
    monkeypatch.delenv("GOLD_FILTER_STRATEGY", raising=False)
    monkeypatch.delenv("JOBFETCHER_FETCH_EVERY_N_DAYS", raising=False)
    return pipe


def test_handler_does_not_sweep_on_a_non_fetch_day(monkeypatch, tmp_path, pkg_logger_restored):
    # THE GATE. Not "was a kwarg passed" — was a request made. The source raises if touched.
    pipe = _wire(monkeypatch, tmp_path, _ExplodingSource())
    out = pipe.handler({"run_date": NON_FETCH_DAY}, None)
    assert out["statusCode"] == 200                       # a skip day is NOT a degraded run
    assert out["ingest"]["fetch_stopped"] == "not_a_fetch_day"
    assert out["ingest"]["fetched"] == 0


def test_handler_does_sweep_on_a_fetch_day(monkeypatch, tmp_path, pkg_logger_restored):
    # The other direction, and the half that makes the pair a gate: without it, hardcoding the
    # skip (never fetching again) would pass.
    src = _CountingSource()
    pipe = _wire(monkeypatch, tmp_path, src)
    out = pipe.handler({"run_date": FETCH_DAY}, None)
    assert out["statusCode"] == 200
    assert src.sweeps == 1
    assert out["ingest"]["fetch_stopped"] is None


def test_the_cadence_env_override_is_honoured_end_to_end(
    monkeypatch, tmp_path, pkg_logger_restored
):
    # `JOBFETCHER_FETCH_EVERY_N_DAYS=1` disables the cadence — the setting the integration suite
    # relies on so it never depends on what today's real date happens to be. Prove it here, on a
    # day that would otherwise be skipped.
    src = _CountingSource()
    pipe = _wire(monkeypatch, tmp_path, src)
    monkeypatch.setenv("JOBFETCHER_FETCH_EVERY_N_DAYS", "1")
    out = pipe.handler({"run_date": NON_FETCH_DAY}, None)
    assert out["statusCode"] == 200
    assert src.sweeps == 1, "the override was ignored — a non-fetch day stayed skipped"
    assert out["ingest"]["fetch_stopped"] is None


@pytest.mark.parametrize("junk", ["banana", "", "3.5"])
def test_a_junk_cadence_override_falls_back_instead_of_killing_the_run(
    monkeypatch, tmp_path, pkg_logger_restored, junk
):
    # negative (Examiner S3): the `except ValueError` fallback had ZERO coverage anywhere — the
    # unit suite never reached it and the integration suite sets a valid "1". A logging/config
    # typo must degrade to the documented default, never take the daily run down.
    pipe = _wire(monkeypatch, tmp_path, _ExplodingSource())
    monkeypatch.setenv("JOBFETCHER_FETCH_EVERY_N_DAYS", junk)
    out = pipe.handler({"run_date": NON_FETCH_DAY}, None)
    assert out["statusCode"] == 200                       # not a 500, not a page
    assert out["ingest"]["fetch_stopped"] == "not_a_fetch_day"  # fell back to the default 3


def test_an_absurd_cadence_override_does_not_page_the_operator(
    monkeypatch, tmp_path, pkg_logger_restored
):
    # negative (Examiner M1): `next_fetch_day` used to walk day-by-day to `date.max` and raise
    # OverflowError — and because logging evaluates its %-args eagerly, that reached the caller
    # even with logging off, turning a nonsense value into a statusCode:500 AND a PIPELINE_ALARM.
    pipe = _wire(monkeypatch, tmp_path, _ExplodingSource())
    monkeypatch.setenv("JOBFETCHER_FETCH_EVERY_N_DAYS", "4000000")
    out = pipe.handler({"run_date": NON_FETCH_DAY}, None)
    assert out["statusCode"] == 200
    assert out["ingest"]["fetch_stopped"] == "not_a_fetch_day"
