"""ERR-009 unit tests: `wait_for_db_resume` absorbs ONLY Aurora Serverless v2's scale-to-zero
resume signal (name-matched — botocore generates the class dynamically), re-raises everything
else immediately, and gives up loudly when the budget is spent. Plus the handler wiring: the
wait runs BEFORE the first real DB touch (`upsert_profile`) in every mode, and a wait failure
still surfaces as the loud 500. All fakes, no DB, `time.sleep` patched — the suite stays fast."""
from __future__ import annotations

import pytest
from sqlalchemy.exc import StatementError

import jobfetcher.db.engine as engine_mod
from jobfetcher.db.engine import wait_for_db_resume


# --------------------------------------------------------------------------- fakes
def _resume_error(message: str = "resuming after being auto-paused. Please wait a few seconds"):
    """A stand-in for botocore's DYNAMICALLY GENERATED exception class: same class NAME,
    unimportable origin — exactly what the name-based matcher must catch."""
    exc_type = type("DatabaseResumingException", (Exception,), {})
    return exc_type(message)


class _OkConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, stmt):  # noqa: ARG002
        return 1


class _FakeEngine:
    """Scripted engine: `connect()` raises the queued exceptions in order, then succeeds.
    `repeat_last=True` re-raises the final exception forever (the never-resuming cluster)."""

    def __init__(self, *failures: BaseException, repeat_last: bool = False) -> None:
        self.failures = list(failures)
        self.repeat_last = repeat_last
        self.attempts = 0

    def connect(self):
        self.attempts += 1
        if self.failures:
            exc = self.failures[0] if self.repeat_last else self.failures.pop(0)
            raise exc
        return _OkConn()


@pytest.fixture
def no_sleep(monkeypatch) -> list[float]:
    """Neutralize the wait; return the list of requested sleep durations."""
    delays: list[float] = []
    monkeypatch.setattr(engine_mod.time, "sleep", delays.append)
    return delays


# --------------------------------------------------------------------------- the helper
def test_retries_resume_failures_then_succeeds(no_sleep, caplog):
    engine = _FakeEngine(_resume_error(), _resume_error(), _resume_error())
    with caplog.at_level("INFO", logger="jobfetcher.db.engine"):
        wait_for_db_resume(engine)  # returns None — the wait absorbed the resume window
    assert engine.attempts == 4  # 3 resume failures + the succeeding attempt
    assert no_sleep == [5.0, 5.0, 5.0]  # default interval, one sleep per failure
    # each wait is logged (CloudWatch visibility for cold starts)
    assert sum("Aurora resuming" in r.getMessage() for r in caplog.records) == 3


def test_non_resume_exception_reraises_immediately(no_sleep):
    # negative: a REAL failure must never be absorbed as "still resuming" — zero sleeps
    engine = _FakeEngine(RuntimeError("connection refused"))
    with pytest.raises(RuntimeError, match="connection refused"):
        wait_for_db_resume(engine)
    assert engine.attempts == 1
    assert no_sleep == []


def test_sqlalchemy_wrapped_non_resume_reraises_immediately(no_sleep):
    # negative twin: a StatementError wrapping an ORDINARY driver error is a real failure too
    wrapped = StatementError("boom", "SELECT 1", {}, ValueError("relation does not exist"))
    engine = _FakeEngine(wrapped)
    with pytest.raises(StatementError):
        wait_for_db_resume(engine)
    assert engine.attempts == 1
    assert no_sleep == []


def test_budget_exhaustion_reraises_the_resume_error(no_sleep):
    # a cluster that never resumes: ~budget/interval sleeps, then the resume error surfaces
    engine = _FakeEngine(_resume_error(), repeat_last=True)
    with pytest.raises(Exception, match="auto-paused"):
        wait_for_db_resume(engine, budget_s=30.0, interval_s=5.0)
    assert no_sleep == [5.0] * 6  # exactly budget/interval waits
    assert engine.attempts == 7  # the initial try + one per wait


def test_statement_error_orig_chain_is_matched(no_sleep):
    # the LIVE shape (ERR-009): SQLAlchemy StatementError carrying the botocore exception on
    # `.orig`. The resume message is deliberately ABSENT so only the name-walk can match.
    wrapped = StatementError("(botocore.errorfactory) see orig", "SELECT 1", {},
                             _resume_error(message="please hold"))
    engine = _FakeEngine(wrapped)
    wait_for_db_resume(engine)
    assert engine.attempts == 2  # one absorbed resume failure, then success
    assert no_sleep == [5.0]


def test_dunder_cause_chain_is_matched(no_sleep):
    # a `raise … from resume` chain (no `.orig`) must also be walked to the root cause
    try:
        raise RuntimeError("query failed") from _resume_error(message="please hold")
    except RuntimeError as e:
        chained = e
    engine = _FakeEngine(chained)
    wait_for_db_resume(engine)
    assert engine.attempts == 2
    assert no_sleep == [5.0]


def test_message_substring_is_the_belt(no_sleep):
    # belt: a wrapper that FLATTENS the chain into a string (different class, no cause) still
    # counts as the resume signal via the documented message substring
    flat = RuntimeError("DB is resuming after being auto-paused. Please wait a few seconds")
    engine = _FakeEngine(flat)
    wait_for_db_resume(engine)
    assert engine.attempts == 2
    assert no_sleep == [5.0]


