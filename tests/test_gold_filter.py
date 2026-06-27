"""Gold filter unit tests (no DB / no live LLM): the deterministic strategy, the LLM strategy
with a FakeLlm (including the fail-open path), and the apply_gold_filter orchestration over a
fake repo + a stub strategy. Each carries a negative."""
from __future__ import annotations

import json

import pytest

from jobfetcher.adapters.filter_deterministic import DeterministicFilterStrategy
from jobfetcher.adapters.filter_llm import LlmFilterStrategy
from jobfetcher.core.ingest import apply_gold_filter
from jobfetcher.core.models import DissectedPosting, Skill
from jobfetcher.core.ports import FilterError, LlmError
from jobfetcher.core.profile import Profile
from jobfetcher.core.search_spec import SearchSpec
from tests.helpers import FakeLlm


# --------------------------------------------------------------------------- builders
def _spec(titles=None, countries=None, cities=None) -> SearchSpec:
    return SearchSpec.model_validate(
        {
            "source": "jsearch", "secret_name": "s", "aws_region": "us-east-1",
            "targeting": {
                "job_titles": titles or ["Data Engineer"],
                "countries": countries or ["sa"],
                "cities": cities or [],
                "states": [],
            },
            "date_posted": "week", "language": "en", "employment_types": [],
            "remote": "off", "threshold": 60,
            "budget": {"max_pages_per_query": 1, "request_budget_per_run": 10},
        }
    )


def _profile(avoid=None) -> Profile:
    return Profile.model_validate(
        {
            "name": "Tester",
            "skills": [{"name": "Python"}, {"name": "SQL"}],
            "preferences": {
                "target_titles": ["Data Engineer"],
                "target_locations": ["Riyadh"],
                "avoid_keywords": avoid or [],
            },
        }
    )


