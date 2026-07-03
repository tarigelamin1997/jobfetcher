"""Profile contract tests: the sample loads + validates; from_jsonb round-trips; negatives
(missing required fields, blanks, bad JSONB) fail loudly."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobfetcher.core.profile import Profile

SAMPLE = Path(__file__).resolve().parents[1] / "config" / "profile.sample.yml"


def test_sample_profile_loads_and_validates():
    prof = Profile.from_yaml(SAMPLE)
    assert prof.name
    assert len(prof.skills) >= 1
    names = {s.name for s in prof.skills}
    assert {"Python", "SQL", "dbt"} <= names
    assert "Data Engineer" in prof.preferences.target_titles
    assert "Riyadh" in prof.preferences.target_locations
    assert prof.preferences.avoid_keywords  # dealbreakers present


def test_from_yaml_text_parses_and_validates():
    # the source-agnostic loader the S3 config path uses (ADR-0022)
    prof = Profile.from_yaml_text(SAMPLE.read_text(encoding="utf-8"))
    assert prof.name and len(prof.skills) >= 1


def test_from_yaml_text_empty_is_loud():
    # negative: empty document -> {} -> ValidationError (name + >=1 skill required)
    with pytest.raises(ValidationError):
        Profile.from_yaml_text("")


def test_sample_has_no_contact_pii():
    # the sample must stay sanitized: no actual contact PII *values* leak in. Check the data
    # (parsed YAML), not the comments — the header legitimately mentions "email/phone/address".
    import re

    data = SAMPLE.read_text(encoding="utf-8")
    # strip comment lines, then scan the remaining values
    body = "\n".join(ln for ln in data.splitlines() if not ln.lstrip().startswith("#")).lower()
    assert "@" not in body, "sample profile leaks an email address"
    assert not re.search(r"\+?\d[\d\s-]{8,}\d", body), "sample profile leaks a phone number"


def test_from_jsonb_round_trips():
    prof = Profile.from_yaml(SAMPLE)
    rebuilt = Profile.from_jsonb(prof.model_dump())
    assert rebuilt == prof


def test_from_yaml_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        Profile.from_yaml("config/does-not-exist.yml")


def test_missing_required_field_is_loud():
    # negative: no preferences → loud ValidationError, not a silent default
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {"name": "x", "skills": [{"name": "Python"}]}
        )


def test_empty_skills_is_loud():
    # negative: skills is required and must be non-empty (nothing to match on otherwise)
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {"name": "x", "skills": [], "preferences": {}}
        )


def test_blank_name_is_loud():
    with pytest.raises(ValidationError):
        Profile.model_validate(
            {"name": "   ", "skills": [{"name": "Python"}], "preferences": {}}
        )


def test_from_jsonb_rejects_non_dict():
    with pytest.raises(ValueError, match="must be an object"):
        Profile.from_jsonb(["not", "a", "dict"])
