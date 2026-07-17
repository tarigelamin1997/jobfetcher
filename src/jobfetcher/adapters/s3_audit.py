"""`S3AuditStore` — persists each pipeline stage's structured procedures + results to S3, so the
full medallion (silver dissections, gold decisions, scores) **and** the per-run summary live in S3
alongside Aurora — a replayable audit trail (v0.12.0). Today only the bronze RAW (`raw/…`) and the
rendered report (`reports/…`) reach S3; the derived results were Aurora-only and the run summary
lived only in the logs.

Mirrors `S3RawStore`/`S3ReportStore`: the bucket comes from `$JOBFETCHER_DATA_BUCKET` (no default —
a clear error if unset), boto3 uses the ambient IAM identity (no secrets here), tests inject a
moto/mock client. The run context (`run_id`, `run_date`) is bound at construction so stage call
sites pass only records. No IAM/Terraform change: the Lambda role already grants `s3:PutObject`
on the whole data bucket, so these new prefixes are already covered.

**Non-fatal by contract:** every write goes through `_safe_put`, which logs a warning and returns
`None` on ANY failure — an audit write can NEVER fail a pipeline run (the run's DB writes + email
are independent). This mirrors the `notify` full-list-report guard, but at the store boundary, so
every call site is inherently non-fatal.

**Batched:** one object per stage per run (JSONL — one record per line), so the audit costs a
handful of PutObjects per run, not one per posting. Overwrite semantics — a re-run for the same
`run_id` overwrites its own objects (like `reports/`, unlike immutable-bronze).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any

log = logging.getLogger(__name__)

_BUCKET_ENV = "JOBFETCHER_DATA_BUCKET"


class S3AuditStore:
    """Persists stage records + the run summary to S3 under `silver/`, `gold/`, `scores/`, `runs/`
    (keyed `{prefix}/{run_date}/{run_id}`). Every write is best-effort (non-fatal)."""

    def __init__(
        self, *, run_id: str, run_date: date, bucket: str | None = None, client: Any = None
    ) -> None:
        self._run_id = run_id
        self._day = run_date.isoformat()
        self._bucket = bucket or os.environ.get(_BUCKET_ENV) or ""
        if not self._bucket.strip():
            raise ValueError(
                f"no S3 data bucket configured — set ${_BUCKET_ENV} or pass bucket="
            )
        if client is not None:
            self._client = client
        else:
            import boto3  # lazy: tests inject a moto/mock client and need no real import here

            self._client = boto3.client("s3")

    # ------------------------------------------------------------------ stage writers (batched)
    def put_silver(self, records: list[dict[str, Any]]) -> str | None:
        """The silver dissections → `silver/{date}/{run_id}.jsonl` (one JSON per line)."""
        return self._put_jsonl("silver", records)

    def put_gold(self, records: list[dict[str, Any]]) -> str | None:
        """The gold filter decisions → `gold/{date}/{run_id}.jsonl`."""
        return self._put_jsonl("gold", records)

    def put_scores(self, records: list[dict[str, Any]]) -> str | None:
        """The score results → `scores/{date}/{run_id}.jsonl` (both score_gold and reassess)."""
        return self._put_jsonl("scores", records)

    def put_run_summary(self, summary: dict[str, Any]) -> str | None:
        """The handler's run-summary dict → `runs/{date}/{run_id}.json` — the per-run procedure
        record (statusCode, per-stage counts, partial, reassess graduations/deltas) that otherwise
        survives only in the logs."""
        key = f"runs/{self._day}/{self._run_id}.json"
        return self._guarded_put(
            key,
            lambda: json.dumps(summary, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            "application/json",
        )

    # ------------------------------------------------------------------ internals
    def _put_jsonl(self, prefix: str, records: list[dict[str, Any]]) -> str | None:
        """Batch `records` into one newline-delimited-JSON object. An **empty** batch writes
        nothing (the run summary records the zero, so an absent object is unambiguous — an
        empty-object write would falsely imply the stage ran with results)."""
        if not records:
            return None
        key = f"{prefix}/{self._day}/{self._run_id}.jsonl"
        return self._guarded_put(
            key,
            lambda: "\n".join(
                json.dumps(r, ensure_ascii=False, default=str) for r in records
            ).encode("utf-8"),
            "application/x-ndjson",
        )

    def _guarded_put(self, key: str, make_body: "Any", content_type: str) -> str | None:
        """The single non-fatal boundary: **serialize AND write** inside one guard, or log +
        return `None` on ANY failure. Serialization is inside the guard on purpose — a stray
        non-serializable value must be as non-fatal as an S3 error. An audit write must never
        fail the pipeline run (mirrors the `notify` report guard)."""
        try:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=make_body(), ContentType=content_type
            )
            return key
        except Exception as exc:  # noqa: BLE001 — audit is an enhancement; NEVER fail the run
            log.warning("S3 audit write skipped (key=%s): %s", key, exc)
            return None
