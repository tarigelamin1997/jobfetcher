"""Ingest orchestration unit tests with in-memory fakes (no net / AWS / DB): fetch_to_bronze
landing + dedup-by-id, land_silver happy path + fingerprint + dissection-skip, and the
end-to-end ingest summary. Each carries a negative."""
from __future__ import annotations

import json
from typing import Any

from jobfetcher.core.dissector import Dissector, DissectionError
from jobfetcher.core.ingest import fetch_to_bronze, ingest, land_silver
from jobfetcher.core.ports import LlmError
from jobfetcher.core.search_spec import SearchSpec
from tests.helpers import CANNED_LLM_JSON, FakeLlm


# --------------------------------------------------------------------------- fakes
class FakeRawStore:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.put_calls: list[str] = []  # source_job_ids actually put (for idempotency asserts)
        self.payloads: list[dict] = []

    def put_raw(self, *, source, source_job_id, payload, run_date=None) -> str:
        key = f"raw/{source}/2026-06-27/{source_job_id}.json"
        self.keys.append(key)
        self.put_calls.append(source_job_id)
        self.payloads.append(payload)
        return key


class FakeRepo:
    """A minimal in-memory `Repository`: bronze is idempotent on bronze_id; postings keyed."""

    def __init__(self) -> None:
        self.bronze: dict[str, dict] = {}
        self.postings: dict[str, dict] = {}

    def upsert_bronze(self, *, bronze_id, source, source_job_id, raw_payload, run_id,
                      s3_raw_key=None) -> str:
        self.bronze.setdefault(bronze_id, {"s3_raw_key": s3_raw_key, "run_id": run_id})
        return bronze_id

    def save_posting(self, dissected, *, posting_id, fingerprint=None, **kw) -> str:
        self.postings[posting_id] = {"dissected": dissected, "fingerprint": fingerprint, **kw}
        return posting_id

    def get_posting(self, posting_id):
        rec = self.postings.get(posting_id)
        return rec["dissected"] if rec else None


class FakeSource:
    def __init__(self, jobs: list[dict[str, Any]]) -> None:
        self._jobs = jobs

    def fetch(self, spec, *, run_id):
        yield from self._jobs


def _spec() -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch", "secret_name": "s", "aws_region": "us-east-1",
            "targeting": {"job_titles": ["de"], "countries": ["sa"], "cities": [], "states": []},
            "date_posted": "week", "language": "en", "employment_types": [],
            "remote": "off", "threshold": 60, "hard_floor": 50, "near_miss_band": 10,
            "reassess_max_age_days": 45, "digest_max_age_days": 90,
            "budget": {"max_pages_per_query": 1, "request_budget_per_run": 10},
        }
    )


def _job(jid: str) -> dict:
    return {
        "job_id": jid,
        "job_title": "Senior Data Engineer",
        "job_description": (
            "Required: 3+ years with Python and SQL. Experience with Airflow is a plus. "
            "You will build ETL pipelines on AWS."
        ),
        "employer_name": "Acme",
        "job_apply_link": "https://x/apply",
        "job_location": "Riyadh",
        "job_city": "Riyadh",
        "job_country": "SA",
        "job_employment_type": "FULLTIME",
        "job_state": None,
    }


def _dissector() -> Dissector:
    return Dissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model")


# --------------------------------------------------------------------------- fetch_to_bronze
def test_fetch_to_bronze_lands_and_dedups():
    # C2: "a" appears twice (as it would across two title×country queries) → landed ONCE,
    # one S3 put, one bronze row — no wasted re-land / re-dissect downstream.
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a"), _job("b"), _job("a")])
    landed = fetch_to_bronze(
        _spec(), run_id="r", source="jsearch", source_adapter=src, raw_store=store, repo=repo
    )
    assert [bid for bid, _, _ in landed] == ["jsearch:a", "jsearch:b"]  # "a" deduped within run
    assert set(repo.bronze) == {"jsearch:a", "jsearch:b"}
    assert store.put_calls == ["a", "b"]  # exactly one put per distinct source id
    assert repo.bronze["jsearch:a"]["s3_raw_key"] == "raw/jsearch/2026-06-27/a.json"


def test_fetch_to_bronze_skips_jobs_without_id():
    # negative: a payload with no job_id can't form a stable bronze_id → skipped, not crashed.
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([{"job_title": "no id"}, _job("ok")])
    landed = fetch_to_bronze(
        _spec(), run_id="r", source="jsearch", source_adapter=src, raw_store=store, repo=repo
    )
    assert [bid for bid, _, _ in landed] == ["jsearch:ok"]
    assert set(repo.bronze) == {"jsearch:ok"}


