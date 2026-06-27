"""`S3RawStore` — lands each immutable raw payload in S3 at `raw/{source}/{date}/{id}.json`
(the lake-landing half of bronze; the `bronze_posting` row is the DB half). Behind a tiny
`RawStore` interface so the core depends on the port, not boto3 (ADR-0015); tests use moto.

The bucket comes from `$JOBFETCHER_DATA_BUCKET` (no default — a clear error if unset; never a
hardcoded bucket). No secrets here — boto3 uses the ambient IAM identity.
"""
from __future__ import annotations

import json
import os
import urllib.parse
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

        **Idempotent + immutable (C4):** if the object already exists it is NOT overwritten —
        bronze is an immutable snapshot, so a cross-run re-fetch returns the existing key
        without re-putting. The existence check is a single cheap `head_object`.

        The id segment is percent-encoded (`/` and other separators escaped) so an id like
        `abc/def` lands as one flat object, never nested S3 prefixes. Deterministic; the DB
        still stores the real, unescaped id."""
        day = (run_date or date.today()).isoformat()
        safe_id = urllib.parse.quote(source_job_id, safe="")
        key = f"raw/{source}/{day}/{safe_id}.json"
        if self._exists(key):
            return key  # immutable bronze — never overwrite an existing raw snapshot
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        return key

    def _exists(self, key: str) -> bool:
        """True if `key` already exists in the bucket. A 404/NoSuchKey/NotFound means absent;
        any other client error propagates (we don't silently overwrite on an ambiguous
        failure). Reads the error shape off the exception's `response` dict so it works for
        both botocore `ClientError` and the fakes/moto tests use — no hard botocore import."""
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as exc:  # noqa: BLE001 — re-raise anything that isn't a clean 404
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                raise
            err_code = response.get("Error", {}).get("Code")
            http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if err_code in ("404", "NoSuchKey", "NotFound") or http_status == 404:
                return False
            raise
