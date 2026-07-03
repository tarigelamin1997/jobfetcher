"""JSearchSourceAdapter unit tests (network mocked): the raw→metadata mapping + seniority,
and the fetch sweep's pagination / budget / quota-stop. Each carries a negative."""
import io
import urllib.error

import pytest

from jobfetcher.adapters import jsearch_source
from jobfetcher.adapters.jsearch_source import (
    JSearchSourceAdapter,
    _seniority_from_title,
    get_key,
    jd_and_metadata_from_jsearch,
)
from jobfetcher.core.ports import SourceError
from jobfetcher.core.search_spec import SearchSpec


def _spec(*, titles=("data engineer",), countries=("sa",), max_pages=3, budget=100,
          employment_types=()) -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch",
            "secret_name": "jobfetcher/jsearch",
            "aws_region": "us-east-1",
            "targeting": {
                "job_titles": list(titles),
                "countries": list(countries),
                "cities": [],
                "states": [],
            },
            "date_posted": "week",
            "language": "en",
            "employment_types": list(employment_types),
            "remote": "off",
            "threshold": 60, "hard_floor": 50, "near_miss_band": 10,
            "budget": {"max_pages_per_query": max_pages, "request_budget_per_run": budget},
        }
    )


def _job(jid: str, **over) -> dict:
    base = {
        "job_id": jid,
        "job_title": "Senior Data Engineer",
        "job_description": "Build pipelines with Python.",
        "job_location": "Riyadh",
        "job_city": "Riyadh",
        "job_country": "SA",
        "job_employment_type": "FULLTIME",
        "employer_name": "Acme",
        "job_apply_link": "https://x/apply",
        "job_state": None,
    }
    base.update(over)
    return base


def _full_page(n: int, start: int = 0) -> dict:
    return {"data": [_job(f"j{start + i}") for i in range(n)]}


# --------------------------------------------------------------------------- mapping
def test_mapping_extracts_jd_and_metadata():
    jd, meta = jd_and_metadata_from_jsearch(_job("j1"))
    assert jd == "Build pipelines with Python."
    assert meta.raw_title == "Senior Data Engineer"
    assert meta.seniority == "senior"  # parsed deterministically from the title
    assert meta.city == "Riyadh" and meta.country == "SA" and meta.language == "en"


def test_mapping_prefers_query_country_over_raw():
    # C3: the authoritative *query* country overrides the per-record job_country.
    _jd, meta = jd_and_metadata_from_jsearch(_job("j1", job_country="SA"), query_country="ae")
    assert meta.country == "ae"  # the queried scope wins


def test_mapping_falls_back_to_raw_country_when_no_query():
    # C3 fallback: with no query_country threaded, the source-stated job_country is kept.
    _jd, meta = jd_and_metadata_from_jsearch(_job("j1", job_country="SA"))
    assert meta.country == "SA"


def test_fetch_attaches_query_country_side_channel(monkeypatch):
    # C3: each yielded job carries the queried country on the transient side-channel key.
    from jobfetcher.adapters.jsearch_source import QUERY_COUNTRY_KEY

    _patch_pages(monkeypatch, [{"data": [_job("j0")]}])
    out = list(
        JSearchSourceAdapter(api_key="k").fetch(
            _spec(countries=("ae",), max_pages=1), run_id="r"
        )
    )
    assert out[0][QUERY_COUNTRY_KEY] == "ae"


def test_mapping_tolerates_missing_fields():
    # negative: a sparse payload → no crash, sensible fallbacks, no JD.
    jd, meta = jd_and_metadata_from_jsearch({})
    assert jd == ""
    assert meta.raw_title == "Untitled role"
    assert meta.seniority is None and meta.location is None


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Principal Engineer", "principal"),
        ("Staff Data Engineer", "staff"),
        ("Junior Analyst", "junior"),
        ("Data Engineer", None),  # negative: no seniority keyword
    ],
)
def test_seniority_from_title(title, expected):
    assert _seniority_from_title(title) == expected


# --------------------------------------------------------------------------- key resolution
def test_get_key_env_fallback(monkeypatch):
    monkeypatch.setattr(jsearch_source, "boto3", None, raising=False)
    monkeypatch.setenv("JSEARCH_API_KEY", "envkey")
    # force the boto3 path to fail so the env fallback is exercised
    import builtins

    real_import = builtins.__import__

    def _no_boto3(name, *a, **k):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_boto3)
    assert get_key(_spec()) == "envkey"


