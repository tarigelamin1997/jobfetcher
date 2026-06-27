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
from typing import TYPE_CHECKING, Any

from ..adapters.jsearch_source import QUERY_COUNTRY_KEY, jd_and_metadata_from_jsearch
from .clean import clean
from .dissector import DissectionError
from .fingerprint import fingerprint

if TYPE_CHECKING:
    from ..adapters.s3_raw import RawStore
    from .dissector import Dissector
    from .ports import Repository, SourceAdapter
    from .search_spec import SearchSpec

log = logging.getLogger(__name__)


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

    try:
        dissected = dissector.dissect(jd, meta)
    except DissectionError as exc:
        log.warning("dissection failed for %s (run_id=%s): %s", bronze_id, run_id, exc)
        return None
    return repo.save_posting(
        dissected,
        posting_id=f"{source}:{source_job_id}",
        bronze_id=bronze_id,
        source=source,
        source_job_id=source_job_id,
        run_id=run_id,
        company=raw_payload.get("employer_name"),
        apply_url=raw_payload.get("job_apply_link"),
        description=raw_payload.get("job_description"),
        state=raw_payload.get("job_state"),
        pipeline_version=pipeline_version,
        fingerprint=fp,
        status="silver",
    )


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
) -> dict[str, int]:
    """End-to-end Step-4 run: fetch→bronze, then derive silver for each *distinct, new*
    posting. Returns a small summary of counts. `bronzed` == distinct ids landed this run;
    `silvered` + `skipped` + `already` partition them: `skipped` = dissection failures,
    `already` = an existing posting we did NOT re-dissect (C2: a re-run wastes no LLM call)."""
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
    for bronze_id, raw, query_country in landed:
        posting_id = f"{source}:{raw['job_id']}"
        # C2: a posting that already exists must NOT be re-dissected — that is wasted LLM cost
        # on a re-run. Skip the silver/LLM pass entirely and count it.
        if repo.get_posting(posting_id) is not None:
            already += 1
            continue
        result = land_silver(
            bronze_id,
            raw,
            run_id=run_id,
            source=source,
            source_job_id=raw["job_id"],
            dissector=dissector,
            repo=repo,
            language=spec.language,
            query_country=query_country,
            pipeline_version=pipeline_version,
        )
        if result is None:
            skipped += 1
        else:
            silvered += 1

    return {
        "fetched": len(landed),
        "bronzed": len(landed),
        "silvered": silvered,
        "skipped": skipped,
        "already": already,
    }
