"""Isolated `S3AuditStore` unit tests (no AWS, no moto): the unset-bucket clear error, the key
layout per stage, the batched-JSONL body shape, the run-summary object, the empty-batch no-op, and
— the load-bearing guarantee — that ANY `put_object` failure is swallowed (logged, returns None,
never propagates), so an audit write can never fail a pipeline run. Mirrors `test_s3_reports.py`."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from jobfetcher.adapters.s3_audit import _BUCKET_ENV, S3AuditStore

RUN_DATE = date(2026, 7, 11)


class _FakeS3:
    """Captures `put_object` calls."""

    def __init__(self) -> None:
        self.puts: list[dict[str, Any]] = []

    def put_object(self, **kw: Any) -> dict:
        self.puts.append(kw)
        return {}


class _ExplodingS3:
    """Every `put_object` raises — models a transient S3 failure the audit must NOT propagate."""

    def put_object(self, **kw: Any) -> dict:
        raise RuntimeError("injected S3 failure")


def _store(client: Any) -> S3AuditStore:
    return S3AuditStore(run_id="r1", run_date=RUN_DATE, bucket="b", client=client)


# --------------------------------------------------------------------------- construction
def test_unset_bucket_raises_clear_error(monkeypatch):
    # negative: no bucket env and no explicit bucket → a clear, actionable error.
    monkeypatch.delenv(_BUCKET_ENV, raising=False)
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3AuditStore(run_id="r1", run_date=RUN_DATE)


def test_blank_bucket_env_raises(monkeypatch):
    monkeypatch.setenv(_BUCKET_ENV, "   ")
    with pytest.raises(ValueError, match=_BUCKET_ENV):
        S3AuditStore(run_id="r1", run_date=RUN_DATE)


# --------------------------------------------------------------------------- key layout + body
def test_put_silver_key_layout_and_jsonl_body():
    client = _FakeS3()
    key = _store(client).put_silver([{"posting_id": "jsearch:1"}, {"posting_id": "jsearch:2"}])
    assert key == "silver/2026-07-11/r1.jsonl"
    assert len(client.puts) == 1
    call = client.puts[0]
    assert call["Bucket"] == "b"
    assert call["Key"] == "silver/2026-07-11/r1.jsonl"
    assert call["ContentType"] == "application/x-ndjson"
    # body is newline-delimited JSON — one valid JSON object per line, N lines for N records
    lines = call["Body"].decode("utf-8").split("\n")
    assert len(lines) == 2
    assert [json.loads(line)["posting_id"] for line in lines] == ["jsearch:1", "jsearch:2"]


def test_put_gold_and_scores_key_layout():
    client = _FakeS3()
    store = _store(client)
    assert store.put_gold([{"posting_id": "p"}]) == "gold/2026-07-11/r1.jsonl"
    assert store.put_scores([{"posting_id": "p"}]) == "scores/2026-07-11/r1.jsonl"
    assert [c["Key"] for c in client.puts] == [
        "gold/2026-07-11/r1.jsonl", "scores/2026-07-11/r1.jsonl"
    ]


def test_put_run_summary_writes_indented_json_object():
    client = _FakeS3()
    summary = {"statusCode": 200, "run_id": "r1", "score": {"scored": 3}}
    key = _store(client).put_run_summary(summary)
    assert key == "runs/2026-07-11/r1.json"
    call = client.puts[0]
    assert call["Key"] == "runs/2026-07-11/r1.json"
    assert call["ContentType"] == "application/json"
    assert json.loads(call["Body"].decode("utf-8")) == summary  # round-trips


def test_empty_batch_writes_nothing():
    # an empty stage → NO object (the run summary records the zero; an empty object would lie).
    client = _FakeS3()
    store = _store(client)
    assert store.put_silver([]) is None
    assert store.put_gold([]) is None
    assert store.put_scores([]) is None
    assert client.puts == []


# --------------------------------------------------------------------------- the non-fatal guard
def test_put_failure_is_swallowed_not_propagated(caplog):
    # THE guarantee: a put_object that raises must NOT propagate — it logs + returns None, so an
    # audit write can never fail the pipeline run. Covers the JSONL path and the run-summary path.
    store = _store(_ExplodingS3())
    assert store.put_silver([{"a": 1}]) is None
    assert store.put_scores([{"a": 1}]) is None
    assert store.put_run_summary({"statusCode": 500}) is None
    # and it logged a warning for each (observability without failure)
    assert sum("audit write skipped" in r.message for r in caplog.records) >= 1


def test_serialization_failure_is_swallowed():
    # serialization is INSIDE the guard: a record json.dumps can't encode (a circular ref, which
    # even default=str can't rescue) must NOT propagate — logged, returns None, nothing written.
    client = _FakeS3()
    store = _store(client)
    circular: dict[str, Any] = {}
    circular["self"] = circular
    assert store.put_scores([circular]) is None
    assert client.puts == []  # never reached put_object; the run is unaffected


def test_non_json_native_values_serialize_via_default():
    # ScoreResult/DissectedPosting dumps use model_dump(mode="json"), but default=str is the
    # belt-and-suspenders for any stray non-JSON-native value — it must not raise.
    client = _FakeS3()
    key = _store(client).put_scores([{"posting_id": "p", "when": date(2026, 7, 11)}])
    assert key is not None
    line = json.loads(client.puts[0]["Body"].decode("utf-8"))
    assert line["when"] == "2026-07-11"