def test_get_key_raises_when_unresolvable(monkeypatch):
    monkeypatch.delenv("JSEARCH_API_KEY", raising=False)
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)
    import builtins

    real_import = builtins.__import__

    def _no_boto3(name, *a, **k):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_boto3)
    with pytest.raises(SourceError):
        get_key(_spec())


# --------------------------------------------------------------------------- fetch sweep
def _patch_pages(monkeypatch, pages):
    """Make `_fetch_page` return successive items of `pages` (dict or exception)."""
    seq = iter(pages)

    def _fake(query, country, page, spec, key):
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(jsearch_source, "_fetch_page", _fake)
    monkeypatch.setattr(jsearch_source.time, "sleep", lambda *_: None)


def test_fetch_paginates_until_short_page(monkeypatch):
    # full page (10) then a short page (3) → 13 jobs, stop (no 3rd call needed).
    _patch_pages(monkeypatch, [_full_page(10), {"data": [_job(f"s{i}") for i in range(3)]}])
    out = list(JSearchSourceAdapter(api_key="k").fetch(_spec(max_pages=5), run_id="r"))
    assert len(out) == 13


def test_fetch_empty_data_yields_nothing(monkeypatch):
    # negative: empty data → 0 postings, no crash, stops (short page).
    _patch_pages(monkeypatch, [{"data": []}])
    out = list(JSearchSourceAdapter(api_key="k").fetch(_spec(), run_id="r"))
    assert out == []


def test_fetch_429_stops_gracefully(monkeypatch):
    # negative: a 429 (quota/rate) mid-sweep stops politely, yielding what came before — never
    # crashes (C5: 429 stays graceful).
    err = urllib.error.HTTPError("u", 429, "rate", None, io.BytesIO(b""))
    _patch_pages(monkeypatch, [_full_page(10), err])
    out = list(JSearchSourceAdapter(api_key="k").fetch(_spec(max_pages=5), run_id="r"))
    assert len(out) == 10  # first page survived; the 429 stopped the rest


@pytest.mark.parametrize("code", [401, 403])
def test_fetch_auth_failure_hard_fails(monkeypatch, code):
    # C5: 401 (bad/missing key) and 403 (auth/subscription failure) must FAIL LOUDLY —
    # a broken credential must never become a silent zero-count "success".
    err = urllib.error.HTTPError("u", code, "auth", None, io.BytesIO(b""))
    _patch_pages(monkeypatch, [err])
    with pytest.raises(SourceError, match=str(code)):
        list(JSearchSourceAdapter(api_key="k").fetch(_spec(), run_id="r"))


def test_fetch_auth_failure_raises_even_after_results(monkeypatch):
    # C5: an auth failure mid-sweep still raises — we do NOT silently return the partial
    # results, because a revoked key invalidates the run's completeness guarantee.
    err = urllib.error.HTTPError("u", 403, "auth", None, io.BytesIO(b""))
    _patch_pages(monkeypatch, [_full_page(10), err])
    with pytest.raises(SourceError):
        list(JSearchSourceAdapter(api_key="k").fetch(_spec(max_pages=5), run_id="r"))


def test_fetch_budget_caps_requests(monkeypatch):
    # request budget = 2 → at most 2 pages fetched even though more are available.
    _patch_pages(monkeypatch, [_full_page(10), _full_page(10, 10), _full_page(10, 20)])
    spec = _spec(max_pages=10, budget=2)
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert len(out) == 20  # exactly 2 full pages, then the cap stops the sweep


def test_fetch_network_error_skips_query(monkeypatch):
    # negative: a URLError breaks this query's pages but the sweep continues to the next query.
    err = urllib.error.URLError("down")
    _patch_pages(monkeypatch, [err, {"data": [_job("ok")]}])
    spec = _spec(titles=("de", "da"), max_pages=2)  # two queries
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert [j["job_id"] for j in out] == ["ok"]  # query 1 skipped, query 2 yielded


