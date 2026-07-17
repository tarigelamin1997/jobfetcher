"""The single v0 pipeline Lambda (build-plan Step 7).

ONE handler wires the whole run — fetch→bronze→silver→gold→score→notify — over the injected
ports, reusing the Step-4..6 orchestrators in `core.ingest` (it owns no I/O of its own; every
side effect goes through an adapter, so the same handler runs against live AWS and against the
moto+local-Postgres integration test).

**Idempotent per run-date (VG4).** fetch/silver/gold/score are already idempotent via their own
upserts (a re-run skips done work, never duplicates rows). The one side effect that has no
natural dedup key is the *email*, so a small `run_log` table guards it: notify runs ONLY when
`was_digest_sent(user, run_date)` is false, and `mark_digest_sent` is written after a successful
send. Two runs for the same date → identical DB state + at most one email.

**Resumable on failure.** A stage exception propagates out of the pipeline body; the handler
logs it (with `run_id`) and returns a `500` summary so EventBridge / the next invocation retries.
Because every prior stage is idempotent and the `run_log` blocks a double email, a re-run
*resumes* — it re-does only what didn't complete and never re-sends.

Config (the search spec, the profile, and all the AWS resource names/ARNs) reaches the handler
as **paths + env vars** only — never values in code. How those config files land in the Lambda
package is a Step-10 deploy concern; the handler just reads a path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

from ..adapters.filter_deterministic import DeterministicFilterStrategy
from ..adapters.jsearch_source import JSearchSourceAdapter
from ..adapters.llm_openai import OpenAICompatLlmClient
from ..adapters.repository_postgres import PostgresRepository
from ..adapters.s3_audit import S3AuditStore
from ..adapters.s3_config import read_config_text
from ..adapters.s3_raw import S3RawStore
from ..adapters.s3_reports import S3ReportStore
from ..adapters.ses_notifier import SesNotifier
from ..config import LlmConfig
from ..core.dissector import Dissector
from ..core.ingest import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_USER_ID,
    Deadline,
    apply_gold_filter,
    ingest,
    notify,
    reassess,
    score_gold,
)
from ..core.profile import Profile
from ..core.scorer import Scorer
from ..core.search_spec import SearchSpec
from ..db.engine import wait_for_db_resume

log = logging.getLogger(__name__)

# Per-task models (ADR-0012): cheap flash for high-volume dissection, the stronger pro for the
# scarcer scoring calls. Both are still config (LlmConfig.model) — never hardcoded in a stage.
_DISSECT_MODEL = "deepseek-v4-flash"
_SCORE_MODEL = "deepseek-v4-pro"

_DEFAULT_SEARCH_CONFIG_PATH = "config/search_config.local.yml"
_DEFAULT_PROFILE_PATH = "config/profile.local.yml"

# Env vars the handler reads (config paths + the DB resolution + the recipient).
_SEARCH_CONFIG_ENV = "SEARCH_CONFIG_PATH"
_PROFILE_ENV = "PROFILE_PATH"
_DB_URL_ENV = "JOBFETCHER_DB_URL"
_DB_CLUSTER_ARN_ENV = "DB_CLUSTER_ARN"
_DB_SECRET_ARN_ENV = "DB_SECRET_ARN"
_DB_NAME_ENV = "DB_NAME"
_RECIPIENT_ENV = "RECIPIENT_EMAIL"
_MAX_WORKERS_ENV = "PIPELINE_MAX_WORKERS"
_GOLD_FILTER_ENV = "GOLD_FILTER_STRATEGY"
_LOG_LEVEL_ENV = "LOG_LEVEL"
_ALEMBIC_HEAD_ENV = "ALEMBIC_HEAD"

# The alembic head THIS code was written against — the smoke gate's fallback expectation when
# $ALEMBIC_HEAD is unset. Update per migration (terraform/lambda.tf pins the deployed value);
# the unit test cross-checks it against migrations/versions/ so it can't silently go stale.
_EXPECTED_MIGRATION_HEAD = "0006_subscores"

# Seconds reserved before the Lambda's hard timeout: in-flight LLM calls + the tail of DB
# writes + notify must finish inside this margin (H-2 deadline guard).
_DEADLINE_MARGIN_S = 60.0


# --------------------------------------------------------------------------- pure helpers
def configure_log_level(env: dict[str, str]) -> str:
    """Set the `jobfetcher` package logger's level from `$LOG_LEVEL` (default INFO); return
    the applied level name.

    Why (ERR-009's detection follow-up): AWS Lambda's Python runtime pre-attaches a handler to
    the ROOT logger, so package records already propagate to CloudWatch — only the LEVEL gates
    them. With no level ever set in the package, the effective level resolved to the root's
    WARNING and every `log.info(...)` was dropped at the originating logger (verified live:
    771 reassess events written, zero matching INFO lines in the log group). Setting the
    package logger's level is sufficient: a record propagated to ancestors is re-gated only by
    HANDLER levels (Lambda's root handler is NOTSET), never by ancestor LOGGER levels.

    A junk value falls back to INFO with a WARNING (visible even under the broken pre-fix
    config) — the one knob where fail-loud is wrong: a logging typo must never kill the run.
    """
    raw = (env.get(_LOG_LEVEL_ENV) or "").strip().upper() or "INFO"
    level = logging.getLevelName(raw)  # a known name → its int; junk → a "Level X" string
    if not isinstance(level, int):
        log.warning("invalid $%s %r — falling back to INFO", _LOG_LEVEL_ENV, raw)
        raw, level = "INFO", logging.INFO
    logging.getLogger(__name__.split(".")[0]).setLevel(level)
    return raw


def resolve_db_url(env: dict[str, str]) -> str:
    """Resolve the SQLAlchemy connection URL from the environment (a pure function — unit-tested).

    `$JOBFETCHER_DB_URL` wins when set (local Postgres for dev/tests). Otherwise the Aurora Data
    API URL is assembled from `$DB_CLUSTER_ARN` / `$DB_SECRET_ARN` / `$DB_NAME` (ADR-0018) — the
    dialect carries the ARNs as query params and uses the ambient IAM identity (no secret value in
    code). Raises `ValueError` if neither path is fully configured (a clear misconfig, never a
    silent default)."""
    explicit = env.get(_DB_URL_ENV)
    if explicit and explicit.strip():
        return explicit.strip()

    cluster_arn = (env.get(_DB_CLUSTER_ARN_ENV) or "").strip()
    secret_arn = (env.get(_DB_SECRET_ARN_ENV) or "").strip()
    db_name = (env.get(_DB_NAME_ENV) or "").strip()
    if not (cluster_arn and secret_arn and db_name):
        raise ValueError(
            "no DB connection configured — set $JOBFETCHER_DB_URL (local) or all of "
            f"${_DB_CLUSTER_ARN_ENV}/${_DB_SECRET_ARN_ENV}/${_DB_NAME_ENV} (Aurora Data API)"
        )
    # The sqlalchemy-aurora-data-api dialect maps query params straight to its `connect()` kwargs,
    # whose name is `aurora_cluster_arn` (not `cluster_arn`) — verified live in the Step-10 deploy.
    params = urllib.parse.urlencode({"aurora_cluster_arn": cluster_arn, "secret_arn": secret_arn})
    return f"postgresql+auroradataapi://:@/{db_name}?{params}"


def resolve_run_id(event: dict[str, Any] | None) -> str:
    """The correlation id for the run: `event['run_id']` when provided (manual re-trigger of a
    specific run), else a fresh short uuid. Pure."""
    if event and isinstance(event.get("run_id"), str) and event["run_id"].strip():
        return event["run_id"].strip()
    return uuid.uuid4().hex[:8]


def resolve_mode(event: dict[str, Any] | None) -> str:
    """The run mode (ADR-0023): `event['mode']` lowercased, else `""` (the normal
    fetch→gold→score→notify pipeline). `"reassess"` = replay scoring over existing scored
    postings against the current profile, no fetch. `"smoke"` = the post-deploy gate: DB
    reachability + migration-head check only, zero side effects. Pure."""
    if event and isinstance(event.get("mode"), str):
        return event["mode"].strip().lower()
    return ""


def resolve_expected_migration_head(env: dict[str, str]) -> str:
    """The alembic head the deployed code EXPECTS the database to be at: `$ALEMBIC_HEAD`
    (pinned by terraform/lambda.tf, updated per migration) or the hardcoded head this code
    was built against. The `{"mode":"smoke"}` gate compares the live `alembic_version` to
    this — a deploy against an unmigrated DB fails loudly instead of at the first write. Pure."""
    return (env.get(_ALEMBIC_HEAD_ENV) or "").strip() or _EXPECTED_MIGRATION_HEAD


def resolve_run_date(event: dict[str, Any] | None) -> date:
    """The run date (the VG4 idempotency key): `event['run_date']` (ISO `YYYY-MM-DD`) when
    provided, else today in UTC. Pure. Raises `ValueError` on a malformed override."""
    if event and isinstance(event.get("run_date"), str) and event["run_date"].strip():
        return date.fromisoformat(event["run_date"].strip())
    return datetime.now(timezone.utc).date()


def resolve_search_config_path(env: dict[str, str]) -> str:
    """`$SEARCH_CONFIG_PATH` or the documented local default. Pure."""
    return env.get(_SEARCH_CONFIG_ENV) or _DEFAULT_SEARCH_CONFIG_PATH


def resolve_profile_path(env: dict[str, str]) -> str:
    """`$PROFILE_PATH` or the documented local default. Pure."""
    return env.get(_PROFILE_ENV) or _DEFAULT_PROFILE_PATH


def resolve_max_workers(env: dict[str, str]) -> int:
    """`$PIPELINE_MAX_WORKERS` (H-2 concurrency knob) or the documented default. Pure.
    Raises `ValueError` on a non-integer or a value < 1 — a clear misconfig, never a
    silent fallback."""
    raw = (env.get(_MAX_WORKERS_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_WORKERS
    workers = int(raw)  # ValueError on junk, deliberately
    if workers < 1:
        raise ValueError(f"${_MAX_WORKERS_ENV} must be >= 1, got {workers}")
    return workers


def resolve_deadline(context: Any) -> Deadline | None:
    """A `Deadline` from the Lambda context's remaining time minus a safety margin (H-2),
    or `None` when there is no real context (local runs / tests → no time budget). Pure."""
    get_remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(get_remaining):
        return None
    return Deadline(get_remaining() / 1000.0 - _DEADLINE_MARGIN_S)


def compute_profile_hash(profile: Profile, spec: SearchSpec) -> str:
    """The lineage hash of what scoring judges against: the full profile payload + the three
    strictness knobs, canonically serialized (sorted keys, ASCII) → sha256 hex. Stored on the
    `profile` row at sync and stamped on every `score_event` (migration 0004), so any score can
    be traced to the exact profile content that produced it — same content, same hash, across
    runs and machines. Pure."""
    return hashlib.sha256(
        json.dumps(
            {
                **profile.model_dump(),
                "threshold": spec.threshold,
                "hard_floor": spec.hard_floor,
                "near_miss_band": spec.near_miss_band,
            },
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def resolve_filter_strategy(env: dict[str, str]) -> Any:
    """The gold `FilterStrategy` from `$GOLD_FILTER_STRATEGY` (H-3): `deterministic`
    (default) or `llm` (the `LlmFilterStrategy` on the cheap dissect model — semantic
    adjacency the token rule can't judge). Raises `ValueError` on anything else — a
    misconfigured stage must fail loudly, never silently fall back."""
    choice = (env.get(_GOLD_FILTER_ENV) or "deterministic").strip().lower()
    if choice == "deterministic":
        return DeterministicFilterStrategy()
    if choice == "llm":
        from ..adapters.filter_llm import LlmFilterStrategy

        return LlmFilterStrategy(OpenAICompatLlmClient(LlmConfig(model=_DISSECT_MODEL)))
    raise ValueError(
        f"${_GOLD_FILTER_ENV} must be 'deterministic' or 'llm', got {choice!r}"
    )


# --------------------------------------------------------------------------- handler
def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """The one v0 Lambda. Returns a `{statusCode, run_id, ...stage counts}` summary.

    On any stage failure: log it with `run_id`, return `{statusCode: 500, ...}` so the run is
    retried (and resumes — upserts skip done work, `run_log` blocks a double email). On success:
    `{statusCode: 200, ...}` with the per-stage counts.

    **Deadline guard (H-2):** LLM stages stop *starting* new work `_DEADLINE_MARGIN_S` before
    the Lambda timeout and report the remainder as `deferred`; the summary then carries
    `partial: true` and **notify is skipped** — the digest goes out on the completing re-run
    (sending early would trip the send-once `run_log` guard with an incomplete shortlist).
    """
    event = event or {}
    configure_log_level(os.environ)  # FIRST: the run's INFO telemetry must reach CloudWatch
    run_id = resolve_run_id(event)
    run_date = resolve_run_date(event)
    mode = resolve_mode(event)
    user_id = DEFAULT_USER_ID
    rlog = logging.LoggerAdapter(log, {"run_id": run_id})

    try:
        # Bound as the FIRST statement (constructed for real below) so the outer `except` can
        # persist the run summary even when a stage fails before the store is built — and can
        # never hit UnboundLocalError. Stays None through smoke mode (no audit side effects).
        audit_store: S3AuditStore | None = None
        env = dict(os.environ)

        # --- smoke mode (deploy gate): prove the Lambda reaches the DB AND the schema is at
        # the head this code expects — with ZERO side effects. Runs BEFORE any config read or
        # adapter construction, so it touches env + the repo engine ONLY: no S3, no LLM/source/
        # notifier clients, no writes, nothing sent. Post-`terraform apply` one-liner:
        # docs/runbooks/deploy.md §2. Inside this try deliberately — a connection failure
        # surfaces as the standard 500 below. ---
        if mode == "smoke":
            repo = PostgresRepository(resolve_db_url(env))
            # Aurora scale-to-zero (ERR-009): the post-deploy gate is the invocation MOST
            # exposed to a paused cluster (it runs right after `terraform apply`, often on an
            # idle stack) — wait out the resume before the version probe. Read-only `SELECT 1`
            # on the same engine, so the gate's zero-side-effects contract holds.
            wait_for_db_resume(repo.engine)
            expected = resolve_expected_migration_head(env)
            with repo.engine.connect() as conn:
                # alembic_version is a single-row, single-column table — no ORDER BY exists.
                actual = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
            if actual != expected:
                rlog.error("mode=smoke migration mismatch: db=%s expected=%s", actual, expected)
                return {
                    "statusCode": 400,
                    "run_id": run_id,
                    "run_date": run_date.isoformat(),
                    "mode": "smoke",
                    "error": (
                        f"migration mismatch: DB is at {actual!r} but this code expects "
                        f"{expected!r} — run `alembic upgrade head` (or fix $ALEMBIC_HEAD)"
                    ),
                }
            rlog.info("mode=smoke ok — alembic_version=%s", actual)
            return {
                "statusCode": 200,
                "run_id": run_id,
                "run_date": run_date.isoformat(),
                "mode": "smoke",
                "alembic_version": actual,
            }

        # Config is read at RUNTIME from its location (an s3://bucket/key URI in deployment, a
        # local path in tests/dev) — ADR-0022. Not bundled in the zip, so editing a setting +
        # `scripts/push_config.py` takes effect on the next run with no rebuild/redeploy.
        spec = SearchSpec.from_yaml_text(read_config_text(resolve_search_config_path(env)))
        profile = Profile.from_yaml_text(read_config_text(resolve_profile_path(env)))
        recipient = (env.get(_RECIPIENT_ENV) or "").strip()
        max_workers = resolve_max_workers(env)
        deadline = resolve_deadline(context)

        repo = PostgresRepository(resolve_db_url(env))
        # Aurora scale-to-zero (ERR-009): a run that catches the cluster asleep must WAIT out
        # the ~15–30s resume, not die at the first DB touch (retry_attempts=0 ⇒ a dead run
        # stays dead). Two explicit call sites cover every mode: the smoke gate's own repo
        # above, and this shared repo BEFORE the profile sync / remaining-mode split. The 90s
        # budget costs nothing against the 900s Lambda timeout + the deadline guard's margin.
        wait_for_db_resume(repo.engine)

        # Re-sync the single-user profile row from the config files EVERY run (idempotent upsert):
        # the config is the single source of truth for the user's profile + shortlist strictness,
        # so editing either config file and redeploying actually takes effect (VG8). This replaces
        # the old seed-once bootstrap, which froze the profile + knobs after the first run — a user
        # could not change any setting without a raw DB edit. The three strictness knobs are all
        # user-set on the SearchSpec now; ingest.py keeps _DEFAULT_* only as the NULL-row safety net.
        rlog.info("syncing profile row from config for user_id=%s", user_id)
        # The lineage hash (migration 0004): stored on the profile row here, then stamped on
        # every score_event this run writes — the score↔profile provenance link.
        profile_hash = compute_profile_hash(profile, spec)
        repo.upsert_profile(
            user_id=user_id,
            profile=profile.model_dump(),
            threshold=spec.threshold,
            hard_floor=spec.hard_floor,
            near_miss_band=spec.near_miss_band,
            profile_hash=profile_hash,
        )

        # Build the adapters (one place; each reads its own env var / secret path).
        source_adapter = JSearchSourceAdapter()
        raw_store = S3RawStore()
        report_store = S3ReportStore()  # B-1: same data bucket; the full-list report + presign
        # v0.12.0: the full-audit store (silver/gold/scores/runs → S3). Even CONSTRUCTION is
        # guarded — the whole audit trail is an enhancement that must NEVER fail the run (its
        # methods are internally non-fatal too; $JOBFETCHER_DATA_BUCKET is set, same as above).
        try:
            audit_store = S3AuditStore(run_id=run_id, run_date=run_date)
        except Exception as exc:  # noqa: BLE001 — audit is best-effort; never a run-fatal path
            rlog.warning("S3 audit store unavailable — run continues without audit: %s", exc)
            audit_store = None
        dissector = Dissector(
            OpenAICompatLlmClient(LlmConfig(model=_DISSECT_MODEL)), model_id=_DISSECT_MODEL
        )
        strategy = resolve_filter_strategy(env)  # H-3: deterministic (default) | llm
        scorer = Scorer(
            OpenAICompatLlmClient(LlmConfig(model=_SCORE_MODEL)), model_id=_SCORE_MODEL
        )
        notifier = SesNotifier()

        # --- reassess mode (ADR-0023): replay scoring over the already-scored postings against
        # the profile just synced from config — NO fetch, NO gold, NO notify. The medallion's
        # immutable-bronze replay: a profile improvement can graduate old jobs with zero JSearch
        # spend. Returns its own report and exits before the normal pipeline. ---
        if mode == "reassess":
            rlog.info("mode=reassess start — replay scoring, no fetch")
            reassess_report = reassess(
                run_id=run_id,
                repo=repo,
                scorer=scorer,
                profile_hash=profile_hash,
                user_id=user_id,
                max_workers=max_workers,
                deadline=deadline,
                max_age_days=spec.reassess_max_age_days,
                audit_store=audit_store,
            )
            rlog.info("mode=reassess done %s", reassess_report)
            reassess_summary = {
                "statusCode": 200,
                "run_id": run_id,
                "run_date": run_date.isoformat(),
                "mode": "reassess",
                "reassess": reassess_report,
            }
            if audit_store is not None:
                audit_store.put_run_summary(reassess_summary)  # non-fatal
            return reassess_summary

        # --- the pipeline, in sequence (each stage is idempotent via its own upserts) ---
        rlog.info("stage=ingest start run_date=%s", run_date.isoformat())
        ingest_counts = ingest(
            spec,
            run_id=run_id,
            source_adapter=source_adapter,
            raw_store=raw_store,
            repo=repo,
            dissector=dissector,
            source=spec.source,
            max_workers=max_workers,
            deadline=deadline,
            audit_store=audit_store,
        )
        rlog.info("stage=ingest done %s", ingest_counts)

        rlog.info("stage=gold start")
        db_profile = Profile.from_jsonb(repo.get_profile(user_id)["profile"])
        gold_counts = apply_gold_filter(
            spec, db_profile, strategy=strategy, repo=repo, source=spec.source,
            audit_store=audit_store,
        )
        rlog.info("stage=gold done %s", gold_counts)

        rlog.info("stage=score start")
        score_counts = score_gold(
            run_id=run_id,
            repo=repo,
            scorer=scorer,
            profile_hash=profile_hash,
            user_id=user_id,
            max_workers=max_workers,
            deadline=deadline,
            audit_store=audit_store,
        )
        rlog.info("stage=score done %s", score_counts)

        # --- notify: send-once guard (VG4). Skip entirely if the digest already went out today,
        # or if this run is PARTIAL (deadline deferred work) — an early digest would trip the
        # send-once guard with an incomplete shortlist; the completing re-run sends it.
        partial = bool(ingest_counts.get("deferred") or score_counts.get("deferred"))
        if partial:
            rlog.warning(
                "stage=notify skipped — partial run (deferred: ingest=%s score=%s); "
                "the completing re-run sends the digest",
                ingest_counts.get("deferred", 0),
                score_counts.get("deferred", 0),
            )
            notify_counts: dict[str, int] = {"surfaced": 0, "below_threshold": 0, "sent": 0}
        elif repo.was_digest_sent(user_id=user_id, run_date=run_date):
            rlog.info("stage=notify skipped — digest already sent for %s", run_date.isoformat())
            notify_counts = {"surfaced": 0, "below_threshold": 0, "sent": 0}
        else:
            rlog.info("stage=notify start recipient=%s", recipient)
            notify_counts = notify(
                run_id=run_id,
                repo=repo,
                notifier=notifier,
                recipient_email=recipient,
                user_id=user_id,
                run_date=run_date,
                # Digest truthfulness: still-open matches older than this drop out of the
                # digest (0 = keep forever) — the user knob, threaded like every other spec knob.
                max_age_days=spec.digest_max_age_days,
                # B-1: the full-list report + presigned link (best-effort inside notify —
                # a report failure degrades the digest to plain text, never fails the run).
                report_store=report_store,
            )
            # Mark sent ONLY after a successful send (notify raises on a failed send, so we never
            # get here on failure — the guard is not written, the next run re-sends).
            repo.mark_digest_sent(user_id=user_id, run_date=run_date, run_id=run_id)
            rlog.info("stage=notify done %s", notify_counts)

    except Exception as exc:  # noqa: BLE001 — surface ANY stage failure as a retryable 500
        rlog.exception("pipeline failed: %s", exc)
        error_summary = {
            "statusCode": 500,
            "run_id": run_id,
            "run_date": run_date.isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
        }
        if audit_store is not None:
            audit_store.put_run_summary(error_summary)  # non-fatal — the failed run's record
        return error_summary

    summary = {
        "statusCode": 200,
        "run_id": run_id,
        "run_date": run_date.isoformat(),
        "partial": partial,
        "ingest": ingest_counts,
        "gold": gold_counts,
        "score": score_counts,
        "notify": notify_counts,
    }
    if audit_store is not None:
        audit_store.put_run_summary(summary)  # v0.12.0 — the per-run procedure record (non-fatal)
    rlog.info("pipeline done %s", summary)
    return summary
