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
def _write_config(
    tmp_path: Path, *, threshold: int = 60, extra_skill: str | None = None
) -> tuple[str, str]:
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
        f"threshold: {threshold}\n"
        "hard_floor: 50\n"
        "near_miss_band: 10\n"
        "reassess_max_age_days: 45\n"
        "digest_max_age_days: 90\n"
        "budget:\n"
        "  max_pages_per_query: 1\n"
        "  request_budget_per_run: 5\n",
        encoding="utf-8",
    )
    # An added skill shifts compute_profile_hash → the reassess test uses it to model a real
    # skill gain (a DIFFERENT profile_hash), so a threshold crossing is an HONEST graduation
    # rather than same-profile LLM noise (ADR-0026 / the honest-graduation gate).
    skills = "  - name: Python\n  - name: SQL\n"
    if extra_skill:
        skills += f"  - name: {extra_skill}\n"
    profile = tmp_path / "profile.yml"
    profile.write_text(
        "name: Tester\n"
        "skills:\n"
        f"{skills}"
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

        from jobfetcher.adapters.s3_audit import S3AuditStore
        from jobfetcher.adapters.s3_raw import S3RawStore
        from jobfetcher.adapters.s3_reports import S3ReportStore
        from jobfetcher.adapters.ses_notifier import SesNotifier

        monkeypatch.setattr(
            pipe, "S3RawStore", lambda: S3RawStore(bucket="jobfetcher-test-bucket", client=s3)
        )
        monkeypatch.setattr(
            pipe, "S3ReportStore",
            lambda: S3ReportStore(bucket="jobfetcher-test-bucket", client=s3),
        )
        monkeypatch.setattr(
            pipe, "S3AuditStore",
            lambda *, run_id, run_date: S3AuditStore(
                run_id=run_id, run_date=run_date, bucket="jobfetcher-test-bucket", client=s3
            ),
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


def _invoke(tmp_path: Path, *, threshold: int = 60, run_date: date = RUN_DATE) -> dict[str, Any]:
    from jobfetcher.handlers.pipeline import handler

    search_path, profile_path = _write_config(tmp_path, threshold=threshold)
    os.environ["SEARCH_CONFIG_PATH"] = search_path
    os.environ["PROFILE_PATH"] = profile_path
    os.environ["RECIPIENT_EMAIL"] = "to@jobfetcher.test"
    return handler({"run_id": uuid4().hex[:8], "run_date": run_date.isoformat()}, None)


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


def test_handler_persists_audit_medallion_to_s3(repo, patched, tmp_path):
    """v0.12.0: every stage's structured results + the per-run summary also land in S3 — the
    full audit medallion alongside Aurora. One batched object per stage per run."""
    import boto3

    _truncate(repo)
    out = _invoke(tmp_path)
    assert out["statusCode"] == 200 and out["score"]["scored"] == 2

    s3 = boto3.client("s3", region_name="us-east-1")

    def keys(prefix: str) -> list[str]:
        resp = s3.list_objects_v2(Bucket="jobfetcher-test-bucket", Prefix=prefix)
        return [o["Key"] for o in resp.get("Contents", [])]

    def body(key: str) -> bytes:
        return s3.get_object(Bucket="jobfetcher-test-bucket", Key=key)["Body"].read()

    # each stage wrote its batched object + the run summary (mirrors the medallion in S3)
    assert keys("silver/") and keys("gold/") and keys("scores/") and keys("runs/")

    # the run summary IS the handler's returned procedure record (which lived only in logs before)
    run_summary = json.loads(body(keys("runs/")[0]))
    assert run_summary["statusCode"] == 200
    assert run_summary["score"]["scored"] == 2 and run_summary["notify"]["sent"] == 1

    # the scores object is JSONL — one line per scored posting, carrying the ScoreResult
    score_lines = body(keys("scores/")[0]).decode("utf-8").splitlines()
    assert len(score_lines) == 2
    assert all(json.loads(line)["score"] == 90 for line in score_lines)
    assert all(json.loads(line)["fit_category"] == "strong_fit" for line in score_lines)

    # the gold object records a decision per candidate (both promoted here)
    gold_lines = body(keys("gold/")[0]).decode("utf-8").splitlines()
    assert len(gold_lines) == 2 and all(json.loads(line)["promoted"] for line in gold_lines)

    # the silver object carries the dissected skills (the structured result, not just raw)
    silver_lines = body(keys("silver/")[0]).decode("utf-8").splitlines()
    assert len(silver_lines) == 2 and all("skills" in json.loads(line) for line in silver_lines)


def test_handler_audit_failure_does_not_fail_run(repo, patched, tmp_path, monkeypatch):
    """The audit trail is an enhancement — a totally broken S3 audit path must NEVER fail the
    run: the pipeline still returns 200 and the digest still sends (the non-fatal guarantee,
    end-to-end through the handler)."""
    import jobfetcher.handlers.pipeline as pipe

    from jobfetcher.adapters.s3_audit import S3AuditStore

    class _BoomS3:
        def put_object(self, **kw):  # noqa: ANN003, ANN201
            raise RuntimeError("s3 audit is down")

    monkeypatch.setattr(
        pipe, "S3AuditStore",
        lambda *, run_id, run_date: S3AuditStore(
            run_id=run_id, run_date=run_date, bucket="b", client=_BoomS3()
        ),
    )

    _truncate(repo)
    out = _invoke(tmp_path)
    assert out["statusCode"] == 200  # audit failures never fail the run
    assert out["score"]["scored"] == 2
    assert out["notify"]["sent"] == 1  # the digest still goes out
    # and the real work still landed in Aurora despite the dead audit path
    assert _count(repo, "score") == 2


def test_handler_reads_config_from_s3(repo, patched, tmp_path):
    """ADR-0022: config is read from S3 at RUNTIME (not bundled). Put the two YAMLs in the
    (moto) bucket, point the env at `s3://` URIs, and the pipeline runs from S3 — the seam that
    lets a user change settings via `push_config.py` with no rebuild/redeploy."""
    import boto3

    from jobfetcher.handlers.pipeline import handler

    _truncate(repo)
    bucket = "jobfetcher-test-bucket"  # created by the `patched` fixture (moto)
    search_path, profile_path = _write_config(tmp_path)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.put_object(Bucket=bucket, Key="config/search_config.yml",
                  Body=Path(search_path).read_text(encoding="utf-8").encode("utf-8"))
    s3.put_object(Bucket=bucket, Key="config/profile.yml",
                  Body=Path(profile_path).read_text(encoding="utf-8").encode("utf-8"))

    os.environ["SEARCH_CONFIG_PATH"] = f"s3://{bucket}/config/search_config.yml"
    os.environ["PROFILE_PATH"] = f"s3://{bucket}/config/profile.yml"
    os.environ["RECIPIENT_EMAIL"] = "to@jobfetcher.test"
    out = handler({"run_id": uuid4().hex[:8], "run_date": RUN_DATE.isoformat()}, None)

    assert out["statusCode"] == 200  # config sourced from S3 drove a full run
    assert out["ingest"]["silvered"] == 2
    assert out["notify"]["sent"] == 1


def test_handler_resyncs_settings_from_config_on_every_run(repo, patched, tmp_path):
    """The write-once fix: a user's config edit must actually take effect. Run 1 seeds the
    profile row (threshold 60 → both score-90 jobs surface). Then the user raises the bar to 95
    and re-runs: the EXISTING profile row must be RE-SYNCED to 95 (not frozen at 60), so the
    shortlist now surfaces 0. Pre-fix this was impossible — the seed-once guard left the row at
    60 forever and the edit was silently ignored."""
    from jobfetcher.core.ingest import DEFAULT_USER_ID

    _truncate(repo)

    # run 1 @ threshold 60 → row created, both jobs (score 90) surface
    out1 = _invoke(tmp_path, threshold=60)
    assert out1["statusCode"] == 200
    assert out1["notify"]["surfaced"] == 2
    row1 = repo.get_profile(DEFAULT_USER_ID)
    assert (row1["threshold"], row1["hard_floor"], row1["near_miss_band"]) == (60, 50, 10)

    # run 2 @ threshold 95, DIFFERENT run_date (so notify runs again) → row RE-SYNCED to 95,
    # and now nothing surfaces (90 < 95). The profile row already existed — the fix updates it.
    out2 = _invoke(tmp_path, threshold=95, run_date=date(2026, 6, 29))
    assert out2["statusCode"] == 200
    row2 = repo.get_profile(DEFAULT_USER_ID)
    assert row2["threshold"] == 95  # the edit took effect (pre-fix: still 60)
    assert out2["notify"]["surfaced"] == 0  # the higher bar changed what the user receives


class _LlmScoring:
    """A scripted LLM whose SCORE reply is parametrized (dissect reply fixed), so a re-run can
    return a different score — the reassess/graduation scenario."""

    def __init__(self, model: str, score: int) -> None:
        self.config = type("C", (), {"model": model})()
        self._model = model
        self._score = score

    def complete(self, *, system: str, user: str) -> str:  # noqa: ARG002
        if "score" in system.lower() or self._model.endswith("pro"):
            return json.dumps({
                "score": self._score, "strengths": ["python"], "gaps": [],
                "strategic_assessment": "x", "poster_type": "direct employer",
                "legitimacy_verified": True,
            })
        return json.dumps({
            "skills": [{"name": "Python", "level": "must", "evidence": "Required: Python and SQL"}],
            "sector": "fintech", "normalized_title": "Data Engineer",
        })


def test_handler_reassess_mode_replays_and_graduates(repo, patched, tmp_path, monkeypatch):
    """ADR-0023: `{"mode":"reassess"}` re-scores the already-scored postings against the current
    profile with NO fetch, carries the old score into `previous_score`, and GRADUATES a job that
    crosses the threshold upward — the immutable-bronze replay ("my progress changed my matches")."""
    import jobfetcher.handlers.pipeline as pipe
    from sqlalchemy import text as _text

    _truncate(repo)

    # --- initial full run @ threshold 80, jobs score 60 → scored but NOT surfaced ---
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: _LlmScoring(cfg.model, 60))
    out1 = _invoke(tmp_path, threshold=80)
    assert out1["statusCode"] == 200
    assert out1["score"]["scored"] == 2 and out1["score"]["surfaced"] == 0  # 60 < 80

    # --- reassess: the user gains a skill (the profile genuinely changes → a NEW profile_hash)
    # and the re-score now returns 90. Both 60→90 crossings are HONEST graduations — a crossing
    # is only badged when the profile actually changed, never on same-profile LLM noise (the
    # negative is covered by test_reassess_same_profile_crossing_is_not_a_graduation). The
    # source EXPLODES if fetched, proving replay never re-fetches. ---
    monkeypatch.setattr(pipe, "OpenAICompatLlmClient", lambda cfg=None, **kw: _LlmScoring(cfg.model, 90))

    class _ExplodingSource:
        def fetch(self, spec, *, run_id):  # noqa: ARG002
            raise AssertionError("reassess must not fetch!")
            yield  # pragma: no cover — makes this a generator; body never runs in reassess

    monkeypatch.setattr(pipe, "JSearchSourceAdapter", lambda: _ExplodingSource())

    # add a skill → a DIFFERENT profile_hash than run 1, so the crossing is a real skill gain
    search_path, profile_path = _write_config(tmp_path, threshold=80, extra_skill="Spark")
    os.environ["SEARCH_CONFIG_PATH"] = search_path
    os.environ["PROFILE_PATH"] = profile_path
    os.environ["RECIPIENT_EMAIL"] = "to@jobfetcher.test"
    out2 = pipe.handler({"run_id": "reassess1", "run_date": RUN_DATE.isoformat(), "mode": "reassess"}, None)

    assert out2["statusCode"] == 200 and out2["mode"] == "reassess"
    r = out2["reassess"]
    assert r["reassessed"] == 2
    assert r["graduated"] == 2 and r["downgraded"] == 0  # both 60 → 90 crossed 80, profile changed
    assert len(r["graduations"]) == 2
    assert {g["old_score"] for g in r["graduations"]} == {60}
    assert {g["new_score"] for g in r["graduations"]} == {90}

    # DB proof: the score row now holds 90 with previous_score 60 (the old value carried over)
    with repo.engine.connect() as conn:
        rows = conn.execute(_text("SELECT score, previous_score, fit_category FROM score")).all()
    assert all(row.score == 90 and row.previous_score == 60 for row in rows)
    assert all(row.fit_category == "strong_fit" for row in rows)

    # lineage proof (migration 0004): 2 first-scoring events + 2 reassess events survive the
    # upsert — the reassess did NOT erase the original judgments from the log
    with repo.engine.connect() as conn:
        events = conn.execute(_text(
            "SELECT score, previous_score, run_id, profile_hash, scoring_model "
            "FROM score_event ORDER BY event_id"
        )).all()
    assert len(events) == 4
    assert [e.score for e in events] == [60, 60, 90, 90]
    assert all(e.previous_score is None for e in events[:2])  # first scorings
    assert all(e.previous_score == 60 for e in events[2:])    # reassess carries the old
    assert all(e.run_id == "reassess1" for e in events[2:])
    assert all(e.profile_hash and e.scoring_model for e in events)
    # honest-graduation driver: the profile genuinely changed between the two runs (a skill was
    # added), so the reassess events carry a DIFFERENT profile_hash than the first scorings —
    # precisely what makes the crossings graduations rather than same-profile noise.
    assert events[0].profile_hash == events[1].profile_hash  # run 1 — profile P
    assert events[2].profile_hash == events[3].profile_hash  # reassess — profile P' (P + Spark)
    assert events[0].profile_hash != events[2].profile_hash  # P != P' → a real skill gain
    # the synced profile row carries the same hash the events were stamped with
    with repo.engine.connect() as conn:
        row_hash = conn.execute(_text("SELECT profile_hash FROM profile")).scalar_one()
    assert row_hash == events[-1].profile_hash

    # no NEW bronze/posting rows were created (replay only re-scores; no fetch)
    assert _count(repo, "bronze_posting") == 2 and _count(repo, "posting") == 2


def test_export_snapshot_from_db(repo, patched, tmp_path):
    """ADR-0024: `scripts/export.py`'s DB read + snapshot over a REAL DB. Run a pipeline to
    create scored rows, then export → the flat `jobs` table (in sqlite + csv) carries them with
    score/fit_category, and `bronze`/`runs` are populated. Proves the SQL joins match the schema."""
    import importlib.util
    import sqlite3

    _spec = importlib.util.spec_from_file_location(
        "export", Path(__file__).resolve().parents[1] / "scripts" / "export.py"
    )
    export = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(export)

    _truncate(repo)
    out = _invoke(tmp_path)  # full pipeline → 2 scored postings (score 90)
    assert out["statusCode"] == 200 and out["score"]["scored"] == 2

    data = export.read_data(repo.engine)  # the real SELECTs against the seeded schema
    assert len(data["jobs"]) == 2
    assert all(j["score"] == 90 and j["fit_category"] == "strong_fit" for j in data["jobs"])
    assert data["jobs"][0]["skills"]  # JSONB skills flattened to searchable text
    assert len(data["bronze"]) == 2 and len(data["runs"]) == 1
    assert len(data["events"]) == 2  # one lineage event per scoring (migration 0004)
    assert all(e["scoring_model"] and e["profile_hash"] for e in data["events"])

    sp, cp = export.write_snapshot(
        jobs=data["jobs"], bronze=data["bronze"], runs=data["runs"], profile=data["profile"],
        events=data["events"], application_events=data["application_events"],
        out_dir=tmp_path / "export",
    )
    con = sqlite3.connect(sp)
    assert con.execute("SELECT count(*) FROM jobs WHERE score >= 60").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM score_events").fetchone()[0] == 2
    # migration 0005: the outcome table exists in the snapshot (empty — no track.py calls here)
    assert con.execute("SELECT count(*) FROM application_events").fetchone()[0] == 0
    # count CSV rows via the parser (fields like strengths carry embedded newlines)
    import csv as _csv
    with cp.open(encoding="utf-8", newline="") as f:
        csv_rows = list(_csv.reader(f))
    assert len(csv_rows) == 3  # header + 2 jobs


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


# --------------------------------------------------------------------------- smoke mode (Run 5)
# The post-deploy gate: {"mode":"smoke"} must prove DB reachability + migration head with ZERO
# side effects — no S3 config read, no LLM/source/notifier construction, no row written anywhere.

_ALL_TABLES = (
    "bronze_posting", "posting", "cluster", "score",
    "score_event", "application_event", "run_log", "profile",
)


def _table_counts(repo) -> dict[str, int]:
    return {t: _count(repo, t) for t in _ALL_TABLES}


class _ExplodingSymbol:
    """A stand-in for every adapter/config symbol smoke mode must NEVER touch: constructing or
    calling it IS the failure (the exploding-fake pattern from the reassess test above)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"smoke mode must not touch {self._name}!")


@pytest.fixture
def smoke_guard(monkeypatch, repo):
    """Wire the handler for smoke tests: the repo engine is REAL (migrated to head by the
    module fixture); everything else the normal pipeline would read or build explodes on touch."""
    import jobfetcher.handlers.pipeline as pipe

    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: repo)  # noqa: ARG005
    for symbol in (
        "JSearchSourceAdapter", "OpenAICompatLlmClient", "S3RawStore", "SesNotifier",
        "read_config_text", "resolve_filter_strategy",
    ):
        monkeypatch.setattr(pipe, symbol, _ExplodingSymbol(symbol))
    monkeypatch.delenv("ALEMBIC_HEAD", raising=False)


def test_handler_smoke_mode_matching_head_200_and_writes_nothing(repo, smoke_guard):
    """Positive path: migrated DB + matching expected head → 200 with the version echoed —
    and NOT ONE row written in ANY table (the whole point of a smoke gate). The exploding
    fakes prove no LLM/source/notifier/S3-config access happened either."""
    from jobfetcher.handlers.pipeline import handler, resolve_expected_migration_head

    _truncate(repo)
    before = _table_counts(repo)

    out = handler({"run_id": "smoke1", "mode": "smoke"}, None)

    assert out["statusCode"] == 200
    assert out["mode"] == "smoke"
    assert out["run_id"] == "smoke1"
    # the DB is migrated to head by the fixture, so the echoed version IS the code's expectation
    assert out["alembic_version"] == resolve_expected_migration_head({})
    assert _table_counts(repo) == before  # zero writes anywhere


def test_handler_smoke_mode_mismatched_head_400_and_writes_nothing(repo, smoke_guard, monkeypatch):
    """Negative: $ALEMBIC_HEAD points at a migration the DB doesn't have → 400 with a loud
    'mismatch' error naming both versions, and still zero writes — the gate reports, never
    repairs."""
    from jobfetcher.handlers.pipeline import handler

    _truncate(repo)
    before = _table_counts(repo)
    monkeypatch.setenv("ALEMBIC_HEAD", "9999_not_yet_migrated")

    out = handler({"run_id": "smoke2", "mode": "smoke"}, None)

    assert out["statusCode"] == 400
    assert out["mode"] == "smoke"
    assert "mismatch" in out["error"]
    assert "9999_not_yet_migrated" in out["error"]  # the expected side is named
    assert _table_counts(repo) == before  # zero writes anywhere


def test_handler_smoke_mode_connection_failure_returns_500(smoke_guard, monkeypatch):
    """Negative: an unreachable DB (engine.connect raises) surfaces through the handler's
    standard outer except as a retryable 500 — the smoke branch lives INSIDE the try."""
    import jobfetcher.handlers.pipeline as pipe

    class _DeadEngine:
        def connect(self):
            raise RuntimeError("injected: DB unreachable")

    class _DeadRepo:
        engine = _DeadEngine()

    monkeypatch.setattr(pipe, "PostgresRepository", lambda url: _DeadRepo())  # noqa: ARG005

    out = pipe.handler({"run_id": "smoke3", "mode": "smoke"}, None)

    assert out["statusCode"] == 500
    assert "DB unreachable" in out["error"]


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