# --------------------------------------------------------------------------- malformed shapes (B1)
def test_fetch_malformed_data_shapes_dont_crash(monkeypatch):
    # negative (B1): `data` as a dict, then as a string, then a list with a non-dict item.
    # Only valid dict items are yielded; the bad shapes are skipped, never crash.
    _patch_pages(
        monkeypatch,
        [
            {"data": {"unexpected": "object"}},  # dict, not list → skipped
            {"data": "boom"},                     # string, not list → skipped
            {"data": [_job("good"), "junk", 42]}, # list with non-dict items → yield only the dict
        ],
    )
    spec = _spec(titles=("a", "b", "c"), max_pages=1)  # three queries, one page each
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert [j["job_id"] for j in out] == ["good"]  # only the one valid dict survived


def test_fetch_non_json_body_skips_query(monkeypatch):
    # negative (B2): a JSON-decode failure (gateway HTML page) is transient → skip this query,
    # continue to the next, never crash.
    import json as _json

    err = _json.JSONDecodeError("Expecting value", "<html>...", 0)
    _patch_pages(monkeypatch, [err, {"data": [_job("ok")]}])
    spec = _spec(titles=("de", "da"), max_pages=2)  # two queries
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert [j["job_id"] for j in out] == ["ok"]  # query 1 skipped, query 2 yielded


def test_fetch_read_timeout_skips_query(monkeypatch):
    # negative (B3): a bare TimeoutError (not a URLError subclass on 3.11) is transient →
    # skip this query, continue, never crash.
    _patch_pages(monkeypatch, [TimeoutError("read timed out"), {"data": [_job("ok")]}])
    spec = _spec(titles=("de", "da"), max_pages=2)  # two queries
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert [j["job_id"] for j in out] == ["ok"]


def test_fetch_budget_counts_failed_requests(monkeypatch):
    # S1: a source erroring on every call must still be bounded by the request budget.
    # budget=2, 3 titles × 2 countries = 6 possible queries → at most 2 real HTTP calls.
    calls = {"n": 0}

    def _always_error(query, country, page, spec, key):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr(jsearch_source, "_fetch_page", _always_error)
    monkeypatch.setattr(jsearch_source.time, "sleep", lambda *_: None)
    spec = _spec(titles=("a", "b", "c"), countries=("sa", "ae"), max_pages=1, budget=2)
    out = list(JSearchSourceAdapter(api_key="k").fetch(spec, run_id="r"))
    assert out == []
    assert calls["n"] == 2  # the cap bounded the billed calls, despite every call erroring


def test_get_key_json_secret_without_api_key_raises(monkeypatch):
    # negative (M2): a secret that is valid JSON but lacks api_key/apiKey → SourceError,
    # never the whole JSON blob returned as the "key".
    class _FakeSecrets:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 kwarg name
            return {"SecretString": '{"username": "u", "password": "p"}'}

    class _FakeBoto3:
        @staticmethod
        def client(*a, **k):
            return _FakeSecrets()

    monkeypatch.setitem(__import__("sys").modules, "boto3", _FakeBoto3)
    with pytest.raises(SourceError, match="no 'api_key'"):
        get_key(_spec())


# --------------------------------------------------------------------------- employment_types (v0.3.1)
def _capture_fetch_url(monkeypatch) -> dict:
    """Intercept `_fetch_page`'s HTTP call → capture the request URL (with query params)."""
    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": []}'

    def _fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(jsearch_source.urllib.request, "urlopen", _fake_urlopen)
    return captured


def test_fetch_page_wires_employment_types_when_set(monkeypatch):
    # the filter now actually reaches the JSearch query (it was defined but never sent)
    from jobfetcher.core.search_spec import EmploymentType

    captured = _capture_fetch_url(monkeypatch)
    spec = _spec(employment_types=(EmploymentType.fulltime, EmploymentType.contractor))
    jsearch_source._fetch_page("data engineer", "sa", 1, spec, "k")
    assert "employment_types=FULLTIME%2CCONTRACTOR" in captured["url"]  # comma url-encoded


def test_fetch_page_omits_employment_types_when_empty(monkeypatch):
    # [] = no filter → the param is absent entirely (not an empty string)
    captured = _capture_fetch_url(monkeypatch)
    jsearch_source._fetch_page("data engineer", "sa", 1, _spec(employment_types=()), "k")
    assert "employment_types" not in captured["url"]