# --------------------------------------------------------------------------- handler wiring
_SEARCH_YML = (
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
    "hard_floor: 50\n"
    "near_miss_band: 10\n"
    "reassess_max_age_days: 45\n"
    "digest_max_age_days: 90\n"
    "budget:\n"
    "  max_pages_per_query: 1\n"
    "  request_budget_per_run: 5\n"
)
_PROFILE_YML = (
    "name: Tester\n"
    "skills:\n"
    "  - name: Python\n"
    "preferences:\n"
    "  target_titles: ['Data Engineer']\n"
    "  target_locations: ['Riyadh']\n"
    "  avoid_keywords: []\n"
)


def _wire_handler(monkeypatch, tmp_path, calls: list[str], *, wait=None):
    """Stub every adapter + stage the handler builds, recording the DB-relevant call order.
    Returns the pipeline module. No DB, no AWS, no network."""
    import jobfetcher.handlers.pipeline as pipe
    from jobfetcher.core.profile import Profile

    profile_dump = Profile.from_yaml_text(_PROFILE_YML).model_dump()

    class _FakeRepo:
        engine = object()  # the sentinel wait_for_db_resume must receive

        def upsert_profile(self, **kw):  # noqa: ARG002
            calls.append("upsert_profile")

        def get_profile(self, user_id):  # noqa: ARG002
            return {"profile": profile_dump}

        def was_digest_sent(self, **kw):  # noqa: ARG002
            return True  # notify is skipped — not under test here

    fake_repo = _FakeRepo()

    def _fake_wait(engine):
        assert engine is fake_repo.engine  # the wait probes the SAME engine the run will use
        calls.append("wait_for_db_resume")
        if wait is not None:
            wait()

    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: fake_repo)  # noqa: ARG005
    monkeypatch.setattr(pipe, "wait_for_db_resume", _fake_wait)
    monkeypatch.setattr(pipe, "JSearchSourceAdapter", lambda: object())
    monkeypatch.setattr(pipe, "S3RawStore", lambda: object())
    monkeypatch.setattr(pipe, "SesNotifier", lambda: object())
    monkeypatch.setattr(
        pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: object()  # noqa: ARG005
    )
    monkeypatch.setattr(pipe, "ingest", lambda *a, **kw: calls.append("ingest") or {})
    monkeypatch.setattr(pipe, "apply_gold_filter", lambda *a, **kw: calls.append("gold") or {})
    monkeypatch.setattr(pipe, "score_gold", lambda *a, **kw: calls.append("score") or {})
    monkeypatch.setattr(pipe, "reassess", lambda *a, **kw: calls.append("reassess") or {})

    search = tmp_path / "search.yml"
    search.write_text(_SEARCH_YML, encoding="utf-8")
    profile = tmp_path / "profile.yml"
    profile.write_text(_PROFILE_YML, encoding="utf-8")
    monkeypatch.setenv("SEARCH_CONFIG_PATH", str(search))
    monkeypatch.setenv("PROFILE_PATH", str(profile))
    monkeypatch.setenv("RECIPIENT_EMAIL", "to@jobfetcher.test")
    # resolve_db_url runs before the (faked) repo constructor — it must resolve, not raise
    monkeypatch.setenv("JOBFETCHER_DB_URL", "postgresql://u:p@localhost:5433/jobfetcher")
    monkeypatch.delenv("GOLD_FILTER_STRATEGY", raising=False)
    return pipe


# Every dispatch shape the handler has at this base: the normal pipeline (event None and an
# explicit empty mode) + reassess. The wait sits before the mode split, so ANY future mode
# inherits the protection — these pin that placement behaviorally.
@pytest.mark.parametrize(
    ("event", "expect_stage"),
    [
        (None, "ingest"),  # normal pipeline, bare cron event
        ({"mode": ""}, "ingest"),  # normal pipeline, explicit empty mode
        ({"mode": "reassess"}, "reassess"),  # ADR-0023 replay
    ],
)
def test_handler_waits_for_resume_before_any_db_touch(monkeypatch, tmp_path, event, expect_stage):
    calls: list[str] = []
    pipe = _wire_handler(monkeypatch, tmp_path, calls)
    out = pipe.handler(event, None)
    assert out["statusCode"] == 200
    # the resume wait is the FIRST DB-touching call — strictly before the profile sync
    assert calls[0] == "wait_for_db_resume"
    assert calls.index("wait_for_db_resume") < calls.index("upsert_profile")
    assert expect_stage in calls


def test_handler_wait_failure_is_still_a_loud_500(monkeypatch, tmp_path):
    # negative: budget exhausted (the cluster never resumed) → the run dies BEFORE any DB
    # write, loudly, with the resume error named in the 500 — never a silent skip
    calls: list[str] = []

    def _exhausted():
        raise _resume_error()

    pipe = _wire_handler(monkeypatch, tmp_path, calls, wait=_exhausted)
    out = pipe.handler({}, None)
    assert out["statusCode"] == 500
    assert "DatabaseResumingException" in out["error"]
    assert "upsert_profile" not in calls  # nothing touched the DB after the failed wait
