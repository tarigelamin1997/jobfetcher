"""Step-4 ingestion orchestration: the bronze→silver landing, as pure-ish functions over the
ports (`SourceAdapter`, `RawStore`, `Repository`, `Dissector`). This is the entry a later
Lambda calls; it owns no I/O of its own — every side effect goes through an injected port, so
the same code runs against live JSearch/S3/Aurora and against mocks (moto + local Postgres).

The medallion guarantee lives here (ADR-0016): **bronze is landed first + immutably** (S3 +
`bronze_posting`, idempotent on `bronze_id`), then silver is *derived* — so a dissection
failure skips one posting without losing the raw or crashing the run.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from ..adapters.jsearch_source import QUERY_COUNTRY_KEY, jd_and_metadata_from_jsearch
from .clean import clean
from .dissector import DissectionError
from .fingerprint import fingerprint
from .notifier import render_digest
from .ports import FilterError, LlmError, NotifierError, RepositoryError
from .profile import Profile
from .report import render_full_list
from .scorer import ScorerError, subscores_payload

if TYPE_CHECKING:
    from ..adapters.s3_audit import S3AuditStore
    from ..adapters.s3_raw import RawStore
    from ..adapters.s3_reports import ReportStore
    from .dissector import Dissector
    from .models import DissectedPosting, ScoreResult
    from .ports import FilterStrategy, Notifier, Repository, SourceAdapter
    from .scorer import Scorer
    from .search_spec import SearchSpec

# The single-user profile key (v0): one row in `profile`, the multi-user seam (db/tables.py).
DEFAULT_USER_ID = "default"

# Threshold-knob fallbacks if a `profile` row leaves them NULL (the documented defaults,
# 02-architecture "Threshold"). The values still come from the DB row at runtime when set —
# these only cover a row that omitted them, never a hardcoded override of a configured knob.
_DEFAULT_THRESHOLD = 60
_DEFAULT_HARD_FLOOR = 50
_DEFAULT_NEAR_MISS_BAND = 10

# Boundary self-consistency (ADR-0028 lineage): the LLM score is non-deterministic — same input,
# ~16pt average spread measured live (max 60) — so a single score near the shortlist cut is a
# coin-flip on membership. When a posting's first score lands within RESAMPLE_TRIGGER_MARGIN of
# the runtime threshold (the ambiguous zone, on BOTH sides of the cut), it is re-scored to
# RESAMPLE_N total samples and the MEDIAN sample is kept — a coherent `ScoreResult` (its
# narrative/subscores are the ones that produced its number), never an averaged frankenscore. A
# clearly-in / clearly-out first score is kept after ONE call (the cost guard: resampling pays N×
# ONLY on the ~1/5 of scores that sit at the boundary). `RESAMPLE_N <= 1` disables resampling
# entirely (byte-for-byte the pre-boundary-resample behavior — one call, whatever it returns).
#
# The trigger margin defaults to 16 — the measured average spread, deliberately WIDER than the
# default near_miss_band (10, which spans only BELOW the threshold), because sampling noise can
# push a first score across the cut from EITHER side. It is an INDEPENDENT technical knob, never
# coupled to the user's near_miss_band strictness (setting near_miss_band=0 must not disable the
# noise guard). N defaults ODD (3) so the median is an exact single sample. Both are module knobs
# with safe defaults + orchestrator params (never a required SearchSpec field — no config break).
DEFAULT_RESAMPLE_N = 3
DEFAULT_RESAMPLE_TRIGGER_MARGIN = 16

log = logging.getLogger(__name__)

# LLM calls are pure I/O — this many run concurrently per stage (H-2). DB writes always stay
# on the main thread (the Data-API dialect's thread-safety is deliberately not relied on).
DEFAULT_MAX_WORKERS = 8

# Presigned full-list-report link lifetime (B-1). A daily email only needs same-day
# reachability; a link signed with the Lambda role's temporary creds is capped at the session
# TTL anyway (hours) — requesting 12h is a sane ask, tomorrow's digest regenerates the link.
_REPORT_URL_EXPIRY_S = 12 * 3600

# Sentinel returned by a worker whose task started after the deadline: the item was neither
# processed nor failed — it is deferred to the next (idempotent) run.
_DEFERRED = object()


class Deadline:
    """A wall-clock budget for LLM work (H-2). Workers check `expired` before starting an
    LLM call; past the deadline, remaining items are *deferred* (counted, not lost) and the
    run returns partial-but-clean instead of being killed by the Lambda timeout."""

    def __init__(self, seconds: float) -> None:
        self._until = time.monotonic() + max(seconds, 0.0)

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._until


def fetch_to_bronze(
    spec: "SearchSpec",
    *,
    run_id: str,
    source: str,
    source_adapter: "SourceAdapter",
    raw_store: "RawStore",
    repo: "Repository",
) -> list[tuple[str, dict[str, Any], str | None]]:
    """Land each *distinct* fetched raw posting to bronze (S3 + `bronze_posting`) and return
    the `(bronze_id, raw_job, query_country)` triples for the silver pass.

    `bronze_id = f"{source}:{source_job_id}"`. **The same id is landed at most once per run**
    (C2: a `set` dedups ids seen across the title×country matrix) and the S3 put + upsert are
    **skipped entirely when that bronze row already exists** (C4: bronze is immutable — a
    cross-run re-fetch must not overwrite the raw snapshot). A posting with no `job_id` is
    skipped (can't form a stable id)."""
    landed: list[tuple[str, dict[str, Any], str | None]] = []
    seen: set[str] = set()
    for raw_job in source_adapter.fetch(spec, run_id=run_id):
        # Pop the transient query-country side channel so the persisted raw payload is the
        # untouched source object (C3 threading; never mutates what bronze stores).
        job = dict(raw_job)
        query_country = job.pop(QUERY_COUNTRY_KEY, None)
        source_job_id = job.get("job_id")
        if not source_job_id:
            log.warning("skipping posting with no job_id (run_id=%s)", run_id)
            continue
        bronze_id = f"{source}:{source_job_id}"
        if bronze_id in seen:
            continue  # C2: this id already handled this run — don't re-land or re-dissect
        seen.add(bronze_id)

        # C4: `put_raw` is now idempotent (skips the put when the object already exists), so a
        # cross-run re-fetch never overwrites the immutable raw snapshot. The bronze DB row is
        # already idempotent (on_conflict_do_nothing).
        s3_key = raw_store.put_raw(source=source, source_job_id=source_job_id, payload=job)
        repo.upsert_bronze(
            bronze_id=bronze_id,
            source=source,
            source_job_id=source_job_id,
            raw_payload=job,
            run_id=run_id,
            s3_raw_key=s3_key,
        )
        landed.append((bronze_id, job, query_country))
    return landed


def land_silver(
    bronze_id: str,
    raw_payload: dict[str, Any],
    *,
    run_id: str,
    source: str,
    source_job_id: str,
    dissector: "Dissector",
    repo: "Repository",
    language: str = "en",
    query_country: str | None = None,
    pipeline_version: str = "v0",
) -> str | None:
    """Derive one silver `posting` from a bronze raw payload: map → clean → fingerprint →
    dissect → save. Returns the `posting_id`, or `None` if the dissection failed (logged
    and skipped — one bad JD must not crash the run; the raw stays safe in bronze).

    `language` (from `spec.language`) is recorded on the posting metadata — never hardcoded.
    `query_country` (the country actually queried) is the authoritative geo scope (C3): it
    overrides the unreliable per-record `job_country` when set."""
    prepared = _prepare_silver(
        bronze_id,
        raw_payload,
        run_id=run_id,
        source=source,
        source_job_id=source_job_id,
        dissector=dissector,
        language=language,
        query_country=query_country,
        pipeline_version=pipeline_version,
    )
    if prepared is None:
        return None
    dissected, kwargs = prepared
    return repo.save_posting(dissected, **kwargs)


def _prepare_silver(
    bronze_id: str,
    raw_payload: dict[str, Any],
    *,
    run_id: str,
    source: str,
    source_job_id: str,
    dissector: "Dissector",
    language: str = "en",
    query_country: str | None = None,
    pipeline_version: str = "v0",
) -> tuple[Any, dict[str, Any]] | None:
    """The pure-LLM half of `land_silver` — map → clean → fingerprint → dissect, **no DB**
    (H-2: this is what runs on the worker threads). Returns `(dissected, save_kwargs)` for
    the main-thread `repo.save_posting`, or `None` on an isolated dissection failure."""
    jd_text, meta = jd_and_metadata_from_jsearch(
        raw_payload, language=language, query_country=query_country
    )
    jd = clean(jd_text)

    # C1: the fingerprint is the deterministic dedup key — it must be stable across model
    # versions, so it is computed from the RAW source fields (the source title + company +
    # location), never from the LLM's `normalized_title`. Compute it before dissecting so a
    # dissection failure doesn't change the key.
    fp = fingerprint(
        meta.raw_title,
        raw_payload.get("employer_name"),
        meta.location,
    )

    # Failure isolation is symmetric with `score_gold` (ERR-006): a provider-level LlmError
    # (e.g. a 503 that outlived the client's retries) skips THIS posting, never the run.
    try:
        dissected = dissector.dissect(jd, meta)
    except (DissectionError, LlmError) as exc:
        log.warning("dissection failed for %s (run_id=%s): %s", bronze_id, run_id, exc)
        return None
    return dissected, {
        "posting_id": f"{source}:{source_job_id}",
        "bronze_id": bronze_id,
        "source": source,
        "source_job_id": source_job_id,
        "run_id": run_id,
        "company": raw_payload.get("employer_name"),
        "apply_url": raw_payload.get("job_apply_link"),
        "description": raw_payload.get("job_description"),
        "state": raw_payload.get("job_state"),
        "pipeline_version": pipeline_version,
        "fingerprint": fp,
        "status": "silver",
    }


def ingest(
    spec: "SearchSpec",
    *,
    run_id: str,
    source_adapter: "SourceAdapter",
    raw_store: "RawStore",
    repo: "Repository",
    dissector: "Dissector",
    source: str = "jsearch",
    pipeline_version: str = "v0",
    max_workers: int = DEFAULT_MAX_WORKERS,
    deadline: Deadline | None = None,
    audit_store: "S3AuditStore | None" = None,
) -> dict[str, int]:
    """End-to-end Step-4 run: fetch→bronze, then derive silver for each *distinct, new*
    posting. Returns a small summary of counts. `bronzed` == distinct ids landed this run;
    `silvered` + `skipped` + `already` + `deferred` partition them: `skipped` = dissection
    failures, `already` = an existing posting we did NOT re-dissect (C2: a re-run wastes no
    LLM call), `deferred` = not attempted because the `deadline` passed (the next idempotent
    run picks them up).

    **Concurrency model (H-2):** dissections (pure LLM I/O) run on up to `max_workers`
    threads; every `repo` write stays on the main thread — the Data-API dialect's
    thread-safety is never relied on."""
    landed = fetch_to_bronze(
        spec,
        run_id=run_id,
        source=source,
        source_adapter=source_adapter,
        raw_store=raw_store,
        repo=repo,
    )
    silvered = 0
    skipped = 0
    already = 0
    deferred = 0

    # Main-thread pass: partition into already-silvered vs to-dissect (repo reads stay here).
    work: list[tuple[str, dict[str, Any], str | None]] = []
    for bronze_id, raw, query_country in landed:
        posting_id = f"{source}:{raw['job_id']}"
        # C2: a posting that already exists must NOT be re-dissected — that is wasted LLM cost
        # on a re-run. Skip the silver/LLM pass entirely and count it.
        if repo.get_posting(posting_id) is not None:
            already += 1
        else:
            work.append((bronze_id, raw, query_country))

    def _dissect_task(item: tuple[str, dict[str, Any], str | None]):
        if deadline is not None and deadline.expired:
            return _DEFERRED  # out of time budget — leave for the next run, don't start LLM work
        bronze_id, raw, query_country = item
        return _prepare_silver(
            bronze_id,
            raw,
            run_id=run_id,
            source=source,
            source_job_id=raw["job_id"],
            dissector=dissector,
            language=spec.language,
            query_country=query_country,
            pipeline_version=pipeline_version,
        )

    # S3 audit (v0.12.0): accumulate each landed silver record on the MAIN thread (next to the
    # repo write — never on a worker), then write ONE batched JSONL after the join. Non-fatal.
    silver_records: list[dict[str, Any]] = []
    if work:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_dissect_task, item) for item in work]
            for fut in as_completed(futures):
                outcome = fut.result()
                if outcome is _DEFERRED:
                    deferred += 1
                elif outcome is None:
                    skipped += 1
                else:
                    dissected, kwargs = outcome
                    repo.save_posting(dissected, **kwargs)  # main thread — the only writer
                    silvered += 1
                    if audit_store is not None:
                        silver_records.append({
                            **dissected.model_dump(mode="json"),
                            "posting_id": kwargs["posting_id"],
                            "bronze_id": kwargs["bronze_id"],
                        })
    if audit_store is not None:
        audit_store.put_silver(silver_records)
    if deferred:
        log.warning(
            "ingest deadline reached (run_id=%s): %d dissection(s) deferred to the next run",
            run_id,
            deferred,
        )

    return {
        "fetched": len(landed),
        "bronzed": len(landed),
        "silvered": silvered,
        "skipped": skipped,
        "already": already,
        "deferred": deferred,
    }


