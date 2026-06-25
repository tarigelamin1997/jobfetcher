"""Dissector tests (LLM mocked): happy path, grounding guard, retry, parsing, negatives."""
import json

import pytest

from jobfetcher.core.dissector import Dissector, DissectionError, _extract_json, grounding_check
from jobfetcher.core.models import PostingMetadata, Skill
from tests.helpers import CANNED_JD, CANNED_LLM_JSON, FakeLlm

META = PostingMetadata(raw_title="Senior Data Engineer", language="en", seniority="senior")


def test_dissect_happy_path():
    out = Dissector(FakeLlm(CANNED_LLM_JSON), model_id="test-model").dissect(CANNED_JD, META)
    names = {s.name for s in out.skills}
    assert {"Python", "Airflow"} <= names
    assert out.normalized_title == "Data Engineer"
    assert out.seniority == "senior" and out.language == "en"  # carried from metadata
    assert out.model == "test-model"
    assert out.dropped_skill_count == 0


def test_empty_jd_raises():
    with pytest.raises(DissectionError):
        Dissector(FakeLlm()).dissect("   ", META)


def test_ungrounded_skill_is_dropped():
    reply = json.dumps(
        {
            "skills": [
                {"name": "Python", "level": "must", "evidence": "Required: 3+ years with Python and SQL"},
                {"name": "Kubernetes", "level": "must", "evidence": "deep Kubernetes operations"},
            ],
            "sector": None,
            "normalized_title": "Data Engineer",
        }
    )
    out = Dissector(FakeLlm(reply)).dissect(CANNED_JD, META)
    names = {s.name for s in out.skills}
    assert "Python" in names
    assert "Kubernetes" not in names  # evidence not in the JD -> caught by grounding_check
    assert out.dropped_skill_count == 1


def test_retry_then_succeed():
    llm = FakeLlm("not json at all", CANNED_LLM_JSON)
    out = Dissector(llm).dissect(CANNED_JD, META)
    assert len(llm.calls) == 2  # retried exactly once
    assert out.normalized_title == "Data Engineer"


def test_retry_exhausted_raises():
    with pytest.raises(DissectionError):
        Dissector(FakeLlm("nope", "still nope")).dissect(CANNED_JD, META)


def test_grounding_check_whitespace_insensitive():
    jd = "we use Python and    SQL daily"
    grounded, ungrounded = grounding_check(
        jd,
        [
            Skill(name="Python", level="must", evidence="use Python and SQL"),
            Skill(name="Go", level="nice", evidence="Golang microservices"),
        ],
    )
    assert [s.name for s in grounded] == ["Python"]
    assert [s.name for s in ungrounded] == ["Go"]


@pytest.mark.parametrize("text", ['```json\n{"a": 1}\n```', 'thinking... {"a": 1} done', '{"a": 1}'])
def test_extract_json_tolerant(text):
    assert _extract_json(text) == {"a": 1}


def test_extract_json_no_object_raises():
    with pytest.raises(DissectionError):
        _extract_json("no json here")
