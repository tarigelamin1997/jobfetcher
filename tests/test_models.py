"""Contract tests for the dissection data models."""
import pytest
from pydantic import ValidationError

from jobfetcher.core.models import DissectedPosting, LlmExtraction, RequirementLevel, Skill


def test_skill_requires_all_fields():
    with pytest.raises(ValidationError):
        Skill(name="Python", level="must")  # missing evidence


def test_skill_rejects_unknown_level():
    with pytest.raises(ValidationError):
        Skill(name="Python", level="required", evidence="x")


def test_skill_levels():
    assert Skill(name="x", level="must", evidence="e").level is RequirementLevel.must
    assert Skill(name="x", level="implied", evidence="e").level is RequirementLevel.implied


def test_llm_extraction_requires_normalized_title():
    with pytest.raises(ValidationError):
        LlmExtraction(skills=[], sector=None)  # missing normalized_title


def test_llm_extraction_tolerates_extra_keys():
    e = LlmExtraction.model_validate(
        {"skills": [], "sector": None, "normalized_title": "DE", "explanation": "chatter"}
    )
    assert e.normalized_title == "DE"


def test_dissected_posting_minimal():
    d = DissectedPosting(
        raw_title="Senior DE", language="en", normalized_title="Data Engineer", model="m"
    )
    assert d.skills == [] and d.dropped_skill_count == 0 and d.location is None