def test_fetch_to_bronze_threads_query_country():
    # C3: the authoritative *query* country is carried through to the landed triple and the
    # transient side-channel key is popped off the persisted raw payload.
    from jobfetcher.adapters.jsearch_source import QUERY_COUNTRY_KEY

    repo, store = FakeRepo(), FakeRawStore()
    job = {**_job("qc"), QUERY_COUNTRY_KEY: "ae"}  # adapter would attach this; raw says SA
    landed = fetch_to_bronze(
        _spec(), run_id="r", source="jsearch", source_adapter=FakeSource([job]),
        raw_store=store, repo=repo,
    )
    (_bid, raw, query_country) = landed[0]
    assert query_country == "ae"
    assert QUERY_COUNTRY_KEY not in raw  # popped before persisting — stored raw is untouched
    assert QUERY_COUNTRY_KEY not in store.payloads[0]


# --------------------------------------------------------------------------- land_silver
def test_land_silver_writes_posting_with_fingerprint():
    repo = FakeRepo()
    pid = land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_dissector(), repo=repo,
    )
    assert pid == "jsearch:a"
    rec = repo.postings["jsearch:a"]
    assert rec["fingerprint"] and len(rec["fingerprint"]) == 16
    assert rec["company"] == "Acme" and rec["apply_url"] == "https://x/apply"
    assert {s.name for s in rec["dissected"].skills} >= {"Python"}


def test_fingerprint_is_independent_of_llm_normalized_title():
    # C1: the dedup key must be stable across model versions — it is computed from the RAW
    # source title (+ company + location), NOT the LLM's `normalized_title`. Two runs whose
    # FakeLLM emits *different* normalized_titles for the SAME raw posting fingerprint alike.
    from jobfetcher.core.fingerprint import fingerprint

    def _llm_with_title(norm_title: str) -> Dissector:
        reply = json.dumps(
            {"skills": [], "sector": None, "normalized_title": norm_title}
        )
        return Dissector(FakeLlm(reply), model_id="test-model")

    repo_a, repo_b = FakeRepo(), FakeRepo()
    land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_llm_with_title("Data Engineer"), repo=repo_a,
    )
    land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_llm_with_title("Senior Cloud Data Platform Engineer (Big Data)"),
        repo=repo_b,
    )
    fp_a = repo_a.postings["jsearch:a"]["fingerprint"]
    fp_b = repo_b.postings["jsearch:a"]["fingerprint"]
    assert fp_a == fp_b  # model output varied; the dedup key did not
    # and it really is the raw-title fingerprint, not the normalized one
    assert fp_a == fingerprint("Senior Data Engineer", "Acme", "Riyadh")


def test_land_silver_uses_query_country_over_raw():
    # C3: a job whose raw job_country (SA) differs from the queried country (AE) → the silver
    # posting records the AUTHORITATIVE query country.
    repo = FakeRepo()
    land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_dissector(), repo=repo, query_country="ae",
    )
    assert repo.postings["jsearch:a"]["dissected"].country == "ae"  # not the raw "SA"


def test_land_silver_records_spec_language():
    # S2: the posting language comes from the spec, not a hardcoded "en".
    repo = FakeRepo()
    pid = land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_dissector(), repo=repo, language="ar",
    )
    assert pid == "jsearch:a"
    # the spec language flowed all the way into the saved silver posting
    assert repo.postings["jsearch:a"]["dissected"].language == "ar"


def test_land_silver_skips_on_dissection_error():
    # negative: a dissection failure → None (logged + skipped), no posting row, run survives.
    repo = FakeRepo()

    class _D(Dissector):
        def dissect(self, jd_text, metadata):
            raise DissectionError("forced")

    pid = land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_D(FakeLlm()), repo=repo,
    )
    assert pid is None
    assert repo.postings == {}


# --------------------------------------------------------------------------- H-2 concurrency
def test_ingest_dissects_concurrently():
    """H-2 behavioral proof: 12 postings × a 0.15s dissect on 4 workers must beat the serial
    wall-clock (~1.8s) by a wide margin — if the pool were secretly serial this fails."""
    import time as _time

    repo, store = FakeRepo(), FakeRawStore()

    class _SlowDissector(Dissector):
        def dissect(self, jd_text, metadata):
            _time.sleep(0.15)
            return super().dissect(jd_text, metadata)

    jobs = [_job(f"j{i}") for i in range(12)]
    t0 = _time.monotonic()
    summary = ingest(
        _spec(), run_id="r", source_adapter=FakeSource(jobs), raw_store=store, repo=repo,
        dissector=_SlowDissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model"),
        max_workers=4,
    )
    elapsed = _time.monotonic() - t0
    assert summary["silvered"] == 12 and summary["deferred"] == 0
    assert len(repo.postings) == 12  # every result was saved (main-thread writes)
    assert elapsed < 1.2, f"expected concurrent (<1.2s), got {elapsed:.2f}s (serial ~1.8s)"


