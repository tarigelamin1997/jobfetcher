"""`S3ConfigStore` — reads a config YAML from S3 at runtime (ADR-0022), so a user changes
their settings by editing the YAML + `scripts/push_config.py` — no Lambda rebuild/redeploy.

Mirrors `S3RawStore`: the bucket comes from `$JOBFETCHER_DATA_BUCKET` (no default; a clear
error if unset), boto3 uses the ambient IAM identity (no secrets), tests inject a moto client.

`read_config_text(location)` is the dispatch the handler calls: an `s3://bucket/key` URI reads
from S3; anything else is a local file path (tests + local dev keep working unchanged).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_BUCKET_ENV = "JOBFETCHER_DATA_BUCKET"
_S3_SCHEME = "s3://"


class ConfigNotFound(Exception):
    """A config object was expected in S3 but is absent — a clear, actionable failure
    (never a silent fallback to an empty config)."""


class S3ConfigStore:
    """Reads config objects from S3 (boto3 `get_object`). One client, reused."""

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

    def get_text(self, key: str) -> str:
        """Return the object body at `key` as UTF-8 text. A missing object raises
        `ConfigNotFound` with the actionable next step; any other client error propagates."""
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 — turn a clean 404 into ConfigNotFound, re-raise the rest
            response = getattr(exc, "response", None)
            if isinstance(response, dict):
                err_code = response.get("Error", {}).get("Code")
                http_status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if err_code in ("404", "NoSuchKey", "NotFound") or http_status == 404:
                    raise ConfigNotFound(
                        f"config missing at s3://{self._bucket}/{key} "
                        "— upload it with `python scripts/push_config.py`"
                    ) from exc
            raise
        return resp["Body"].read().decode("utf-8")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split an `s3://bucket/key` URI into `(bucket, key)`. Raises `ValueError` on a malformed
    URI (no bucket or no key) — a clear misconfig, never a silent empty key."""
    rest = uri[len(_S3_SCHEME):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"malformed S3 URI (need s3://bucket/key): {uri!r}")
    return bucket, key


def read_config_text(location: str, *, client: Any = None) -> str:
    """Read a config YAML's text from `location` — an `s3://bucket/key` URI (runtime, ADR-0022)
    or a local file path (tests + local dev). The dispatch that decouples config from the
    deploy package: the Lambda points its `$SEARCH_CONFIG_PATH`/`$PROFILE_PATH` at `s3://…`."""
    if location.startswith(_S3_SCHEME):
        bucket, key = parse_s3_uri(location)
        return S3ConfigStore(bucket=bucket, client=client).get_text(key)
    p = Path(location)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {p}")
    return p.read_text(encoding="utf-8")