def apply_gold_filter(
    spec: "SearchSpec",
    profile: "Profile",
    *,
    strategy: "FilterStrategy",
    repo: "Repository",
    source: str = "jsearch",  # noqa: ARG001 — accepted for symmetry/future multi-source; unused in v0
    audit_store: "S3AuditStore | None" = None,
) -> dict[str, int]:
    """Step-4b gold filter: load every silver posting → ask the `strategy` if it is a likely
    fit → for each fit, create its trivial **1:1 cluster** (cluster_id == posting_id), attach
    it (`posting.cluster_id`), and promote it (`status='gold_candidate'`). Non-fits stay silver.

    **Type-replaceable** (ADR-0015): the caller injects the strategy — `DeterministicFilterStrategy`
    by default (P1 — no redundant LLM at v0 volume), `LlmFilterStrategy` selectable.

    **Fail-open** (build-plan Step 4b FAILURE-MODE): a `FilterStrategy` that raises `FilterError`
    is treated as INCLUDE — a real fit must never be dropped before scoring; over-inclusion is
    cheap (the Scorer filters), a dropped fit is invisible. The fail-open count is logged.

    Returns `{silver, gold, dropped}` (silver = candidates examined; gold + dropped partition it).
    """
    candidates = repo.get_silver_postings()
    gold = 0
    dropped = 0
    failed_open = 0
    # S3 audit (v0.12.0): one decision record per candidate examined (fit or not). Non-fatal.
    gold_records: list[dict[str, Any]] = []
    for posting_id, posting in candidates:
        try:
            likely_fit = strategy.filter(spec, profile, posting)
        except FilterError as exc:
            log.warning("gold filter failed open (include) for %s: %s", posting_id, exc)
            likely_fit = True
            failed_open += 1

        if audit_store is not None:
            # cluster_id == posting_id for a promoted candidate (v0 1:1 cluster); None if dropped.
            gold_records.append({
                "posting_id": posting_id,
                "cluster_id": posting_id if likely_fit else None,
                "likely_fit": likely_fit,
                "promoted": likely_fit,
            })

        if not likely_fit:
            dropped += 1
            continue

        # v0: a trivial 1:1 cluster (cluster_id == posting_id); real clustering is M2.
        repo.upsert_cluster(cluster_id=posting_id, representative_posting_id=posting_id)
        repo.set_posting_cluster(posting_id, posting_id)
        repo.mark_gold_candidate(posting_id)
        gold += 1

    if audit_store is not None:
        audit_store.put_gold(gold_records)

    summary = {"silver": len(candidates), "gold": gold, "dropped": dropped}
    if failed_open:
        log.info("gold filter: %d posting(s) failed open (included)", failed_open)
    return summary


