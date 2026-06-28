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
        "budget": {"max_pages_per_query": 5, "request_budget_per_run": 70},
    }


# ── positive sanity ─────────────────────────────────────────────────────────


def test_sample_search_spec_loads_and_validates():
    spec = SearchSpec.from_yaml(SAMPLE)
    assert spec.source == "jsearch"
    assert spec.targeting.job_titles
    assert spec.targeting.countries == [c.lower() for c in spec.targeting.countries]
    assert 0 <= spec.threshold <= 100


def test_valid_dict_constructs():
    spec = SearchSpec.model_validate(_valid_spec_dict())
    assert spec.targeting.countries == ["sa"]


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
