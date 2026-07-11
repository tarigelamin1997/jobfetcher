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
    scores (with previous_score + lineage) tracked, every save also APPENDED to `events` (the
    append-only score_event mirror). Only the methods reassess() touches."""

    def __init__(self, profile_row, targets):
        self._profile_row = profile_row
        self._targets = targets
        self.saved: dict[str, dict] = {}
        self.events: list[dict] = []  # append-only, like score_event
        self.max_age_days_seen: object = "NOT CALLED"

    def get_profile(self, user_id):
        return self._profile_row

    def get_scored_for_reassess(self, *, max_age_days=None):
        self.max_age_days_seen = max_age_days
        return list(self._targets)

    def save_score(self, *, cluster_id, score, fit_category, strengths, gaps,
                   strategic_assessment, poster_type, legitimacy_verified,
                   scoring_model, profile_hash, run_id=None, previous_score=None,
                   subscores=None):
        self.saved[cluster_id] = {
            "score": score, "fit_category": fit_category, "previous_score": previous_score,
            "scoring_model": scoring_model, "profile_hash": profile_hash, "run_id": run_id,
            "subscores": subscores,
        }
        self.events.append({"cluster_id": cluster_id, **self.saved[cluster_id]})
        return cluster_id


def _profile_row(threshold=60):
    return {"profile": _profile().model_dump(), "threshold": threshold,
            "hard_floor": 50, "near_miss_band": 10}


def _targets(prior_hash="ph-old"):
    # (posting_id, cluster_id, dissected, current_score, current_fit_category, prior_profile_hash)
    # `prior_hash` is the profile the CURRENT score came from; default "ph-old" DIFFERS from the
    # "ph-unit"/"ph-*" the tests score against, so a crossing is an HONEST graduation. Pass the
    # SAME hash as the run's profile_hash to model a same-profile (noise) crossing (no graduation).
    return [
        ("p-grad", "c-grad", _dissected("A"), 45, "misaligned", prior_hash),  # was below 60
        ("p-drop", "c-drop", _dissected("B"), 80, "strong_fit", prior_hash),  # was above 60
        ("p-same", "c-same", _dissected("C"), 90, "strong_fit", prior_hash),  # stays above
    ]


def _scorer_for(scores):
    return Scorer(FakeLlm(*[_score_json(s) for s in scores]))


def test_reassess_graduates_downgrades_and_tracks_previous_score():
    """The core replay behavior: an improved profile re-scores the scored set — one job crosses
    UP (graduated), one crosses DOWN, one stays; every save carries the old score into
    previous_score. max_workers=1 so the scripted FakeLlm maps to targets in order."""
    repo = _FakeReassessRepo(_profile_row(60), _targets())
    # new scores in target order: p-grad 45->75 (graduate), p-drop 80->40 (downgrade), p-same 90->92
    # resample_n=1: this test scripts one reply PER target, so boundary resampling is disabled to
    # keep the FakeLlm call→target mapping 1:1 (resampling is covered by its own tests below).
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_scorer_for([75, 40, 92]), max_workers=1, resample_n=1)

    assert report["reassessed"] == 3
    assert report["graduated"] == 1
    assert report["downgraded"] == 1
    assert report["unchanged"] == 1
    assert report["failed"] == 0 and report["deferred"] == 0

    # previous_score carries the OLD score on every re-score
    assert repo.saved["c-grad"]["score"] == 75
    assert repo.saved["c-grad"]["fit_category"] == "strong_fit"
    assert repo.saved["c-grad"]["previous_score"] == 45
    assert repo.saved["c-drop"]["previous_score"] == 80
    assert repo.saved["c-same"]["previous_score"] == 90

    # the graduation is reported with the old->new delta
    grads = report["graduations"]
    assert len(grads) == 1
    assert grads[0]["posting_id"] == "p-grad"
    assert grads[0]["old_score"] == 45 and grads[0]["new_score"] == 75
    assert grads[0]["new_category"] == "strong_fit"

    # the delta-distribution keys are present (additive — nothing existing renamed):
    # |75-45|=30, |40-80|=40, |92-90|=2 → one small delta, two 21+
    assert report["delta_buckets"] == {"0-5": 1, "6-10": 0, "11-20": 0, "21+": 2}
    assert report["max_delta"] == 40
    assert report["mean_delta"] == 24.0  # (30+40+2)/3


def test_reassess_never_fetches():
    """The replay guarantee (ADR-0023): reassess must touch NO source/raw-store — a repo that
    only exposes the reassess methods is sufficient. If reassess tried to fetch, it would need
    a SourceAdapter/RawStore it was never given, and this would fail loudly."""
    repo = _FakeReassessRepo(_profile_row(60), _targets()[:1])
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_scorer_for([70]), max_workers=1)
    assert report["reassessed"] == 1
    # no fetch-related attribute was ever needed on the repo
    assert not hasattr(repo, "upsert_bronze") and not hasattr(repo, "get_gold_candidates")


def test_reassess_isolates_a_scoring_failure():
    # negative: one un-scorable posting is skipped (failed), the rest still reassess
    class _OneBoom:
        model_id = "test-model"

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
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_OneBoom(), max_workers=1)
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
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_CountingScorer(), deadline=Deadline(0))
    assert report == {
        "reassessed": 0, "graduated": 0, "downgraded": 0, "unchanged": 0,
        "failed": 0, "deferred": 3, "graduations": [],
        # nothing reassessed → an all-zero distribution (honest zeros, keys still present)
        "delta_buckets": {"0-5": 0, "6-10": 0, "11-20": 0, "21+": 0},
        "max_delta": 0, "mean_delta": 0.0,
    }
    assert _CountingScorer.calls == 0
    assert repo.saved == {}
    assert repo.events == []  # nothing appended to the lineage log either


# --------------------------------------------------------------------------- score_event lineage
def test_score_then_reassess_appends_two_events():
    """The dual-write baseline (migration 0004): a first scoring then a reassess of the SAME
    cluster = TWO appended events (the log grows), while `saved` (the `score` upsert mirror)
    holds only the current judgment. The first event has previous_score None (first scoring),
    the second carries the explicit old score — each event self-contained."""
    from jobfetcher.core.ingest import score_gold

    class _FakeFullRepo(_FakeReassessRepo):
        """The reassess fake + the two gold-side methods score_gold needs."""

        def __init__(self, profile_row, candidates, targets):
            super().__init__(profile_row, targets)
            self._candidates = candidates
            self.status = {pid: "gold_candidate" for pid, _, _ in candidates}

        def get_gold_candidates(self):
            return list(self._candidates)

        def mark_scored(self, posting_id):
            self.status[posting_id] = "scored"

    repo = _FakeFullRepo(
        _profile_row(60),
        candidates=[("p-1", "c-1", _dissected("A"))],
        targets=[("p-1", "c-1", _dissected("A"), 55, "near_miss", "ph-before")],
    )
    score_gold(run_id="r1", repo=repo, profile_hash="ph-before",
               scorer=_scorer_for([55]), max_workers=1)
    reassess(run_id="r2", repo=repo, profile_hash="ph-after",
             scorer=_scorer_for([75]), max_workers=1)

    assert len(repo.events) == 2  # append-only: one event per scoring, nothing overwritten
    first, second = repo.events
    assert first["cluster_id"] == second["cluster_id"] == "c-1"
    assert first["score"] == 55 and first["previous_score"] is None  # first scoring
    assert second["score"] == 75 and second["previous_score"] == 55  # reassess carries the old
    assert (first["profile_hash"], second["profile_hash"]) == ("ph-before", "ph-after")
    assert (first["run_id"], second["run_id"]) == ("r1", "r2")
    # the current-judgment mirror holds ONE entry — the latest
    assert repo.saved["c-1"]["score"] == 75


def test_reassess_delta_distribution_bucket_edges_and_mean():
    """The bucket edges pinned on a crafted delta set: deltas 5, 6, 10, 11, 20, 21 land in
    0-5 / 6-10 / 6-10 / 11-20 / 11-20 / 21+ respectively (inclusive upper bounds); max=21;
    mean=(5+6+10+11+20+21)/6=12.1666… → 12.2 (1 decimal)."""
    targets = [
        (f"p-{i}", f"c-{i}", _dissected(chr(65 + i)), old, "near_miss", "ph-old")
        for i, old in enumerate([50, 50, 50, 50, 50, 50])
    ]
    # new scores craft |new − old| = 5, 6, 10, 11, 20, 21 (mixing up- and down-moves to
    # prove the distribution is over ABSOLUTE deltas). resample_n=1: one scripted reply per
    # target, so boundary resampling is disabled to keep the call→target mapping exact.
    new_scores = [55, 44, 60, 39, 70, 29]
    repo = _FakeReassessRepo(_profile_row(60), targets)
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_scorer_for(new_scores), max_workers=1, resample_n=1)
    assert report["reassessed"] == 6
    assert report["delta_buckets"] == {"0-5": 1, "6-10": 2, "11-20": 2, "21+": 1}
    assert report["max_delta"] == 21
    assert report["mean_delta"] == 12.2


def test_reassess_delta_distribution_excludes_failures():
    """The distribution covers only SUCCESSFUL reassessments: with the middle target failing,
    its would-be delta never pollutes the buckets/mean (3 targets, 1 failed → 2 deltas)."""
    class _MiddleBoom:
        model_id = "test-model"

        def __init__(self):
            self._n = 0

        def score(self, dissected, profile):
            self._n += 1
            from jobfetcher.core.models import ScoreResult
            from jobfetcher.core.scorer import ScorerError
            if self._n == 2:
                raise ScorerError("boom")
            return ScoreResult.model_validate(json.loads(_score_json(48)))

    # olds 45/80/90; successes score 48 → deltas |48-45|=3 and |48-90|=42 (the failed 80 absent).
    # resample_n=1 so _MiddleBoom's 2nd call maps to the 2nd TARGET (not a resample of the 1st).
    repo = _FakeReassessRepo(_profile_row(60), _targets())
    report = reassess(run_id="r", repo=repo, profile_hash="ph-unit",
                      scorer=_MiddleBoom(), max_workers=1, resample_n=1)
    assert report["failed"] == 1 and report["reassessed"] == 2
    assert report["delta_buckets"] == {"0-5": 1, "6-10": 0, "11-20": 0, "21+": 1}
    assert report["max_delta"] == 42
    assert report["mean_delta"] == 22.5  # (3+42)/2


def test_reassess_threads_subscores_into_save_score():
    """Migration 0006 threading: a reply WITH all 7 subscores → the saved row + appended
    event carry the blob (7 factors + code_total + llm_total); a reply WITHOUT → None."""
    from jobfetcher.core.scorer import FACTOR_WEIGHTS

    subs = {name: 70 for name in FACTOR_WEIGHTS}
    repo = _FakeReassessRepo(_profile_row(60), _targets()[:2])
    scorer = Scorer(FakeLlm(_score_json(75, **subs), _score_json(80)))
    # resample_n=1: one scripted reply per target — disable resampling so each target's blob is
    # its own reply (resampling would mix the two replies and pick a median of a different one).
    reassess(run_id="r", repo=repo, profile_hash="ph-unit", scorer=scorer, max_workers=1,
             resample_n=1)
    with_subs = repo.saved["c-grad"]["subscores"]
    assert with_subs == {**subs, "code_total": 70, "llm_total": 75}
    assert repo.saved["c-drop"]["subscores"] is None  # omitted → NULL, never partial
    assert [e["subscores"] for e in repo.events] == [with_subs, None]  # events self-contained


def test_reassess_passes_max_age_days_through():
    """The age bound reaches the repository: `max_age_days` is forwarded verbatim to
    `get_scored_for_reassess` — 45 stays 45, and the None/0 defaults stay unbounded."""
    repo = _FakeReassessRepo(_profile_row(60), _targets()[:1])
    reassess(run_id="r", repo=repo, profile_hash="ph-unit",
             scorer=_scorer_for([70]), max_workers=1, max_age_days=45)
    assert repo.max_age_days_seen == 45

    # regression: no arg → None (unbounded, the pre-0004 behavior)
    repo2 = _FakeReassessRepo(_profile_row(60), _targets()[:1])
    reassess(run_id="r", repo=repo2, profile_hash="ph-unit",
             scorer=_scorer_for([70]), max_workers=1)
    assert repo2.max_age_days_seen is None

    # regression: 0 is passed through too — the repository treats it as unbounded
    repo3 = _FakeReassessRepo(_profile_row(60), _targets()[:1])
    reassess(run_id="r", repo=repo3, profile_hash="ph-unit",
             scorer=_scorer_for([70]), max_workers=1, max_age_days=0)
    assert repo3.max_age_days_seen == 0


# --------------------------------------------------------------------------- honest graduations
def test_reassess_graduation_requires_a_profile_change():
    """Positive: the prior score came from a DIFFERENT profile ('ph-old' != 'ph-new') and the
    re-score crosses the bar (45 -> 75) → a REAL graduation is counted + listed."""
    repo = _FakeReassessRepo(_profile_row(60), _targets(prior_hash="ph-old")[:1])  # p-grad, old 45
    report = reassess(run_id="r", repo=repo, profile_hash="ph-new",
                      scorer=_scorer_for([75]), max_workers=1, resample_n=1)
    assert report["graduated"] == 1
    assert report["unchanged"] == 0
    assert [g["posting_id"] for g in report["graduations"]] == ["p-grad"]


def test_reassess_same_profile_crossing_is_not_a_graduation():
    """The exact scan-bug negative: previous_score < threshold <= new_score BUT the prior score
    came from the SAME profile ('ph-same' == 'ph-same') → the crossing is LLM sampling noise, not
    a skill gain: NO graduation counted, an EMPTY graduations list, folded into `unchanged`. The
    posting is still reassessed + saved (only the graduation JUDGMENT changes)."""
    repo = _FakeReassessRepo(_profile_row(60), _targets(prior_hash="ph-same")[:1])  # p-grad, old 45
    report = reassess(run_id="r", repo=repo, profile_hash="ph-same",
                      scorer=_scorer_for([75]), max_workers=1, resample_n=1)
    assert report["graduated"] == 0
    assert report["graduations"] == []
    assert report["unchanged"] == 1  # the crossing folds into unchanged, never announced
    assert report["reassessed"] == 1
    assert repo.saved["c-grad"]["score"] == 75  # the score itself still updates


# --------------------------------------------------------------------------- boundary resample
def test_reassess_resamples_boundary_and_keeps_median():
    """Boundary self-consistency in the replay path: a first re-score near the threshold (62,
    within the 16-pt margin of 60) is resampled to N=3 total and the MEDIAN sample is persisted
    (median of 62/58/70 = 62) — the LLM is called exactly 3× for the one boundary posting."""
    repo = _FakeReassessRepo(_profile_row(60), _targets()[:1])  # p-grad, old 45
    llm = FakeLlm(_score_json(62), _score_json(58), _score_json(70))
    reassess(run_id="r", repo=repo, profile_hash="ph-unit", scorer=Scorer(llm), max_workers=1)
    assert repo.saved["c-grad"]["score"] == 62  # the median of the three boundary samples
    assert len(llm.calls) == 3  # resampled to N total for the boundary posting