def derive_fit_category(
    score: int, *, threshold: int, hard_floor: int, near_miss_band: int
) -> str:
    """Map a numeric score to a `fit_category` from the **runtime** threshold/floor/band — the
    band routing, in code, never asked of the LLM (VG8). The bands (02-architecture "Threshold"):

      - `score >= threshold`                        -> "strong_fit"  (the surfaced shortlist)
      - `threshold - near_miss_band <= score`       -> "near_miss"   (the watch band below the
        `< threshold`                                                 cut, default 50-59)
      - `score >= hard_floor` (below the near band)  -> "stretch"    (a real-but-distant fit kept
                                                                      for analytics, above floor)
      - `score < hard_floor`                         -> "misaligned" (analytics only)

    `stretch` is the band-derived 4th bucket the ERD names (`strong_fit | stretch | misaligned
    | near_miss`): the slice that clears the hard floor but sits below the near-miss band — i.e.
    a genuine but far stretch, distinct from a near-miss (just-below-threshold) and from
    misaligned (below floor). It is derived purely from the configured bands — no new knob.
    """
    if score >= threshold:
        return "strong_fit"
    if score >= threshold - near_miss_band:
        return "near_miss"
    if score >= hard_floor:
        return "stretch"
    return "misaligned"


def _load_profile_and_knobs(
    repo: "Repository", user_id: str
) -> tuple["Profile", int, int, int]:
    """Load the profile + its runtime threshold/floor/band from the `profile` row (the single
    authority, VG8); NULL knobs fall back to the documented defaults. Shared by `score_gold`
    and `reassess`. Raises `RepositoryError` if there is no profile row."""
    row = repo.get_profile(user_id)
    if row is None:
        raise RepositoryError(f"no profile row for user_id={user_id!r} — cannot score")
    profile = Profile.from_jsonb(row["profile"])
    threshold = row["threshold"] if row["threshold"] is not None else _DEFAULT_THRESHOLD
    hard_floor = row["hard_floor"] if row["hard_floor"] is not None else _DEFAULT_HARD_FLOOR
    near_miss_band = (
        row["near_miss_band"] if row["near_miss_band"] is not None else _DEFAULT_NEAR_MISS_BAND
    )
    return profile, threshold, hard_floor, near_miss_band


