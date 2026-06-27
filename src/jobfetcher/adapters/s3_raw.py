"""`S3RawStore` — lands each immutable raw payload in S3 at `raw/{source}/{date}/{id}.json`
(the lake-landing half of bronze; the `bronze_posting` row is the DB half). Behind a tiny
`RawStore` interface so the core depends on the port, not boto3 (ADR-0015); tests use moto.

The bucket comes from `$JOBFETCHER_DATA_BUCKET` (no default — a clear error if unset; never a
hardcoded bucket). No secrets here — boto3 uses the ambient IAM identity.
"""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Protocol

_BUCKET_ENV = "JOBFETCHER_DATA_BUCKET"


class RawStore(Protocol):
    """Persists one untouched raw payload, returns its storage key (the S3 object key)."""

    def put_raw(
        self,
        *,
        source: str,
        source_job_id: str,
        payload: dict[str, Any],
        run_date: date | None = None,
    ) -> str:
        ...


class S3RawStore:
    """`RawStore` over S3 (boto3 `put_object`). One client, reused."""

    def __init__(self, *, bucket: str | None = None, client: Any = None) -> None:
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

    def put_raw(
        self,
        *,
        source: str,
        source_job_id: str,
        payload: dict[str, Any],
        run_date: date | None = None,
    ) -> str:
        """Write `payload` as pretty JSON to `raw/{source}/{date}/{source_job_id}.json`.
        Idempotent by key: re-landing the same id the same day overwrites identical bytes."""
        day = (run_date or date.today()).isoformat()
        key = f"raw/{source}/{day}/{source_job_id}.json"
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        return key
