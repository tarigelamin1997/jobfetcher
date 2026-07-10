"""Isolated `S3ReportStore` unit tests (no AWS, no moto): the unset-bucket clear error, the
`put_report` object shape, and `presign` delegating to `generate_presigned_url`. Each carries a
negative. Mirrors `test_s3_raw.py`."""
from __future__ import annotations

from typing import Any

import pytest

from jobfetcher.adapters.s3_reports import _BUCKET_ENV, S3ReportStore


class _FakeS3:
    """Captures `put_object` + `generate_presigned_url` calls."""

    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []
        self.presigns: list[dict[str, Any]] = []

    def put_object(self, **kw: Any) -> dict:
        self.puts.append(kw)
        return {}

    def generate_presigned_url(self, op: str, *, Params: dict, ExpiresIn: int) -> str:  # noqa: N803
        self.presigns.append({"op": op, "Params": Params, "ExpiresIn": ExpiresIn})
        return f"https://signed.example.com/{Params['Key']}?e={ExpiresIn}"


def test_unset_bucket_raises_clear_error(monkeypatch):
    # negative: no bucket env and no explicit bucket → a clear, actionable error.
    monkeypatch.delenv(_BUCKET_ENV, raising=False)
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3ReportStore()


def test_blank_bucket_env_raises(monkeypatch):
    monkeypatch.setenv(_BUCKET_ENV, "   ")
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3ReportStore()


def test_put_report_writes_html_object():
    client = _FakeS3()
    store = S3ReportStore(bucket="b", client=client)
    store.put_report(html="<html>hi</html>", key="reports/2026-07-10/jobs-r.html")
    assert len(client.puts) == 1
    call = client.puts[0]
    assert call["Bucket"] == "b"
    assert call["Key"] == "reports/2026-07-10/jobs-r.html"
    assert call["Body"] == b"<html>hi</html>"
    assert call["ContentType"] == "text/html; charset=utf-8"


def test_presign_delegates_to_generate_presigned_url():
    client = _FakeS3()
    store = S3ReportStore(bucket="b", client=client)
    url = store.presign(key="reports/2026-07-10/jobs-r.html", expires=3600)
    assert url == "https://signed.example.com/reports/2026-07-10/jobs-r.html?e=3600"
    assert len(client.presigns) == 1
    call = client.presigns[0]
    assert call["op"] == "get_object"
    assert call["Params"] == {"Bucket": "b", "Key": "reports/2026-07-10/jobs-r.html"}
    assert call["ExpiresIn"] == 3600