def _median_sample(samples: "list[ScoreResult]") -> "ScoreResult":
    """The sample carrying the median score — a COHERENT `ScoreResult` (its strengths/gaps/
    assessment/subscores are the ones that produced its number), never an averaged frankenscore.
    Samples are sorted by score and the lower-median index is returned, so an even N still yields
    a real sample deterministically; an odd N (the default 3) is the exact median."""
    ordered = sorted(samples, key=lambda r: r.score)
    return ordered[(len(ordered) - 1) // 2]


def _score_with_resample(
    scorer: "Scorer",
    dissected: "DissectedPosting",
    profile: Profile,
    *,
    threshold: int,
    trigger_margin: int,
    resample_n: int,
    deadline: Deadline | None = None,
) -> "ScoreResult":
    """Score one posting once; resample near the boundary for self-consistency.

    The LLM score is non-deterministic, so a single score within `trigger_margin` of `threshold`
    is a coin-flip on shortlist membership. In that ambiguous zone (checked on BOTH sides of the
    cut) the posting is re-scored to `resample_n` TOTAL samples and the MEDIAN sample is returned
    (`_median_sample` — a coherent judgment, never averaged). A clearly-in / clearly-out first
    score, or `resample_n <= 1` (disabled), returns after EXACTLY ONE call — the cost guard.

    Purity + isolation are preserved: the `Scorer` is untouched, and a `ScorerError`/`LlmError`
    from ANY sample propagates to the caller's per-item try/except (the posting is skipped, the
    run continues) exactly as a single-score failure does today.

    **Deadline-aware (H-2 "never times out"):** the FIRST sample always runs — an in-flight task
    completing its first score is the existing task-start contract — but each EXTRA sample is
    gated: once `deadline` has passed we STOP and return the median of the samples collected so
    far (`_median_sample` handles an even/short count), never STARTING a new score past the wall.
    So resampling can never push a full-set reassess past the Lambda timeout."""
    first = scorer.score(dissected, profile)
    if resample_n <= 1 or abs(first.score - threshold) > trigger_margin:
        return first
    samples = [first]
    for _ in range(resample_n - 1):
        if deadline is not None and deadline.expired:
            break  # out of budget — median of what we have; never START a sample past the wall
        samples.append(scorer.score(dissected, profile))
    return _median_sample(samples)


def score_gold(
    *,
    run_id: str,
    repo: "Repository",
    scorer: "Scorer",
    profile_hash: str,
    user_id: str = DEFAULT_USER_ID,
    max_workers: int = DEFAULT_MAX_WORKERS,
    deadline: Deadline | None = None,
    resample_n: int = DEFAULT_RESAMPLE_N,
    resample_margin: int = DEFAULT_RESAMPLE_TRIGGER_MARGIN,
    audit_store: "S3AuditStore | None" = None,
) -> dict[str, int]:
    """Step-5 scoring: load the candidate profile + its **runtime** threshold knobs, then for
    each gold candidate -> score it (LLM, boundary-resampled) -> derive its `fit_category` from
    the config bands (VG8) -> upsert the `score` row (keyed on `cluster_id`) -> mark it `scored`.

    **Boundary self-consistency:** a candidate whose first score lands within `resample_margin`
    of `threshold` is re-scored to `resample_n` samples and the MEDIAN sample is persisted, so a
    non-deterministic score near the cut stops being a coin-flip on the shortlist (`_score_with_
    resample`). `resample_n <= 1` disables it (identical to before). Cost falls only on the
    boundary set; a clearly-in/out score is scored exactly once.

    `profile_hash` (required — the caller computes it from the profile+knobs it synced) is
    stamped, with `scorer.model_id` and `run_id`, on the `score_event` lineage row that
    `save_score` appends alongside every score (migration 0004).

    The profile and the threshold/floor/band are read from the `profile` row **at runtime**
    (never hardcoded) — changing `threshold` in that one DB value changes which jobs surface
    on the next run, with no code change (VG8). The surfaced/shortlist set is `score >=
    threshold` (== `fit_category == 'strong_fit'`).

    **A scoring/LLM failure (`ScorerError`/`LlmError`) is logged and SKIPPED, never crashes the
    run** (mirrors `land_silver`): one un-scorable posting must not lose the rest of the
    shortlist — but a DB failure (`RepositoryError`) stays loud and aborts the run.

    Returns `{gold, scored, surfaced, failed, deferred}` — `gold` = candidates examined;
    `scored` + `failed` + `deferred` partition them; `surfaced` = those at/above the
    threshold (a subset of `scored`); `deferred` = not attempted (deadline passed — the next
    idempotent run scores them).

    **Concurrency model (H-2):** scoring calls (pure LLM I/O) run on up to `max_workers`
    threads; the `save_score`/`mark_scored` writes stay on the main thread.
    """
    profile, threshold, hard_floor, near_miss_band = _load_profile_and_knobs(repo, user_id)

    candidates = repo.get_gold_candidates()
    scored = 0
    surfaced = 0
    failed = 0
    deferred = 0
    # S3 audit (v0.12.0): one record per persisted score (main thread), batched after the join.
    score_records: list[dict[str, Any]] = []

    def _score_task(candidate: tuple) -> Any:
        posting_id, _cluster_id, dissected = candidate
        if deadline is not None and deadline.expired:
            return _DEFERRED  # out of time budget — leave for the next run
        try:
            return _score_with_resample(
                scorer,
                dissected,
                profile,
                threshold=threshold,
                trigger_margin=resample_margin,
                resample_n=resample_n,
                deadline=deadline,
            )
        except (ScorerError, LlmError) as exc:
            log.warning("scoring failed for %s (run_id=%s): %s", posting_id, run_id, exc)
            return None

    if candidates:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_score_task, c): c for c in candidates}
            for fut in as_completed(futures):
                result = fut.result()
                if result is _DEFERRED:
                    deferred += 1
                    continue
                if result is None:
                    failed += 1
                    continue
                posting_id, cluster_id, _dissected = futures[fut]
                fit_category = derive_fit_category(
                    result.score,
                    threshold=threshold,
                    hard_floor=hard_floor,
                    near_miss_band=near_miss_band,
                )
                repo.save_score(  # main thread — the only writer
                    cluster_id=cluster_id,
                    score=result.score,
                    fit_category=fit_category,
                    strengths=result.strengths,
                    gaps=result.gaps,
                    strategic_assessment=result.strategic_assessment,
                    poster_type=result.poster_type,
                    legitimacy_verified=result.legitimacy_verified,
                    scoring_model=scorer.model_id,
                    profile_hash=profile_hash,
                    run_id=run_id,
                    # Migration 0006 (SHADOW): the 7-factor blob + code_total + llm_total,
                    # or None (→ NULL) when the LLM omitted any subscore. `result.score`
                    # above stays the product number regardless.
                    subscores=subscores_payload(result),
                )
                repo.mark_scored(posting_id)
                scored += 1
                if result.score >= threshold:
                    surfaced += 1
                if audit_store is not None:
                    score_records.append({
                        "posting_id": posting_id,
                        "cluster_id": cluster_id,
                        "fit_category": fit_category,
                        **result.model_dump(mode="json"),
                    })
    if audit_store is not None:
        audit_store.put_scores(score_records)
    if deferred:
        log.warning(
            "scoring deadline reached (run_id=%s): %d candidate(s) deferred to the next run",
            run_id,
            deferred,
        )

    return {
        "gold": len(candidates),
        "scored": scored,
        "surfaced": surfaced,
        "failed": failed,
        "deferred": deferred,
    }


def reassess(
    *,
    run_id: str,
    repo: "Repository",
    scorer: "Scorer",
    profile_hash: str,
    user_id: str = DEFAULT_USER_ID,
    max_workers: int = DEFAULT_MAX_WORKERS,
    deadline: Deadline | None = None,
    resample_n: int = DEFAULT_RESAMPLE_N,
    resample_margin: int = DEFAULT_RESAMPLE_TRIGGER_MARGIN,
    max_age_days: int | None = None,
    audit_store: "S3AuditStore | None" = None,
) -> dict[str, Any]:
    """Replay scoring over the already-scored postings against the **current** profile — no
    fetch, no gold (ADR-0023). The medallion's immutable-bronze → replay property: when the
    user's profile improves (a new skill), a posting that was a `stretch`/`near_miss` can
    **graduate** to `strong_fit` with **zero JSearch calls** (only LLM scoring tokens).

    Same concurrency model as `score_gold` (H-2): LLM calls on `max_workers` threads, all DB
    writes on the main thread; `save_score` carries the old score into `previous_score`, and
    stamps `profile_hash` (required) + `scorer.model_id` + `run_id` on the `score_event`
    lineage row it appends (migration 0004).

    `max_age_days` bounds the replay by posting age (`None` or `0` = unbounded — every scored
    posting): passed straight to `get_scored_for_reassess`, which ages each posting by
    `COALESCE(posting.fetched_at, bronze.fetched_at)` and still INCLUDES a posting whose age
    is unknown even when the bound is set (see its docstring).

    Same boundary self-consistency as `score_gold` (`_score_with_resample`): a re-score that
    lands within `resample_margin` of `threshold` is resampled to `resample_n` and the MEDIAN
    sample is kept, so a genuine profile change is what moves a score, not sampling noise.
    `resample_n <= 1` disables it (identical to before).

    Returns `{reassessed, graduated, downgraded, unchanged, failed, deferred}` plus a
    `graduations` list (`posting_id/title/company/old_score→new_score/old_cat→new_cat`) — a
    **graduation** = a posting that newly reached at/above the threshold (`old < threshold <=
    new`) **AND whose prior score came from a DIFFERENT profile** (`prior_profile_hash !=
    profile_hash`). A crossing under an unchanged profile is LLM noise, not a skill gain, so it
    is counted `unchanged`, never announced. The digest of graduations rides the email-UX unit;
    here they are reported + persisted.

    The report also carries the |new − old| **delta distribution** over the successfully
    reassessed postings (scoring-stability observability, an M7 input): `delta_buckets`
    (`{"0-5", "6-10", "11-20", "21+"}` counts), `max_delta`, and `mean_delta` (1 decimal;
    `0`/`0.0` when nothing was reassessed). Additive — nothing existing renamed."""
    profile, threshold, hard_floor, near_miss_band = _load_profile_and_knobs(repo, user_id)
    targets = repo.get_scored_for_reassess(max_age_days=max_age_days)

    reassessed = 0
    graduated = 0
    downgraded = 0
    unchanged = 0
    failed = 0
    deferred = 0
    graduations: list[dict[str, Any]] = []
    deltas: list[int] = []  # |new − old| per successful reassess — the distribution input
    # S3 audit (v0.12.0): one record per re-scored posting (main thread), batched after the join.
    score_records: list[dict[str, Any]] = []

    def _rescore_task(target: tuple) -> Any:
        posting_id = target[0]
        if deadline is not None and deadline.expired:
            return _DEFERRED
        try:
            return _score_with_resample(  # target[2] = dissected
                scorer,
                target[2],
                profile,
                threshold=threshold,
                trigger_margin=resample_margin,
                resample_n=resample_n,
                deadline=deadline,
            )
        except (ScorerError, LlmError) as exc:
            log.warning("reassess scoring failed for %s (run_id=%s): %s", posting_id, run_id, exc)
            return None

    if targets:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_rescore_task, t): t for t in targets}
            for fut in as_completed(futures):
                result = fut.result()
                if result is _DEFERRED:
                    deferred += 1
                    continue
                if result is None:
                    failed += 1
                    continue
                posting_id, cluster_id, dissected, old_score, old_cat, prior_profile_hash = (
                    futures[fut]
                )
                new_cat = derive_fit_category(
                    result.score,
                    threshold=threshold,
                    hard_floor=hard_floor,
                    near_miss_band=near_miss_band,
                )
                repo.save_score(  # main thread — the only writer; carries old → previous_score
                    cluster_id=cluster_id,
                    score=result.score,
                    fit_category=new_cat,
                    strengths=result.strengths,
                    gaps=result.gaps,
                    strategic_assessment=result.strategic_assessment,
                    poster_type=result.poster_type,
                    legitimacy_verified=result.legitimacy_verified,
                    scoring_model=scorer.model_id,
                    profile_hash=profile_hash,
                    run_id=run_id,
                    previous_score=old_score,
                    # Migration 0006 (SHADOW): same threading as score_gold — the blob or
                    # None (→ NULL); the LLM holistic stays the product number.
                    subscores=subscores_payload(result),
                )
                reassessed += 1
                deltas.append(abs(result.score - old_score))
                if audit_store is not None:
                    score_records.append({
                        "posting_id": posting_id,
                        "cluster_id": cluster_id,
                        "fit_category": new_cat,
                        "previous_score": old_score,
                        **result.model_dump(mode="json"),
                    })
                # A graduation is a crossing UP *caused by a profile change* — the prior score
                # came from a DIFFERENT profile than this run's. A crossing under the SAME
                # profile is LLM sampling noise (the boundary resample already suppresses most),
                # so it is folded into `unchanged`, never announced or badged.
                crossed_up = old_score < threshold <= result.score
                # An unknown prior hash (None — a pathological pre-0004 row with no lineage
                # event) can't PROVE a profile change, so it is NOT a graduation — consistent
                # with the digest side (`prior is not None and prior != latest`).
                profile_changed = (
                    prior_profile_hash is not None and prior_profile_hash != profile_hash
                )
                if crossed_up and profile_changed:  # honest graduation
                    graduated += 1
                    graduations.append(
                        {
                            "posting_id": posting_id,
                            "title": dissected.normalized_title or dissected.raw_title,
                            "old_score": old_score,
                            "new_score": result.score,
                            "old_category": old_cat,
                            "new_category": new_cat,
                        }
                    )
                elif old_score >= threshold > result.score:  # crossed DOWN → downgraded
                    downgraded += 1
                else:  # both sides equal, or a same-profile (noise) crossing
                    unchanged += 1
    if audit_store is not None:
        audit_store.put_scores(score_records)
    if deferred:
        log.warning(
            "reassess deadline reached (run_id=%s): %d posting(s) deferred to the next run",
            run_id,
            deferred,
        )
    log.info(
        "reassess done (run_id=%s): %d reassessed, %d graduated, %d downgraded",
        run_id,
        reassessed,
        graduated,
        downgraded,
    )
    # |new − old| distribution over the successful reassessments (M7 calibration input):
    # bucket edges are inclusive upper bounds (0-5, 6-10, 11-20, 21+); empty run → all-zero
    # buckets, max 0, mean 0.0 (honest zeros, not None — the keys are always present).
    delta_buckets = {"0-5": 0, "6-10": 0, "11-20": 0, "21+": 0}
    for d in deltas:
        if d <= 5:
            delta_buckets["0-5"] += 1
        elif d <= 10:
            delta_buckets["6-10"] += 1
        elif d <= 20:
            delta_buckets["11-20"] += 1
        else:
            delta_buckets["21+"] += 1
    max_delta = max(deltas) if deltas else 0
    mean_delta = round(sum(deltas) / len(deltas), 1) if deltas else 0.0
    log.info(
        "reassess delta distribution (run_id=%s): buckets=%s max=%d mean=%.1f",
        run_id,
        delta_buckets,
        max_delta,
        mean_delta,
    )
    return {
        "reassessed": reassessed,
        "graduated": graduated,
        "downgraded": downgraded,
        "unchanged": unchanged,
        "failed": failed,
        "deferred": deferred,
        "graduations": graduations,
        "delta_buckets": delta_buckets,
        "max_delta": max_delta,
        "mean_delta": mean_delta,
    }


