"""Ingest orchestration unit tests with in-memory fakes (no net / AWS / DB): fetch_to_bronze
landing + dedup-by-id, land_silver happy path + fingerprint + dissection-skip, and the
end-to-end ingest summary. Each carries a negative."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from jobfetcher.core.dissector import Dissector, DissectionError
from jobfetcher.core.ingest import fetch_to_bronze, ingest, land_silver
from jobfetcher.core.ports import LlmBillingError, LlmError
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
    # `fetch_stopped: None` = the sweep worked through its whole query matrix, so these
    # counts are the day's real supply rather than a floor (INV-003).
    assert summary == {"fetched": 2, "bronzed": 2, "silvered": 2, "skipped": 0, "already": 0, "deferred": 0, "billing_blocked": 0, "fetch_stopped": None, "fetch_failed_queries": 0}
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
    assert summary == {"fetched": 1, "bronzed": 1, "silvered": 0, "skipped": 1, "already": 0, "deferred": 0, "billing_blocked": 0, "fetch_stopped": None, "fetch_failed_queries": 0}


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
    assert first == {"fetched": 1, "bronzed": 1, "silvered": 1, "skipped": 0, "already": 0, "deferred": 0, "billing_blocked": 0, "fetch_stopped": None, "fetch_failed_queries": 0}
    assert dissector.calls == 1

    second = ingest(
        _spec(), run_id="r2", source_adapter=FakeSource([_job("a")]),
        raw_store=store, repo=repo, dissector=dissector,
    )
    assert second == {"fetched": 1, "bronzed": 1, "silvered": 0, "skipped": 0, "already": 1, "deferred": 0, "billing_blocked": 0, "fetch_stopped": None, "fetch_failed_queries": 0}
    assert dissector.calls == 1  # NOT re-dissected on the re-run


def test_ingest_counts_billing_failures_separately_from_skips(caplog):
    """ERR-010: an empty provider account is NOT a per-item dissection failure.

    It hits every posting identically, so it is counted on its own axis and logged ONCE with
    the total and the operator action. Folding it into `skipped` is what let 137 identical
    per-item warnings hide "the account has no money" for 38 days — the run summary read
    `{"fetched": 137, "silvered": 0, "skipped": 137}`, which is indistinguishable from a bad
    prompt or a flaky provider.
    """
    repo, store = FakeRepo(), FakeRawStore()

    class _Broke(Dissector):
        def dissect(self, jd_text, metadata):
            raise LlmBillingError("402 Payment Required: Insufficient Balance")

    with caplog.at_level("WARNING"):
        summary = ingest(
            _spec(), run_id="r", source_adapter=FakeSource([_job("a"), _job("b")]),
            raw_store=store, repo=repo, dissector=_Broke(FakeLlm()),
        )

    assert summary == {
        "fetched": 2, "bronzed": 2, "silvered": 0, "skipped": 0, "already": 0,
        "deferred": 0, "billing_blocked": 2, "fetch_stopped": None,
        "fetch_failed_queries": 0,
    }
    # exactly one line for two blocked postings, and it names the fix
    billing_lines = [r for r in caplog.records if "OUT OF CREDIT" in r.getMessage()]
    assert len(billing_lines) == 1
    assert "Top up" in billing_lines[0].getMessage()


def test_ingest_billing_failure_does_not_crash_the_run():
    # negative twin: the run still returns a summary (isolated, not run-fatal) — the pipeline
    # keeps its "one bad provider never kills the run" contract.
    repo, store = FakeRepo(), FakeRawStore()

    class _Broke(Dissector):
        def dissect(self, jd_text, metadata):
            raise LlmBillingError("402")

    summary = ingest(
        _spec(), run_id="r", source_adapter=FakeSource([_job("a")]),
        raw_store=store, repo=repo, dissector=_Broke(FakeLlm()),
    )
    assert summary["billing_blocked"] == 1
    assert repo.postings == {}  # nothing landed in silver


# ------------------------------------------------- INV-003: the summary must say WHY zero
# `runs/{date}/{run_id}.json` is the durable record an operator reads days later. It must
# distinguish "the source had nothing" from "we were cut off" — three days of green,
# empty runs went unnoticed in Sept 2026 precisely because it could not.


class StoppedSource(FakeSource):
    """A source that yields what it has and then reports it stopped early."""

    def __init__(self, jobs, reason):
        super().__init__(jobs)
        self.last_stop_reason = reason


def test_ingest_summary_records_why_the_fetch_stopped(caplog):
    repo, store = FakeRepo(), FakeRawStore()
    src = StoppedSource([], "rate_limited")
    with caplog.at_level("WARNING"):
        summary = ingest(
            _spec(), run_id="r", source_adapter=src, raw_store=store, repo=repo,
            dissector=_dissector(),
        )
    assert summary["fetched"] == 0
    assert summary["fetch_stopped"] == "rate_limited"
    assert "stopped early" in caplog.text and "rate_limited" in caplog.text


def test_ingest_summary_distinguishes_cut_off_from_genuinely_empty():
    # THE GATE. Both runs fetch zero. Only the run summary can tell them apart — and it must,
    # from the persisted JSON alone, with no log access and no live system.
    repo, store = FakeRepo(), FakeRawStore()
    cut_off = ingest(
        _spec(), run_id="r1", source_adapter=StoppedSource([], "rate_limited"),
        raw_store=store, repo=repo, dissector=_dissector(),
    )
    quiet = ingest(
        _spec(), run_id="r2", source_adapter=FakeSource([]),
        raw_store=FakeRawStore(), repo=FakeRepo(), dissector=_dissector(),
    )
    assert cut_off["fetched"] == quiet["fetched"] == 0      # identical on the old contract...
    assert cut_off != quiet                                  # ...and distinguishable now
    assert cut_off["fetch_stopped"] == "rate_limited"
    assert quiet["fetch_stopped"] is None


class LazyStoppedSource(FakeSource):
    """A source shaped like the REAL adapter: `fetch()` is a generator, so `last_stop_reason` is
    set *during* iteration, not at construction.

    `StoppedSource` above sets the reason in `__init__`, which makes every test using it blind to
    the one ordering bug this design can have — moving `ingest`'s read of `last_stop_reason` to
    before the sweep still passes. Verified by mutation: with the read hoisted above
    `fetch_to_bronze`, every `StoppedSource` test stayed green while a real 429 would report
    `fetch_stopped: None` in production."""

    def __init__(self, jobs, reason):
        super().__init__(jobs)
        self._reason = reason
        self.last_stop_reason = None

    def fetch(self, spec, *, run_id):
        self.last_stop_reason = None  # reset per sweep, exactly as the real adapter does
        yield from list(super().fetch(spec, run_id=run_id))
        self.last_stop_reason = self._reason  # ...and only decided once the sweep is over


def test_the_stop_reason_is_read_AFTER_the_sweep_not_before():
    # THE ORDERING GATE. `fetch()` is a generator: the reason does not exist until the sweep has
    # run. If `ingest` reads it too early it gets None, and a quota-dead run reports itself as a
    # quiet one — ERR-017 all over again, with a green suite.
    repo, store = FakeRepo(), FakeRawStore()
    summary = ingest(
        _spec(), run_id="r", source_adapter=LazyStoppedSource([_job("a")], "rate_limited"),
        raw_store=store, repo=repo, dissector=_dissector(),
    )
    assert summary["fetched"] == 1
    assert summary["fetch_stopped"] == "rate_limited"


def test_a_matrix_lost_to_upstream_errors_is_not_reported_as_a_quiet_day(caplog):
    # THE BLOCKER (Examiner B1). A 429 and a budget stop were reported; the two OTHER ways a
    # query ends early — a non-429 HTTP error and a transient error — set no reason at all. So a
    # sweep that lost every query to HTTP 503 returned a summary byte-identical to a day the
    # source genuinely had nothing. `fetch_stopped: None` is DEFINED as "the whole matrix was
    # searched"; that made it a lie.
    class PartiallyBrokenSource(FakeSource):
        def __init__(self, jobs, failed):
            super().__init__(jobs)
            self.last_stop_reason = None
            self.last_failed_queries = 0
            self._failed = failed

        def fetch(self, spec, *, run_id):
            self.last_stop_reason = None
            self.last_failed_queries = 0
            yield from list(super().fetch(spec, run_id=run_id))
            self.last_failed_queries = self._failed

    with caplog.at_level("WARNING"):
        broken = ingest(
            _spec(), run_id="r1", source_adapter=PartiallyBrokenSource([], failed=15),
            raw_store=FakeRawStore(), repo=FakeRepo(), dissector=_dissector(),
        )
    quiet = ingest(
        _spec(), run_id="r2", source_adapter=PartiallyBrokenSource([], failed=0),
        raw_store=FakeRawStore(), repo=FakeRepo(), dissector=_dissector(),
    )
    assert broken["fetched"] == quiet["fetched"] == 0   # identical on the old contract...
    assert broken != quiet                              # ...and distinguishable now
    assert broken["fetch_stopped"] == "partial_errors"
    assert broken["fetch_failed_queries"] == 15
    # negative: a sweep that completed cleanly must NOT be tarred with a reason
    assert quiet["fetch_stopped"] is None
    assert quiet["fetch_failed_queries"] == 0
    assert "never searched" in caplog.text


def test_a_skip_day_never_reports_a_previous_sweeps_counters():
    # negative: on a skip day the generator is never created, so the adapter's reset never runs
    # and whatever sits on the instance describes an EARLIER sweep. Reporting that against a run
    # which made zero requests would be the same class of lie the key exists to prevent.
    stale = LazyStoppedSource([], "rate_limited")
    stale.last_stop_reason = "rate_limited"      # left over from a prior run
    stale.last_failed_queries = 9
    summary = ingest(
        _spec(), run_id="r", source_adapter=stale, raw_store=FakeRawStore(),
        repo=FakeRepo(), dissector=_dissector(), run_date=_NON_FETCH_DAY,
    )
    assert summary["fetch_stopped"] == "not_a_fetch_day"
    assert summary["fetch_failed_queries"] == 0


def test_ingest_tolerates_a_source_without_the_attribute():
    # negative: `SourceAdapter` does NOT require `last_stop_reason`. A fake or a future
    # adapter that omits it must not crash the run — it simply reports no reason.
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a")])
    assert not hasattr(src, "last_stop_reason")
    summary = ingest(
        _spec(), run_id="r", source_adapter=src, raw_store=store, repo=repo,
        dissector=_dissector(),
    )
    assert summary["fetched"] == 1 and summary["fetch_stopped"] is None


# ------------------------------------------------ INV-003 / ERR-017: the fetch cadence
# The Lambda runs daily (the dead-man alarm watches a 24h window and cannot be widened);
# the JSearch sweep runs every Nth day because the free tier is 200 requests/MONTH.


def test_is_fetch_day_is_every_nth_day_and_stable_across_month_boundaries():
    from datetime import date, timedelta

    from jobfetcher.core.ingest import is_fetch_day, next_fetch_day

    # exactly one day in three, with no drift at the 31->1 boundary that a day-of-month
    # rule (1,4,7,...,28) would silently stretch into a 4-day gap
    days = [date(2026, 8, 20) + timedelta(days=i) for i in range(40)]
    hits = [d for d in days if is_fetch_day(d)]
    gaps = {(b - a).days for a, b in zip(hits, hits[1:])}
    assert gaps == {3}, f"cadence drifted: {sorted(gaps)}"

    # next_fetch_day is strictly in the future and is itself a fetch day
    for d in days[:5]:
        nxt = next_fetch_day(d)
        assert nxt > d and is_fetch_day(nxt)


def test_is_fetch_day_disabled_means_every_day():
    from datetime import date, timedelta

    from jobfetcher.core.ingest import is_fetch_day

    # negative: n<=1 turns the cadence OFF — every day fetches (the pre-cadence behaviour)
    assert all(is_fetch_day(date(2026, 9, 1) + timedelta(days=i), every_n_days=1) for i in range(7))


def test_next_fetch_day_survives_an_absurd_cadence_instead_of_crashing_the_run():
    # negative (Examiner M1): the previous day-by-day walk raised OverflowError at date.max for
    # any `every_n_days` past the calendar's range — and because logging evaluates its %-args
    # eagerly, that crash reached the caller EVEN WITH LOGGING OFF, turning a nonsense config
    # value into a statusCode:500 and an operator page. A knob nobody should set must not page.
    from datetime import date

    from jobfetcher.core.ingest import next_fetch_day

    assert next_fetch_day(date(2026, 9, 5), every_n_days=4_000_000) is None  # no crash
    # and it still answers correctly in the normal range
    assert next_fetch_day(date(2026, 9, 5), every_n_days=3) == date(2026, 9, 7)
    assert next_fetch_day(date(2026, 9, 7), every_n_days=3) == date(2026, 9, 10)  # strictly after


# `date(2026, 9, 5).toordinal() % 3 == 1` and `% 7 == 6` — a non-fetch day for both cadences.
_NON_FETCH_DAY = date(2026, 9, 5)
_FETCH_DAY = date(2026, 9, 7)  # ordinal % 3 == 0


def test_a_non_fetch_day_never_touches_the_source_and_explains_itself(caplog):
    from jobfetcher.core.ingest import SKIP_NOT_A_FETCH_DAY

    class ExplodingSource:
        def fetch(self, spec, *, run_id):
            raise AssertionError("the source must NOT be called on a non-fetch day")

    repo, store = FakeRepo(), FakeRawStore()
    with caplog.at_level("INFO"):
        summary = ingest(
            _spec(), run_id="r", source_adapter=ExplodingSource(), raw_store=store, repo=repo,
            dissector=_dissector(), run_date=_NON_FETCH_DAY,
        )
    assert summary["fetched"] == 0
    assert summary["fetch_stopped"] == SKIP_NOT_A_FETCH_DAY
    # the message must stand on its own: what happened, why, that nothing is broken, when it resumes
    msg = caplog.text
    assert "ON PURPOSE" in msg and "NOTHING IS BROKEN" in msg
    assert "200 requests per MONTH" in msg
    assert "resumes on 2026-09-07" in msg


def test_a_fetch_day_does_sweep(caplog):
    # THE OTHER DIRECTION, and the half that was missing: proving the skip works proves nothing
    # if the cadence never lets a sweep through. Without this, `skip_fetch = SKIP_...` hardcoded
    # (i.e. never fetching again) passes the suite.
    repo, store = FakeRepo(), FakeRawStore()
    src = FakeSource([_job("a")])
    summary = ingest(
        _spec(), run_id="r", source_adapter=src, raw_store=store, repo=repo,
        dissector=_dissector(), run_date=_FETCH_DAY,
    )
    assert summary["fetched"] == 1
    assert summary["fetch_stopped"] is None


def test_a_planned_skip_is_not_reported_as_a_failure(caplog):
    # negative: the cadence pause must NOT be logged at WARNING like a rate-limit stop —
    # a routine pause that pages like a fault is how alert fatigue starts (B-5).
    repo, store = FakeRepo(), FakeRawStore()
    with caplog.at_level("INFO"):
        ingest(
            _spec(), run_id="r", source_adapter=FakeSource([]), raw_store=store, repo=repo,
            dissector=_dissector(), run_date=_NON_FETCH_DAY,
        )
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_skip_message_reports_the_ACTIVE_cadence_not_the_default(caplog):
    # If the cadence is overridden, the explanation must describe what is actually happening.
    # A message that confidently states the default while a different value is in force is
    # worse than no message — it is the stale-number failure this project keeps hitting.
    with caplog.at_level("INFO"):
        ingest(
            _spec(), run_id="r", source_adapter=FakeSource([]), raw_store=FakeRawStore(),
            repo=FakeRepo(), dissector=_dissector(),
            run_date=_NON_FETCH_DAY, every_n_days=7,
        )
    assert "runs every 7 days" in caplog.text
    assert "runs every 3 days" not in caplog.text


def test_the_skip_message_does_not_contradict_its_own_arithmetic(caplog):
    # Examiner S1. The message used to derive "~13 days" to exhaust the quota and then assert,
    # in the very next clause, "~19 days of every month" with no intake — 13 + 19 = 32 in a
    # 30-day month. `~19` was a PRE-FIX literal (the 6-country, 18-request sweep) surviving
    # inside a message whose commit claimed "none is typed". Every number must be derived.
    with caplog.at_level("INFO"):
        ingest(
            _spec(), run_id="r", source_adapter=FakeSource([]), raw_store=FakeRawStore(),
            repo=FakeRepo(), dissector=_dissector(), run_date=_NON_FETCH_DAY,
        )
    msg = caplog.text
    # `_spec()` is 1 title x 1 country x 1 page = 1 request/sweep -> 200 days to exhaust, so
    # the dead-day figure must clamp to 0 rather than print a negative.
    assert "in ~200 days" in msg
    assert "leave ~0 days a month" in msg
    assert "~19 days" not in msg, "a pre-fix literal is back in a message that derives the rest"
