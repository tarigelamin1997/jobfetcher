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
            "remote": "off", "threshold": 60, "hard_floor": 50, "near_miss_band": 10,
            "reassess_max_age_days": 45, "digest_max_age_days": 90,
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


# ------------------------------------------------------------- H-3: subset title matching
# The six REAL junk titles the live P2 run (2026-07-02) passed to the pro-model scorer under
# the old any-single-shared-token rule, querying "Data Architect" — every one scored ≤15.
_LIVE_JUNK_TITLES = [
    "Analytics Architect",
    "Enterprise Architect",
    "Arangodb Architect",
    "Alliances Manager - Data & AI",
    "Computer Vision Engineer",
    "Azure Architect",
]


@pytest.mark.parametrize("junk_title", _LIVE_JUNK_TITLES)
def test_h3_live_junk_titles_are_rejected(junk_title):
    """H-3 negative (the measured live failure): none of the junk that a 'Data Architect'
    query passed through the old single-token rule may pass the subset rule."""
    spec = _spec(titles=["Data Architect"], countries=["om"])
    junk = _posting(title=junk_title, country="om", city="Muscat", location="Muscat")
    assert DeterministicFilterStrategy().filter(spec, _profile(), junk) is False


@pytest.mark.parametrize(
    ("spec_title", "posting_title"),
    [
        ("Data Architect", "Data Architect"),
        ("Data Architect", "Senior Data Architect"),  # seniority tokens are stopworded
        ("Data Architect", "Lead Data Solutions Architect (Cloud)"),  # extra tokens fine
        ("Data Engineer", "Lead Data Engineer"),
        ("Data Platform Engineer", "Senior Data Platform Engineer"),
    ],
)
def test_h3_target_variants_still_pass(spec_title, posting_title):
    """H-3 positive: real variants of the target titles (seniority prefixes, extra tokens)
    must keep passing — the subset rule requires the target's tokens, not an exact string."""
    spec = _spec(titles=[spec_title])
    posting = _posting(title=posting_title)
    assert DeterministicFilterStrategy().filter(spec, _profile(), posting) is True


def test_h3_any_of_multiple_targets_suffices():
    # a "Data Engineer" posting passes a spec targeting engineer+architect (ANY target)
    spec = _spec(titles=["Data Architect", "Data Engineer"])
    posting = _posting(title="Data Engineer")
    assert DeterministicFilterStrategy().filter(spec, _profile(), posting) is True


def test_h3_partial_target_overlap_is_rejected():
    # negative: sharing ONE token of the target ("architect" but not "data") no longer passes
    spec = _spec(titles=["Data Architect"])
    posting = _posting(title="Software Architect")
    assert DeterministicFilterStrategy().filter(spec, _profile(), posting) is False


def test_h3_normalized_and_raw_titles_are_pooled():
    # tokens may come from either title field — raw says "Sr. DE", normalized carries the rest
    spec = _spec(titles=["Data Engineer"])
    posting = _posting(title="Sr. Data Wrangler")
    posting = posting.model_copy(update={"normalized_title": "Data Engineer"})
    assert DeterministicFilterStrategy().filter(spec, _profile(), posting) is True


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
        self._postings: dict[str, DissectedPosting] = dict(silver)
        self.clusters: dict[str, dict] = {}
        self.posting_cluster: dict[str, str] = {}
        self.status: dict[str, str] = {pid: "silver" for pid, _ in silver}
        # migration 0007: the decision context that produced status='rejected'.
        self.gold_filter_hash: dict[str, str | None] = {pid: None for pid, _ in silver}
        # Every read's returned ids, in call order — lets a test assert what the SECOND run
        # even looked at, which is the whole point of the rejection stamp.
        self.reads: list[list[str]] = []

    def get_silver_postings(self, *, limit=None, filter_hash=None):
        """Mirrors PostgresRepository: unjudged rows always, plus rejected rows whose stamp
        differs from the current context (a NULL stamp counts as differing)."""
        out = []
        for pid, posting in self._postings.items():
            status = self.status[pid]
            if status == "silver":
                out.append((pid, posting))
            elif (
                filter_hash is not None
                and status == "rejected"
                and self.gold_filter_hash.get(pid) != filter_hash
            ):
                out.append((pid, posting))
        if limit is not None:
            out = out[:limit]
        self.reads.append([pid for pid, _ in out])
        return out

    def mark_gold_rejected(self, posting_id, *, filter_hash):
        self.status[posting_id] = "rejected"
        self.gold_filter_hash[posting_id] = filter_hash

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


