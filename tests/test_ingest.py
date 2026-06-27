"""Ingest orchestration unit tests with in-memory fakes (no net / AWS / DB): fetch_to_bronze
landing + dedup-by-id, land_silver happy path + fingerprint + dissection-skip, and the
end-to-end ingest summary. Each carries a negative."""
from __future__ import annotations

from typing import Any

from jobfetcher.core.dissector import Dissector, DissectionError
from jobfetcher.core.ingest import fetch_to_bronze, ingest, land_silver
from jobfetcher.core.search_spec import SearchSpec
from tests.helpers import CANNED_LLM_JSON, FakeLlm


# --------------------------------------------------------------------------- fakes
class FakeRawStore:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put_raw(self, *, source, source_job_id, payload, run_date=None) -> str:
        key = f"raw/{source}/2026-06-27/{source_job_id}.json"
        self.keys.append(key)
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
            "remote": "off", "threshold": 60,
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
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a"), _job("b"), _job("a")])  # "a" twice → exact-id dedup
    landed = fetch_to_bronze(
        _spec(), run_id="r", source="jsearch", source_adapter=src, raw_store=store, repo=repo
    )
    assert len(landed) == 3  # all yielded pairs returned
    assert set(repo.bronze) == {"jsearch:a", "jsearch:b"}  # but only 2 distinct bronze rows
    assert repo.bronze["jsearch:a"]["s3_raw_key"] == "raw/jsearch/2026-06-27/a.json"


def test_fetch_to_bronze_skips_jobs_without_id():
    # negative: a payload with no job_id can't form a stable bronze_id → skipped, not crashed.
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([{"job_title": "no id"}, _job("ok")])
    landed = fetch_to_bronze(
        _spec(), run_id="r", source="jsearch", source_adapter=src, raw_store=store, repo=repo
    )
    assert [bid for bid, _ in landed] == ["jsearch:ok"]
    assert set(repo.bronze) == {"jsearch:ok"}


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


# --------------------------------------------------------------------------- ingest end-to-end
def test_ingest_end_to_end_summary():
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a"), _job("b")])
    summary = ingest(
        _spec(), run_id="r", source_adapter=src, raw_store=store, repo=repo,
        dissector=_dissector(),
    )
    assert summary == {"fetched": 2, "bronzed": 2, "silvered": 2, "skipped": 0}
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
    assert summary == {"fetched": 1, "bronzed": 1, "silvered": 0, "skipped": 1}