def test_ingest_defers_on_expired_deadline():
    """H-2 negative: a deadline that is already past → NO dissection starts (zero LLM calls),
    everything is counted `deferred`, and the run returns cleanly instead of timing out."""
    from jobfetcher.core.ingest import Deadline

    repo, store = FakeRepo(), FakeRawStore()

    class _CountingDissector(Dissector):
        calls = 0

        def dissect(self, jd_text, metadata):
            type(self).calls += 1
            return super().dissect(jd_text, metadata)

    summary = ingest(
        _spec(), run_id="r", source_adapter=FakeSource([_job("a"), _job("b")]),
        raw_store=store, repo=repo,
        dissector=_CountingDissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model"),
        deadline=Deadline(0),  # expired immediately
    )
    assert summary["deferred"] == 2 and summary["silvered"] == 0
    assert _CountingDissector.calls == 0  # no LLM work started past the deadline
    assert summary["bronzed"] == 2  # bronze still landed — only the LLM half is deferred


def test_land_silver_skips_on_llm_error():
    # ERR-006 negative: a provider-level LlmError (a 503 that outlived the client retries)
    # must be isolated exactly like a DissectionError — skip the posting, never crash the
    # run. (Before H-1 this propagated and killed the whole pipeline — seen live.)
    repo = FakeRepo()

    class _D(Dissector):
        def dissect(self, jd_text, metadata):
            raise LlmError("HTTP 503: service busy")

    pid = land_silver(
        "jsearch:a", _job("a"), run_id="r", source="jsearch", source_job_id="a",
        dissector=_D(FakeLlm()), repo=repo,
    )
    assert pid is None
    assert repo.postings == {}


# --------------------------------------------------------------------------- ingest end-to-end
def test_ingest_end_to_end_summary():
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a"), _job("b")])
    summary = ingest(
        _spec(), run_id="r", source_adapter=src, raw_store=store, repo=repo,
        dissector=_dissector(),
    )
    assert summary == {"fetched": 2, "bronzed": 2, "silvered": 2, "skipped": 0, "already": 0, "deferred": 0}
    assert set(repo.postings) == {"jsearch:a", "jsearch:b"}


def test_ingest_counts_dissection_skips():
    # negative: a dissector that always fails → everything skipped, nothing silvered, no crash.
    repo, store = FakeRepo(), FakeRawStore()

    class _D(Dissector):
        def dissect(self, jd_text, metadata):
            raise DissectionError("always")

    summary = ingest(
        _spec(), run_id="r", source_adapter=FakeSource([_job("a")]),
        raw_store=store, repo=repo, dissector=_D(FakeLlm()),
    )
    assert summary == {"fetched": 1, "bronzed": 1, "silvered": 0, "skipped": 1, "already": 0, "deferred": 0}


def test_ingest_rerun_does_not_redissect_existing_posting():
    # C2: a second run over an already-silvered posting must NOT call the LLM again — it is
    # counted as `already`, with zero new dissect calls (no wasted LLM cost on a re-run).
    repo, store = FakeRepo(), FakeRawStore()

    class _CountingDissector(Dissector):
        def __init__(self) -> None:
            super().__init__(FakeLlm(CANNED_LLM_JSON), model_id="test-model")
            self.calls = 0

        def dissect(self, jd_text, metadata):
            self.calls += 1
            return super().dissect(jd_text, metadata)

    dissector = _CountingDissector()
    first = ingest(
        _spec(), run_id="r1", source_adapter=FakeSource([_job("a")]),
        raw_store=store, repo=repo, dissector=dissector,
    )
    assert first == {"fetched": 1, "bronzed": 1, "silvered": 1, "skipped": 0, "already": 0, "deferred": 0}
    assert dissector.calls == 1

    second = ingest(
        _spec(), run_id="r2", source_adapter=FakeSource([_job("a")]),
        raw_store=store, repo=repo, dissector=dissector,
    )
    assert second == {"fetched": 1, "bronzed": 1, "silvered": 0, "skipped": 0, "already": 1, "deferred": 0}
    assert dissector.calls == 1  # NOT re-dissected on the re-run