# ---------------------------------------------------- rejection lineage (migration 0007)
def test_rejected_posting_is_stamped_and_not_re_read_under_the_same_config():
    """The point of the stamp: a settled rejection stays out of the next run's candidate set.

    Without this assertion the whole optimisation can be a no-op that still passes every other
    test — the read would return everything, the filter would agree with itself, and the only
    symptom would be the 1 MB crash coming back at scale (ERR-010).
    """
    silver = [("p-fit", _posting(title="Data Engineer")), ("p-drop", _posting(title="Nurse"))]
    repo = FakeGoldRepo(silver)
    strategy = _StubStrategy({"Data Engineer": True, "Nurse": False})

    first = apply_gold_filter(
        _spec(), _profile(), strategy=strategy, repo=repo, filter_hash="hash-A"
    )

    assert first == {"silver": 2, "gold": 1, "dropped": 1}
    assert repo.status["p-drop"] == "rejected"
    assert repo.gold_filter_hash["p-drop"] == "hash-A"
    assert repo.reads[0] == ["p-fit", "p-drop"]

    # Second run, same config → the rejection is settled and the promoted row has moved on,
    # so there is nothing left to look at.
    second = apply_gold_filter(
        _spec(), _profile(), strategy=strategy, repo=repo, filter_hash="hash-A"
    )
    assert second == {"silver": 0, "gold": 0, "dropped": 0}
    assert repo.reads[1] == []


def test_config_change_reopens_the_rejected_backlog():
    """A changed decision context must re-open every rejection made under the old one.

    This is the behaviour the pre-0007 "non-fits stay silver forever" read provided by
    accident, and the reason a plain terminal status was rejected in the ADR. It is also the
    test that fails if someone stamps `compute_profile_hash` instead of `compute_filter_hash`
    — that hash does not move when `targeting.job_titles` changes.
    """
    repo = FakeGoldRepo([("p-nurse", _posting(title="Nurse"))])

    rejecting = _StubStrategy({"Nurse": False})
    apply_gold_filter(_spec(), _profile(), strategy=rejecting, repo=repo, filter_hash="hash-A")
    assert repo.status["p-nurse"] == "rejected"

    # The operator widens their targeting; the decision context changes with it.
    accepting = _StubStrategy({"Nurse": True})
    summary = apply_gold_filter(
        _spec(titles=["Nurse"]), _profile(), strategy=accepting, repo=repo,
        filter_hash="hash-B",
    )

    assert repo.reads[1] == ["p-nurse"]  # re-opened, not stranded
    assert summary == {"silver": 1, "gold": 1, "dropped": 0}
    assert repo.status["p-nurse"] == "gold_candidate"


def test_no_filter_hash_keeps_the_pre_0007_behaviour():
    # negative: without a hash nothing is ever stamped rejected — the legacy shape the
    # in-memory unit fakes and any pre-0007 caller still rely on.
    repo = FakeGoldRepo([("p-drop", _posting(title="Nurse"))])
    summary = apply_gold_filter(
        _spec(), _profile(), strategy=_StubStrategy({"Nurse": False}), repo=repo
    )
    assert summary == {"silver": 1, "gold": 0, "dropped": 1}
    assert repo.status["p-drop"] == "silver"
    assert repo.gold_filter_hash["p-drop"] is None


def test_fail_open_posting_is_never_stamped_rejected():
    # negative: a strategy that raises is a verdict we never got. Promoting it (fail-open) is
    # existing behaviour; the new requirement is that it must NOT be recorded as a decided
    # rejection, or a transient filter outage would permanently close postings.
    repo = FakeGoldRepo([("p-1", _posting())])

    class _BoomStrategy:
        def filter(self, spec, profile, posting):
            raise FilterError("cannot decide")

    apply_gold_filter(
        _spec(), _profile(), strategy=_BoomStrategy(), repo=repo, filter_hash="hash-A"
    )
    assert repo.status["p-1"] == "gold_candidate"
    assert repo.gold_filter_hash["p-1"] is None
