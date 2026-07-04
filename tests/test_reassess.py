"""Reassess/replay unit tests (LLM mocked, no DB, no fetch): re-scoring the already-scored set
against the current profile updates `previous_score`, graduates a job that crosses the threshold
upward, downgrades one that crosses down, isolates a scoring failure, and defers on a passed
deadline. The critical negative: **no SourceAdapter/RawStore is ever touched** — replay must
never re-fetch. Reuses the scorer-test builders."""
from __future__ import annotations

import json

from jobfetcher.core.ingest import Deadline, reassess
from jobfetcher.core.scorer import Scorer
from tests.helpers import FakeLlm
from tests.test_scorer import _dissected, _profile, _score_json


class _FakeReassessRepo:
    """In-memory repo for the reassess orchestration: a profile + the scored set in, the saved
    scores (with previous_score) tracked. Only the methods reassess() touches."""

    def __init__(self, profile_row, targets):
        self._profile_row = profile_row
        self._targets = targets
        self.saved: dict[str, dict] = {}

    def get_profile(self, user_id):
        return self._profile_row

    def get_scored_for_reassess(self):
        return list(self._targets)

    def save_score(self, *, cluster_id, score, fit_category, strengths, gaps,
                   strategic_assessment, poster_type, legitimacy_verified, previous_score=None):
        self.saved[cluster_id] = {
            "score": score, "fit_category": fit_category, "previous_score": previous_score,
        }
        return cluster_id


def _profile_row(threshold=60):
    return {"profile": _profile().model_dump(), "threshold": threshold,
            "hard_floor": 50, "near_miss_band": 10}


def _targets():
    # (posting_id, cluster_id, dissected, current_score, current_fit_category)
    return [
        ("p-grad", "c-grad", _dissected("A"), 45, "misaligned"),   # was below 60
        ("p-drop", "c-drop", _dissected("B"), 80, "strong_fit"),   # was above 60
        ("p-same", "c-same", _dissected("C"), 90, "strong_fit"),   # stays above
    ]


def _scorer_for(scores):
    return Scorer(FakeLlm(*[_score_json(s) for s in scores]))


def test_reassess_graduates_downgrades_and_tracks_previous_score():
    """The core replay behavior: an improved profile re-scores the scored set — one job crosses
    UP (graduated), one crosses DOWN, one stays; every save carries the old score into
    previous_score. max_workers=1 so the scripted FakeLlm maps to targets in order."""
    repo = _FakeReassessRepo(_profile_row(60), _targets())
    # new scores in target order: p-grad 45->75 (graduate), p-drop 80->40 (downgrade), p-same 90->92
    report = reassess(run_id="r", repo=repo, scorer=_scorer_for([75, 40, 92]), max_workers=1)

    assert report["reassessed"] == 3
    assert report["graduated"] == 1
    assert report["downgraded"] == 1
    assert report["unchanged"] == 1
    assert report["failed"] == 0 and report["deferred"] == 0

    # previous_score carries the OLD score on every re-score
    assert repo.saved["c-grad"] == {"score": 75, "fit_category": "strong_fit", "previous_score": 45}
    assert repo.saved["c-drop"]["previous_score"] == 80
    assert repo.saved["c-same"]["previous_score"] == 90

    # the graduation is reported with the old->new delta
    grads = report["graduations"]
    assert len(grads) == 1
    assert grads[0]["posting_id"] == "p-grad"
    assert grads[0]["old_score"] == 45 and grads[0]["new_score"] == 75
    assert grads[0]["new_category"] == "strong_fit"


def test_reassess_never_fetches():
    """The replay guarantee (ADR-0023): reassess must touch NO source/raw-store — a repo that
    only exposes the reassess methods is sufficient. If reassess tried to fetch, it would need
    a SourceAdapter/RawStore it was never given, and this would fail loudly."""
    repo = _FakeReassessRepo(_profile_row(60), _targets()[:1])
    report = reassess(run_id="r", repo=repo, scorer=_scorer_for([70]), max_workers=1)
    assert report["reassessed"] == 1
    # no fetch-related attribute was ever needed on the repo
    assert not hasattr(repo, "upsert_bronze") and not hasattr(repo, "get_gold_candidates")


def test_reassess_isolates_a_scoring_failure():
    # negative: one un-scorable posting is skipped (failed), the rest still reassess
    class _OneBoom:
        def __init__(self):
            self._n = 0

        def score(self, dissected, profile):
            self._n += 1
            from jobfetcher.core.models import ScoreResult
            from jobfetcher.core.scorer import ScorerError
            if self._n == 2:
                raise ScorerError("boom")
            return ScoreResult.model_validate(json.loads(_score_json(75)))

    repo = _FakeReassessRepo(_profile_row(60), _targets())
    report = reassess(run_id="r", repo=repo, scorer=_OneBoom(), max_workers=1)
    assert report["failed"] == 1
    assert report["reassessed"] == 2  # the other two still went through


def test_reassess_defers_on_expired_deadline():
    # negative: an expired deadline → nothing re-scored, nothing saved, all deferred
    class _CountingScorer:
        calls = 0

        def score(self, dissected, profile):
            type(self).calls += 1
            return None  # never reached

    repo = _FakeReassessRepo(_profile_row(60), _targets())
    report = reassess(run_id="r", repo=repo, scorer=_CountingScorer(), deadline=Deadline(0))
    assert report == {
        "reassessed": 0, "graduated": 0, "downgraded": 0, "unchanged": 0,
        "failed": 0, "deferred": 3, "graduations": [],
    }
    assert _CountingScorer.calls == 0
    assert repo.saved == {}
