"""`S3ReportStore` — uploads the full-list HTML report (B-1) to S3 and mints a presigned GET URL
so the daily digest can carry an https link to the real data. Mirrors `S3RawStore`: the bucket
comes from `$JOBFETCHER_DATA_BUCKET` (no default — a clear error if unset), boto3 uses the ambient
IAM identity (no secrets here), tests inject a moto/mock client.

Known constraint (ACCEPTED — do not fight it): a URL signed with the Lambda role's *temporary*
credentials is capped at the session-token TTL (hours, not days). That is fine for a daily email
— same-day reachability; tomorrow's digest regenerates the link. The caller requests a sane
expiry; boto3/STS caps it as needed.
"""
from __future__ import annotations

import os
from typing import Any, Protocol

_BUCKET_ENV = "JOBFETCHER_DATA_BUCKET"
_REPORT_CONTENT_TYPE = "text/html; charset=utf-8"


class ReportStore(Protocol):
    """Uploads one HTML report object and mints a presigned GET URL for it (the core depends on
    this port, not boto3 — ADR-0015)."""

    def put_report(self, *, html: str, key: str) -> None:
        ...

    def presign(self, *, key: str, expires: int) -> str:
        ...


class S3ReportStore:
    """`ReportStore` over S3 (boto3 `put_object` + `generate_presigned_url`). One client, reused."""

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

    def put_report(self, *, html: str, key: str) -> None:
        """Write `html` to `key` as a UTF-8 `text/html` object. Unlike immutable bronze a report
        is a **regenerated daily snapshot** — a re-run for the same date overwrites, so there is
        no existence check."""
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=html.encode("utf-8"),
            ContentType=_REPORT_CONTENT_TYPE,
        )

    def presign(self, *, key: str, expires: int) -> str:
        """Return a presigned GET URL for `key`, valid for up to `expires` seconds (capped by the
        signing credential's TTL — see the module note)."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )
