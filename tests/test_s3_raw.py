"""Isolated `S3RawStore` unit tests (no AWS, no moto): the unset-bucket clear error and the
deterministic key construction (with a fake client capturing the put). Each carries a negative."""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from jobfetcher.adapters.s3_raw import _BUCKET_ENV, S3RawStore


class _NotFound(Exception):
    """Mimics a botocore ClientError 404 shape (the `response` dict is what `_exists` reads)."""

    def __init__(self) -> None:
        super().__init__("Not Found")
        self.response = {
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }


class _FakeS3:
    """Captures `put_object` calls and models existence so idempotency can be asserted: a key
    is 'present' once it has been put (`head_object` raises a 404 shape until then)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._keys: set[str] = set()

    def put_object(self, **kw) -> dict:
        self.calls.append(kw)
        self._keys.add(kw["Key"])
        return {}

    def head_object(self, *, Bucket, Key) -> dict:  # noqa: N803 — boto3 kwarg names
        if Key not in self._keys:
            raise _NotFound()
        return {}


def test_unset_bucket_raises_clear_error(monkeypatch):
    # negative (M3): no bucket env and no explicit bucket → a clear, actionable error.
    monkeypatch.delenv(_BUCKET_ENV, raising=False)
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3RawStore()


def test_blank_bucket_env_raises(monkeypatch):
    # negative: a whitespace-only env value is still "no bucket".
    monkeypatch.setenv(_BUCKET_ENV, "   ")
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3RawStore()


def test_put_raw_builds_deterministic_key():
    client = _FakeS3()
    store = S3RawStore(bucket="b", client=client)
    key = store.put_raw(
        source="jsearch",
        source_job_id="abc123",
        payload={"job_id": "abc123"},
        run_date=date(2026, 6, 27),
    )
    assert key == "raw/jsearch/2026-06-27/abc123.json"
    assert client.calls[0]["Bucket"] == "b"
    assert client.calls[0]["Key"] == key
    assert client.calls[0]["ContentType"] == "application/json"


def test_put_raw_sanitizes_slash_in_id():
    # M1: a `/` in the source id must NOT nest S3 prefixes — it is percent-encoded flat.
    client = _FakeS3()
    store = S3RawStore(bucket="b", client=client)
    key = store.put_raw(
        source="jsearch",
        source_job_id="abc/def",
        payload={},
        run_date=date(2026, 6, 27),
    )
    assert key == "raw/jsearch/2026-06-27/abc%2Fdef.json"  # one flat object, no nested prefix
    assert "abc/def" not in key.rsplit("/", 1)[-1]


def test_put_raw_is_idempotent_does_not_overwrite():
    # C4: bronze is an immutable snapshot — re-landing the same id (existing object) must NOT
    # issue a second overwriting put. The existence check short-circuits it.
    client = _FakeS3()
    store = S3RawStore(bucket="b", client=client)
    args = {"source": "jsearch", "source_job_id": "dup", "payload": {"v": 1},
            "run_date": date(2026, 6, 27)}
    k1 = store.put_raw(**args)
    k2 = store.put_raw(**{**args, "payload": {"v": 2}})  # different body, same key
    assert k1 == k2
    assert len(client.calls) == 1  # exactly one put — the second was skipped
    assert client.calls[0]["Body"] == _json_bytes({"v": 1})  # original snapshot preserved


def test_put_raw_propagates_non_404_head_error():
    # negative: an ambiguous head_object failure (not a clean 404) must NOT be swallowed —
    # we don't silently overwrite on an unknown error.
    class _Boom(_FakeS3):
        def head_object(self, *, Bucket, Key):  # noqa: N803
            raise RuntimeError("network gone")

    store = S3RawStore(bucket="b", client=_Boom())
    with pytest.raises(RuntimeError, match="network gone"):
        store.put_raw(source="jsearch", source_job_id="x", payload={},
                      run_date=date(2026, 6, 27))


def _json_bytes(obj) -> bytes:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
