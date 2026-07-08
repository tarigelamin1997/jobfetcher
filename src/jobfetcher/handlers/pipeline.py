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

from ..adapters.filter_deterministic import DeterministicFilterStrategy
from ..adapters.jsearch_source import JSearchSourceAdapter
from ..adapters.llm_openai import OpenAICompatLlmClient
from ..adapters.repository_postgres import PostgresRepository
from ..adapters.s3_config import read_config_text
from ..adapters.s3_raw import S3RawStore
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

# Seconds reserved before the Lambda's hard timeout: in-flight LLM calls + the tail of DB
# writes + notify must finish inside this margin (H-2 deadline guard).
_DEADLINE_MARGIN_S = 60.0


# --------------------------------------------------------------------------- pure helpers
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
    postings against the current profile, no fetch. Pure."""
    if event and isinstance(event.get("mode"), str):
        return event["mode"].strip().lower()
    return ""


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
    run_id = resolve_run_id(event)
    run_date = resolve_run_date(event)
    mode = resolve_mode(event)
    user_id = DEFAULT_USER_ID
    rlog = logging.LoggerAdapter(log, {"run_id": run_id})

    try:
        env = dict(os.environ)
        # Config is read at RUNTIME from its location (an s3://bucket/key URI in deployment, a
        # local path in tests/dev) — ADR-0022. Not bundled in the zip, so editing a setting +
        # `scripts/push_config.py` takes effect on the next run with no rebuild/redeploy.
        spec = SearchSpec.from_yaml_text(read_config_text(resolve_search_config_path(env)))
        profile = Profile.from_yaml_text(read_config_text(resolve_profile_path(env)))
        recipient = (env.get(_RECIPIENT_ENV) or "").strip()
        max_workers = resolve_max_workers(env)
        deadline = resolve_deadline(context)

        repo = PostgresRepository(resolve_db_url(env))

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
            )
            rlog.info("mode=reassess done %s", reassess_report)
            return {
                "statusCode": 200,
                "run_id": run_id,
                "run_date": run_date.isoformat(),
                "mode": "reassess",
                "reassess": reassess_report,
            }

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
        )
        rlog.info("stage=ingest done %s", ingest_counts)

        rlog.info("stage=gold start")
        db_profile = Profile.from_jsonb(repo.get_profile(user_id)["profile"])
        gold_counts = apply_gold_filter(
            spec, db_profile, strategy=strategy, repo=repo, source=spec.source
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
            )
            # Mark sent ONLY after a successful send (notify raises on a failed send, so we never
            # get here on failure — the guard is not written, the next run re-sends).
            repo.mark_digest_sent(user_id=user_id, run_date=run_date, run_id=run_id)
            rlog.info("stage=notify done %s", notify_counts)

    except Exception as exc:  # noqa: BLE001 — surface ANY stage failure as a retryable 500
        rlog.exception("pipeline failed: %s", exc)
        return {
            "statusCode": 500,
            "run_id": run_id,
            "run_date": run_date.isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
        }

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
    rlog.info("pipeline done %s", summary)
    return summary
