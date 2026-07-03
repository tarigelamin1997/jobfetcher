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