def _posting(*, title="Data Engineer", country="sa", city="Riyadh", location="Riyadh") -> DissectedPosting:
    return DissectedPosting(
        raw_title=title,
        language="en",
        location=location,
        city=city,
        country=country,
        seniority="mid",
        normalized_title=title,
        sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


# --------------------------------------------------------------------------- deterministic
def test_deterministic_matching_posting_is_included():
    assert DeterministicFilterStrategy().filter(_spec(), _profile(), _posting()) is True


def test_deterministic_wrong_title_is_excluded():
    nurse = _posting(title="Registered Nurse")
    assert DeterministicFilterStrategy().filter(_spec(), _profile(), nurse) is False


def test_deterministic_wrong_location_is_excluded():
    # queried SA, posting is in the US → dropped
    far = _posting(country="us", city="Austin", location="Austin")
    assert DeterministicFilterStrategy().filter(_spec(), _profile(), far) is False


def test_deterministic_avoid_keyword_is_excluded():
    prof = _profile(avoid=["internship"])
    intern = _posting(title="Data Engineer Internship")
    assert DeterministicFilterStrategy().filter(_spec(), prof, intern) is False


def test_deterministic_city_filter_excludes_other_city():
    spec = _spec(cities=["Dubai"])
    riyadh = _posting(country="sa", city="Riyadh", location="Riyadh")
    assert DeterministicFilterStrategy().filter(spec, _profile(), riyadh) is False


def test_deterministic_empty_targeting_is_permissive():
    # an empty cities target = no city constraint; country still matches
    assert DeterministicFilterStrategy().filter(_spec(cities=[]), _profile(), _posting()) is True


# --------------------------------------------------------------------------- LLM strategy
def test_llm_filter_true():
    llm = FakeLlm(json.dumps({"likely_fit": True, "reason": "matches role"}))
    assert LlmFilterStrategy(llm).filter(_spec(), _profile(), _posting()) is True


def test_llm_filter_false():
    llm = FakeLlm(json.dumps({"likely_fit": False, "reason": "wrong field"}))
    assert LlmFilterStrategy(llm).filter(_spec(), _profile(), _posting()) is False


def test_llm_filter_retries_then_succeeds():
    llm = FakeLlm("not json", json.dumps({"likely_fit": True, "reason": "ok"}))
    assert LlmFilterStrategy(llm).filter(_spec(), _profile(), _posting()) is True
    assert len(llm.calls) == 2  # retried exactly once


def test_llm_filter_bad_json_after_retry_raises_filter_error():
    # negative: unparseable twice → FilterError (the caller fails open)
    llm = FakeLlm("nope", "still nope")
    with pytest.raises(FilterError):
        LlmFilterStrategy(llm).filter(_spec(), _profile(), _posting())


def test_llm_filter_transport_error_raises_filter_error():
    # negative: an LLM transport failure → FilterError (the caller fails open)
    class _BoomLlm:
        def complete(self, *, system, user):
            raise LlmError("boom")

    with pytest.raises(FilterError):
        LlmFilterStrategy(_BoomLlm()).filter(_spec(), _profile(), _posting())


# --------------------------------------------------------------------------- apply_gold_filter
class FakeGoldRepo:
    """In-memory repo for the gold-filter orchestration: silver postings in, clusters +
    status transitions tracked."""

    def __init__(self, silver: list[tuple[str, DissectedPosting]]) -> None:
        self._silver = silver
        self.clusters: dict[str, dict] = {}
        self.posting_cluster: dict[str, str] = {}
        self.status: dict[str, str] = {pid: "silver" for pid, _ in silver}

    def get_silver_postings(self, *, limit=None):
        return list(self._silver)

    def upsert_cluster(self, *, cluster_id, representative_posting_id, posting_count=1):
        self.clusters.setdefault(
            cluster_id,
            {"representative_posting_id": representative_posting_id, "posting_count": posting_count},
        )
        return cluster_id

    def set_posting_cluster(self, posting_id, cluster_id):
        self.posting_cluster[posting_id] = cluster_id

    def mark_gold_candidate(self, posting_id):
        self.status[posting_id] = "gold_candidate"


class _StubStrategy:
    """Returns a fixed verdict per posting_id (by normalized_title here for simplicity)."""

    def __init__(self, verdicts: dict[str, bool]) -> None:
        self.verdicts = verdicts

    def filter(self, spec, profile, posting):
        return self.verdicts[posting.normalized_title]


def test_apply_gold_filter_marks_fits_and_clusters():
    silver = [
        ("p-fit", _posting(title="Data Engineer")),
        ("p-drop", _posting(title="Nurse")),
    ]
    repo = FakeGoldRepo(silver)
    strategy = _StubStrategy({"Data Engineer": True, "Nurse": False})

    summary = apply_gold_filter(_spec(), _profile(), strategy=strategy, repo=repo)

    assert summary == {"silver": 2, "gold": 1, "dropped": 1}
    # the fit is promoted, clustered 1:1, attached
    assert repo.status["p-fit"] == "gold_candidate"
    assert repo.clusters == {"p-fit": {"representative_posting_id": "p-fit", "posting_count": 1}}
    assert repo.posting_cluster == {"p-fit": "p-fit"}
    # the non-fit stays silver, no cluster
    assert repo.status["p-drop"] == "silver"
    assert "p-drop" not in repo.posting_cluster


def test_apply_gold_filter_fails_open_on_filter_error():
    # negative: a strategy that raises FilterError → the posting is INCLUDED (fail-open)
    silver = [("p-1", _posting())]
    repo = FakeGoldRepo(silver)

    class _BoomStrategy:
        def filter(self, spec, profile, posting):
            raise FilterError("cannot decide")

    summary = apply_gold_filter(_spec(), _profile(), strategy=_BoomStrategy(), repo=repo)

    assert summary == {"silver": 1, "gold": 1, "dropped": 0}
    assert repo.status["p-1"] == "gold_candidate"  # included despite the error


def test_apply_gold_filter_empty_silver_is_zeroes():
    repo = FakeGoldRepo([])
    summary = apply_gold_filter(_spec(), _profile(), strategy=_StubStrategy({}), repo=repo)
    assert summary == {"silver": 0, "gold": 0, "dropped": 0}
