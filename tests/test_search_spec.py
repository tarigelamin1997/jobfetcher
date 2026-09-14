"""SearchSpec contract tests: the sample loads + validates; negatives (empty lists, a
bad ISO-3166 country, blanks, an unknown key) fail loudly with ValidationError."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfetcher.core.search_spec import SearchSpec

SAMPLE = Path(__file__).resolve().parents[1] / "config" / "search_config.sample.yml"


def _valid_spec_dict() -> dict:
    """A minimal, fully-valid SearchSpec payload — the base that negatives mutate."""
    return {
        "source": "jsearch",
        "secret_name": "jobfetcher/jsearch",
        "aws_region": "us-east-1",
        "targeting": {
            "job_titles": ["Data Engineer"],
            "countries": ["sa"],
            "cities": [],
            "states": [],
        },
        "date_posted": "month",
        "language": "en",
        "employment_types": [],
        "remote": "off",
        "threshold": 60,
        "hard_floor": 50,
        "near_miss_band": 10,
        "reassess_max_age_days": 45,
        "digest_max_age_days": 90,
        "budget": {"max_pages_per_query": 5, "request_budget_per_run": 70},
    }


# ── positive sanity ─────────────────────────────────────────────────────────


def test_sample_search_spec_loads_and_validates():
    spec = SearchSpec.from_yaml(SAMPLE)
    assert spec.source == "jsearch"
    assert spec.targeting.job_titles
    assert spec.targeting.countries == [c.lower() for c in spec.targeting.countries]
    assert 0 <= spec.threshold <= 100


def test_from_yaml_text_parses_and_validates():
    # the source-agnostic loader the S3 path uses (ADR-0022): text in, validated spec out
    text = SAMPLE.read_text(encoding="utf-8")
    assert SearchSpec.from_yaml_text(text).source == "jsearch"


def test_from_yaml_text_empty_is_loud():
    # negative: an empty document is {} -> ValidationError (missing required fields), not a
    # silent empty spec
    with pytest.raises(ValidationError):
        SearchSpec.from_yaml_text("")


def test_valid_dict_constructs():
    spec = SearchSpec.model_validate(_valid_spec_dict())
    assert spec.targeting.countries == ["sa"]


def test_all_three_strictness_knobs_are_user_set():
    # the three shortlist knobs load from config (not code defaults) — this is what makes them
    # user-editable end-to-end
    spec = SearchSpec.model_validate(_valid_spec_dict())
    assert (spec.threshold, spec.hard_floor, spec.near_miss_band) == (60, 50, 10)


def test_sample_carries_the_three_knobs():
    spec = SearchSpec.from_yaml(SAMPLE)
    assert 0 <= spec.hard_floor <= spec.threshold <= 100
    assert spec.near_miss_band >= 0


# ── negatives for the new strictness knobs ──────────────────────────────────


@pytest.mark.parametrize("field", ["hard_floor", "near_miss_band"])
def test_missing_strictness_knob_is_loud(field):
    # required, no default — omitting either fails loudly (the "nothing assumed" contract)
    data = _valid_spec_dict()
    del data[field]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_hard_floor_above_threshold_is_loud():
    # the give-up floor cannot sit above the shortlist bar — cross-field model validator
    data = _valid_spec_dict()
    data["hard_floor"] = 70  # > threshold 60
    with pytest.raises(ValidationError, match="hard_floor"):
        SearchSpec.model_validate(data)


def test_hard_floor_equal_to_threshold_is_allowed():
    # equal is fine (the current 60/50/10 default has floor < threshold; equal collapses stretch)
    data = _valid_spec_dict()
    data["hard_floor"] = 60
    spec = SearchSpec.model_validate(data)
    assert spec.hard_floor == spec.threshold == 60


@pytest.mark.parametrize("field,bad", [("threshold", 101), ("hard_floor", -1), ("near_miss_band", 200)])
def test_strictness_knob_out_of_range_is_loud(field, bad):
    data = _valid_spec_dict()
    data[field] = bad
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


# ── the reassess age-bound knob ──────────────────────────────────────────────


def test_missing_reassess_max_age_days_is_loud():
    # required like every other knob — omitting it fails loudly (the "nothing assumed" contract)
    data = _valid_spec_dict()
    del data["reassess_max_age_days"]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize("bad", [-1, 366, 400])
def test_reassess_max_age_days_out_of_range_is_loud(bad):
    data = _valid_spec_dict()
    data["reassess_max_age_days"] = bad
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize("ok", [0, 45, 365])
def test_reassess_max_age_days_in_range_loads(ok):
    # 0 = no age cutoff (unbounded replay) is a VALID value, not an error
    data = _valid_spec_dict()
    data["reassess_max_age_days"] = ok
    assert SearchSpec.model_validate(data).reassess_max_age_days == ok


def test_sample_carries_reassess_max_age_days():
    spec = SearchSpec.from_yaml(SAMPLE)
    assert 0 <= spec.reassess_max_age_days <= 365


# ── the digest age-bound knob (digest truthfulness) ──────────────────────────


def test_missing_digest_max_age_days_is_loud():
    # required like every other knob — omitting it fails loudly (the "nothing assumed" contract)
    data = _valid_spec_dict()
    del data["digest_max_age_days"]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize("bad", [-1, 366, 400])
def test_digest_max_age_days_out_of_range_is_loud(bad):
    data = _valid_spec_dict()
    data["digest_max_age_days"] = bad
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


@pytest.mark.parametrize("ok", [0, 90, 365])
def test_digest_max_age_days_in_range_loads(ok):
    # 0 = keep forever (no age cutoff on the digest) is a VALID value, not an error
    data = _valid_spec_dict()
    data["digest_max_age_days"] = ok
    assert SearchSpec.model_validate(data).digest_max_age_days == ok


def test_sample_carries_digest_max_age_days():
    spec = SearchSpec.from_yaml(SAMPLE)
    assert 0 <= spec.digest_max_age_days <= 365


# ── negatives (contract fails loudly) ───────────────────────────────────────


def test_empty_job_titles_is_loud():
    data = _valid_spec_dict()
    data["targeting"]["job_titles"] = []
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_empty_countries_is_loud():
    data = _valid_spec_dict()
    data["targeting"]["countries"] = []
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


# NOTE: the `_iso2` validator checks alpha-2 *format* (len == 2 and isalpha), not
# membership in the real ISO-3166 table. So "usa" (len 3), "s" (len 1) and "1a"
# (non-alpha) are rejected; a well-formed-but-nonexistent code like "xx" is NOT
# caught here (see test below documenting that gap).
@pytest.mark.parametrize("bad", ["usa", "s", "1a", ""])
def test_malformed_iso2_country_is_loud(bad):
    data = _valid_spec_dict()
    data["targeting"]["countries"] = [bad]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_wellformed_but_unknown_country_is_accepted_format_only():
    """Documents the v0 contract boundary: `_iso2` validates alpha-2 *shape*, not real
    ISO-3166 membership, so "xx" passes. True membership validation is a future migration —
    pinned here so a later tightening intentionally flips this assertion."""
    data = _valid_spec_dict()
    data["targeting"]["countries"] = ["xx"]
    spec = SearchSpec.model_validate(data)
    assert spec.targeting.countries == ["xx"]


def test_blank_job_title_is_loud():
    data = _valid_spec_dict()
    data["targeting"]["job_titles"] = ["   "]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_valid_employment_types_load(monkeypatch):
    data = _valid_spec_dict()
    data["employment_types"] = ["FULLTIME", "CONTRACTOR"]
    spec = SearchSpec.model_validate(data)
    assert [e.value for e in spec.employment_types] == ["FULLTIME", "CONTRACTOR"]


@pytest.mark.parametrize("bad", ["fulltime", "FULL_TIME", "PERMANENT", "full-time"])
def test_bad_employment_type_is_loud(bad):
    # the v0.3.1 fix: an unknown/typo value now fails at load (was: silently accepted + ignored)
    data = _valid_spec_dict()
    data["employment_types"] = [bad]
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_unknown_key_is_loud():
    data = _valid_spec_dict()
    data["unexpected"] = "nope"
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


def test_unknown_targeting_key_is_loud():
    data = _valid_spec_dict()
    data["targeting"]["region"] = "GCC"
    with pytest.raises(ValidationError):
        SearchSpec.model_validate(data)


# ── free-tier arithmetic: the sample must actually FIT, not just claim to ───
# The sample used to carry `# fits free 200/mo` beside `max_pages_per_query: 5` and
# `request_budget_per_run: 70` — about 700 requests a month, 3.5x the free tier. Anyone copying
# it would have run out within about a week and hit ERR-017. "Fits the free tier" is only true
# if something fails when it stops being true; these tests are that something.


def _worst_case_monthly_requests(spec: SearchSpec) -> tuple[int, int]:
    """`(requests in the worst-aligned month, the plan's monthly quota)` for `spec`.

    Worst-aligned, not average: at a cadence of N days a 31-day window holds up to ceil(31 / N)
    fetch days — 11 at the default cadence of 3, not the ~10 an average suggests. The average
    hides exactly the month that runs out."""
    from jobfetcher.core.ingest import FETCH_EVERY_N_DAYS, SOURCE_MONTHLY_QUOTA, _sweep_cost

    worst_sweeps = -(-31 // FETCH_EVERY_N_DAYS)  # ceil(31 / N)
    return _sweep_cost(spec) * worst_sweeps, SOURCE_MONTHLY_QUOTA


def _fits_free_tier(spec: SearchSpec) -> bool:
    used, quota = _worst_case_monthly_requests(spec)
    return used <= quota


def _budget_lets_a_full_sweep_complete(spec: SearchSpec) -> bool:
    from jobfetcher.core.ingest import _sweep_cost

    return spec.budget.request_budget_per_run >= _sweep_cost(spec)


def _sample_spec() -> SearchSpec:
    return SearchSpec.from_yaml_text(SAMPLE.read_text(encoding="utf-8"))


def test_the_sample_fits_the_free_tier_in_the_worst_month():
    spec = _sample_spec()
    used, quota = _worst_case_monthly_requests(spec)
    assert _fits_free_tier(spec), (
        f"the sample would spend {used} requests in its worst month against a {quota} free tier"
    )


def test_the_sample_budget_lets_a_full_sweep_complete():
    # A budget below the sweep cost means EVERY sweep stops early and reports
    # `budget_exhausted`: the query matrix never completes and the counts are always a floor.
    assert _budget_lets_a_full_sweep_complete(_sample_spec())


def test_the_previous_over_quota_sample_is_caught():
    # negative, through the SAME predicate the positive uses — so a predicate broken to always
    # return True fails here. The shape is the sample this change replaces:
    # 3 titles x 6 countries x 5 pages.
    d = _valid_spec_dict()
    d["targeting"]["job_titles"] = ["Data Engineer", "Data Platform Engineer", "Data Architect"]
    d["targeting"]["countries"] = ["sa", "ae", "qa", "kw", "bh", "om"]
    d["budget"] = {"max_pages_per_query": 5, "request_budget_per_run": 70}
    assert not _fits_free_tier(SearchSpec.model_validate(d))


def test_a_budget_below_the_sweep_cost_is_caught():
    # negative for the budget predicate: 2 titles x 5 countries x 1 page = 10 per sweep, budget 9.
    d = _valid_spec_dict()
    d["targeting"]["job_titles"] = ["Data Engineer", "Data Architect"]
    d["targeting"]["countries"] = ["sa", "ae", "qa", "kw", "bh"]
    d["budget"] = {"max_pages_per_query": 1, "request_budget_per_run": 9}
    assert not _budget_lets_a_full_sweep_complete(SearchSpec.model_validate(d))
