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

from ..adapters.jsearch_source import jd_and_metadata_from_jsearch
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
) -> list[tuple[str, dict[str, Any]]]:
    """Land every fetched raw posting to bronze (S3 + `bronze_posting`) and return the
    `(bronze_id, raw_job)` pairs for the silver pass.

    `bronze_id = f"{source}:{source_job_id}"` — so re-fetching the same id the same run/day
    is deduped automatically by the idempotent `upsert_bronze`. A posting with no `job_id`
    is skipped (can't form a stable id)."""
    landed: list[tuple[str, dict[str, Any]]] = []
    for job in source_adapter.fetch(spec, run_id=run_id):
        source_job_id = job.get("job_id")
        if not source_job_id:
            log.warning("skipping posting with no job_id (run_id=%s)", run_id)
            continue
        s3_key = raw_store.put_raw(source=source, source_job_id=source_job_id, payload=job)
        bronze_id = f"{source}:{source_job_id}"
        repo.upsert_bronze(
            bronze_id=bronze_id,
            source=source,
            source_job_id=source_job_id,
            raw_payload=job,
            run_id=run_id,
            s3_raw_key=s3_key,
        )
        landed.append((bronze_id, job))
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
    pipeline_version: str = "v0",
) -> str | None:
    """Derive one silver `posting` from a bronze raw payload: map → clean → dissect →
    fingerprint → save. Returns the `posting_id`, or `None` if the dissection failed (logged
    and skipped — one bad JD must not crash the run; the raw stays safe in bronze)."""
    jd_text, meta = jd_and_metadata_from_jsearch(raw_payload)
    jd = clean(jd_text)
    try:
        dissected = dissector.dissect(jd, meta)
    except DissectionError as exc:
        log.warning("dissection failed for %s (run_id=%s): %s", bronze_id, run_id, exc)
        return None

    fp = fingerprint(
        dissected.normalized_title,
        raw_payload.get("employer_name"),
        dissected.location,
    )
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
    """End-to-end Step-4 run: fetch→bronze, then derive silver for each. Returns a small
    summary of counts. Bronzed == fetched-with-an-id (idempotent upsert); `silvered` +
    `skipped` partition the bronzed set (skipped = dissection failures)."""
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
    for bronze_id, raw in landed:
        posting_id = land_silver(
            bronze_id,
            raw,
            run_id=run_id,
            source=source,
            source_job_id=raw["job_id"],
            dissector=dissector,
            repo=repo,
            pipeline_version=pipeline_version,
        )
        if posting_id is None:
            skipped += 1
        else:
            silvered += 1

    return {
        "fetched": len(landed),
        "bronzed": len(landed),
        "silvered": silvered,
        "skipped": skipped,
    }
