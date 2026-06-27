"""Repository serialization correctness (no DB): DissectedPosting <-> JSONB row mapping.

These exercise the pure mapping helpers + a constructor guard — the DB-backed round-trip
(save → read-back-equal) is in the integration test (real local Postgres). Each case carries
a negative."""
import pytest

from jobfetcher.adapters.repository_postgres import (
    PostgresRepository,
    _skills_from_json,
    _skills_to_json,
)
from jobfetcher.core.models import DissectedPosting, RequirementLevel, Skill
from jobfetcher.core.ports import RepositoryError


def _sample_skills() -> list[Skill]:
    return [
        Skill(name="Python", level="must", evidence="3+ years with Python"),
        Skill(name="Airflow", level="nice", evidence="Airflow is a plus"),
        Skill(name="ETL", level="implied", evidence="build ETL pipelines"),
    ]


def test_skills_round_trip_through_jsonb():
    skills = _sample_skills()
    as_json = _skills_to_json(skills)
    # JSONB shape: list of plain dicts, level is the string value (not the Enum member).
    assert as_json[0] == {"name": "Python", "level": "must", "evidence": "3+ years with Python"}
    assert all(isinstance(s["level"], str) for s in as_json)

    back = _skills_from_json(as_json)
    assert back == skills  # Pydantic equality: full round-trip is lossless
    assert back[2].level is RequirementLevel.implied


def test_skills_from_json_tolerates_none_and_empty():
    # negative: a NULL / empty skills column reads back as an empty list, not a crash.
    assert _skills_from_json(None) == []
    assert _skills_from_json([]) == []


def test_to_json_empty_skills():
    assert _skills_to_json([]) == []


def test_dissected_posting_serializes_all_skill_fields():
    d = DissectedPosting(
        raw_title="Senior DE",
        language="en",
        normalized_title="Data Engineer",
        model="deepseek-v4-flash",
        skills=_sample_skills(),
        dropped_skill_count=2,
    )
    rebuilt_skills = _skills_from_json(_skills_to_json(d.skills))
    assert [s.name for s in rebuilt_skills] == ["Python", "Airflow", "ETL"]
    assert [s.level.value for s in rebuilt_skills] == ["must", "nice", "implied"]


def test_constructor_rejects_empty_url():
    # negative: no connection URL -> clear RepositoryError, never a silent bad engine.
    with pytest.raises(RepositoryError):
        PostgresRepository("")
    with pytest.raises(RepositoryError):
        PostgresRepository("   ")