def notify(
    *,
    run_id: str,
    repo: "Repository",
    notifier: "Notifier",
    recipient_email: str,
    user_id: str = DEFAULT_USER_ID,
    run_date: date | None = None,
    max_age_days: int | None = None,
    report_store: "ReportStore | None" = None,
) -> dict[str, int]:
    """Step-6 notification: load the profile (its **runtime** threshold) → read the scored
    shortlist (surfaced + below count) → render the daily digest → send it.

    The threshold is read from the `profile` row at runtime (VG8) — the same knob the Scorer
    used — and a NULL falls back to the documented default. The recipient is the caller's arg
    (the Step-7 handler passes `$RECIPIENT_EMAIL`), not hardcoded.

    **Digest truthfulness:** `since` = `repo.get_last_digest_sent_at(user_id)` — when the last
    digest actually went out (`MAX(run_log.digest_sent_at)`; no rows ⇒ `None` ⇒ the first-ever
    digest, everything is new). The renderer splits the shortlist into "new since last digest"
    vs "still open" from each item's `scored_at` vs `since` (fresh judgment) + its
    `previous_score` (first scoring / graduation — pure functions in `core/notifier.py`).
    `max_age_days` (the handler threads `spec.digest_max_age_days`) drops still-open matches
    older than N days from the digest entirely — `None`/`0` = keep forever.

    **A send failure is LOUD** (re-raised): email is the v0 surface, so a failed send is a
    failed run, never a silent skip. **Zero surfaced matches still sends** a valid "no matches
    today" email (VG5 negative) — the digest renderer handles the empty case, not the caller
    (and a zero-NEW day sends an honest "no new matches since {date}" email).

    **The full-list report link is an enhancement, NEVER a new way to fail (B-1):** when a
    `report_store` is supplied, the whole build→upload→presign is best-effort inside a guard —
    any failure is logged and the digest STILL sends, degraded to today's plain text (no link).
    The email send itself stays loud, unchanged.

    Returns `{surfaced, below_threshold, sent}` (`sent` is 1 — a send failure raises before
    we get here)."""
    if not recipient_email:
        raise NotifierError("no recipient_email — cannot send the digest")
    row = repo.get_profile(user_id)
    if row is None:
        raise RepositoryError(f"no profile row for user_id={user_id!r} — cannot notify")
    threshold = row["threshold"] if row["threshold"] is not None else _DEFAULT_THRESHOLD

    # `notify()` is the SINGLE threshold authority (VG8): it resolves the runtime threshold
    # (DB row → documented default) and passes it down, so the surfaced/below split is computed
    # against the one config knob — the Repository no longer re-derives its own constant.
    since = repo.get_last_digest_sent_at(user_id=user_id)
    the_date = run_date or date.today()
    items, below = repo.get_scored_shortlist(
        threshold=threshold, since=since, max_age_days=max_age_days
    )

    # B-1: build the full-list report + presign a link to it. NON-FATAL by contract — read,
    # render, upload and presign are ALL inside the guard; any failure degrades the digest to
    # plain text (full_list_url stays None) and is logged, never raised. The link is same-day
    # reachability, never a reason the daily run fails.
    full_list_url: str | None = None
    if report_store is not None:
        try:
            all_scored = repo.get_all_scored(max_age_days=max_age_days)
            report_html = render_full_list(
                all_scored,
                threshold=threshold,
                run_date=the_date,
                generated_at=datetime.now(timezone.utc),
            )
            report_key = f"reports/{the_date.isoformat()}/jobs-{run_id}.html"
            report_store.put_report(html=report_html, key=report_key)
            full_list_url = report_store.presign(key=report_key, expires=_REPORT_URL_EXPIRY_S)
            log.info(
                "notify: full-list report uploaded (run_id=%s key=%s jobs=%d)",
                run_id, report_key, len(all_scored),
            )
        except Exception as exc:  # noqa: BLE001 — the link is an enhancement; NEVER fail the send
            log.warning(
                "notify: full-list report skipped (run_id=%s) — digest sends without a link: %s",
                run_id, exc,
            )
            full_list_url = None

    subject, html_body, text_body = render_digest(
        items, below, threshold=threshold, date=the_date, since=since,
        full_list_url=full_list_url,
    )
    # A send failure propagates (NotifierError) — the v0 surface is the email; a failed send is
    # a failed run, not a swallowed warning (mirrors the loud DB-failure stance in score_gold).
    notifier.send(
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        recipients=[recipient_email],
    )
    log.info(
        "notify: run_id=%s surfaced=%d below=%d sent=1", run_id, len(items), below
    )
    return {"surfaced": len(items), "below_threshold": below, "sent": 1}
