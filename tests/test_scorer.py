"""Scorer unit tests (LLM mocked, no DB): the `ScoreResult` contract + negatives, the Scorer
with a FakeLlm (happy path, retry, exhausted retry, missing field), the prompt content, and
the `fit_category`/surfaced derivation (VG2 + VG8). Each carries a negative."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from jobfetcher.core.ingest import derive_fit_category, score_gold
from jobfetcher.core.models import DissectedPosting, ScoreResult, Skill
from jobfetcher.core.profile import Profile
from jobfetcher.core.scorer import (
    FACTOR_WEIGHTS,
    SCORING_SYSTEM_PROMPT,
    Scorer,
    ScorerError,
    compute_code_total,
    subscores_payload,
)
from jobfetcher.core.ports import LlmError
from tests.helpers import FakeLlm


# --------------------------------------------------------------------------- builders
def _profile() -> Profile:
    return Profile.model_validate(
        {
            "name": "Tester",
            "headline": "Data Engineer",
            "skills": [{"name": "Python", "level": "expert"}, {"name": "SQL"}],
            "certifications": [{"name": "AWS SAA"}],
            "projects": [{"name": "OrderFlow", "summary": "streaming pipeline"}],
            "preferences": {
                "target_titles": ["Data Engineer"],
                "target_locations": ["Riyadh"],
                "avoid_keywords": [],
            },
        }
    )


def _dissected(title: str = "Data Engineer") -> DissectedPosting:
    return DissectedPosting(
        raw_title=title,
        language="en",
        location="Riyadh",
        city="Riyadh",
        country="sa",
        seniority="mid",
        normalized_title=title,
        sector="fintech",
        skills=[Skill(name="Python", level="must", evidence="Python")],
        model="test-model",
    )


def _score_json(score: int, **over) -> str:
    payload = {
        "score": score,
        "strengths": ["strong Python", "fintech background"],
        "gaps": ["no Spark"],
        "strategic_assessment": "A solid fit; lead with the pipeline project.",
        "poster_type": "direct employer",
        "legitimacy_verified": True,
    }
    payload.update(over)
    return json.dumps(payload)


def _subscores(value: int = 80, **over) -> dict[str, int]:
    """All 7 subscore keys at `value` (uniform → the weighted total is exactly `value`)."""
    subs = {name: value for name in FACTOR_WEIGHTS}
    subs.update(over)
    return subs


# --------------------------------------------------------------------------- ScoreResult contract
def test_score_result_happy():
    r = ScoreResult.model_validate(json.loads(_score_json(82)))
    assert r.score == 82 and r.legitimacy_verified is True
    assert r.strengths and r.gaps and r.strategic_assessment


def test_score_result_rejects_out_of_range_score():
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(json.loads(_score_json(150)))


def test_score_result_rejects_missing_required_field():
    # negative: no strategic_assessment → invalid
    bad = json.loads(_score_json(70))
    del bad["strategic_assessment"]
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(bad)


def test_score_result_tolerates_extra_keys():
    r = ScoreResult.model_validate({**json.loads(_score_json(60)), "chatter": "ignored"})
    assert r.score == 60


def test_score_result_accepts_all_seven_subscores():
    r = ScoreResult.model_validate(json.loads(_score_json(82, **_subscores(75))))
    assert all(getattr(r, name) == 75 for name in FACTOR_WEIGHTS)
    assert r.score == 82  # the holistic stays independent of the subscores


def test_score_result_subscores_default_to_none():
    # regression: a pre-subscore reply (no subscore keys at all) still validates — optional.
    r = ScoreResult.model_validate(json.loads(_score_json(70)))
    assert all(getattr(r, name) is None for name in FACTOR_WEIGHTS)


@pytest.mark.parametrize("bad_value", [-1, 101])
@pytest.mark.parametrize("field", sorted(FACTOR_WEIGHTS))
def test_score_result_rejects_out_of_range_subscore(field, bad_value):
    # negative: ANY subscore out of 0-100 → ValidationError (the existing ScorerError/skip
    # path downstream) — bounds are enforced at validation, compute_code_total never re-checks.
    bad = json.loads(_score_json(70, **_subscores(80, **{field: bad_value})))
    with pytest.raises(ValidationError):
        ScoreResult.model_validate(bad)


# --------------------------------------------------------------------------- compute_code_total
def test_factor_weights_sum_to_exactly_one_and_mirror_the_model():
    """The module-load assertion's invariant, pinned: the documented weights (ADR-0028-to-be)
    sum to exactly 1.0, and every weight key is a real `ScoreResult` subscore field."""
    assert sum(FACTOR_WEIGHTS.values()) == 1.0
    assert set(FACTOR_WEIGHTS) == {
        "core_skill_match", "tool_tech_alignment", "achievement_relevance",
        "seniority_scope", "ats_keyword_coverage", "domain_sector_fit", "realistic_fit",
    }
    for name in FACTOR_WEIGHTS:
        assert name in ScoreResult.model_fields


def test_compute_code_total_exact_weighted_sum():
    """Hand-computed: .30*85 + .20*90 + .15*77 + .15*63 + .10*52 + .05*41 + .05*33
    = 25.5 + 18 + 11.55 + 9.45 + 5.2 + 2.05 + 1.65 = 73.4 → rounds to 73."""
    r = ScoreResult.model_validate(json.loads(_score_json(
        70,
        core_skill_match=85, tool_tech_alignment=90, achievement_relevance=77,
        seniority_scope=63, ats_keyword_coverage=52, domain_sector_fit=41, realistic_fit=33,
    )))
    assert compute_code_total(r) == (73, "complete")


def test_compute_code_total_rounding_rule_is_pinned_half_to_even():
    """Python `round` = banker's rounding, pinned in BOTH directions: a weighted sum of
    73.5 → 74 (up to even) and 72.5 → 72 (down to even). Hand-computed:
    73.5 = 27 + 16 + 10.5 + 10.5 + 5 + 2 + 2.5 ; 72.5 = 27 + 16 + 10.5 + 9 + 5 + 2 + 3."""
    up = ScoreResult.model_validate(json.loads(_score_json(
        70,
        core_skill_match=90, tool_tech_alignment=80, achievement_relevance=70,
        seniority_scope=70, ats_keyword_coverage=50, domain_sector_fit=40, realistic_fit=50,
    )))
    assert compute_code_total(up) == (74, "complete")
    down = ScoreResult.model_validate(json.loads(_score_json(
        70,
        core_skill_match=90, tool_tech_alignment=80, achievement_relevance=70,
        seniority_scope=60, ats_keyword_coverage=50, domain_sector_fit=40, realistic_fit=60,
    )))
    assert compute_code_total(down) == (72, "complete")


@pytest.mark.parametrize("missing", sorted(FACTOR_WEIGHTS))
def test_compute_code_total_any_missing_subscore_fails_open(missing):
    # negative: ANY single missing subscore → (None, "subscores_missing"), never a raise.
    subs = _subscores(80)
    del subs[missing]
    r = ScoreResult.model_validate(json.loads(_score_json(70, **subs)))
    assert compute_code_total(r) == (None, "subscores_missing")


def test_compute_code_total_all_missing_fails_open():
    r = ScoreResult.model_validate(json.loads(_score_json(70)))
    assert compute_code_total(r) == (None, "subscores_missing")


def test_subscores_payload_full_and_missing():
    """The persisted JSONB shape: the 7 factors + code_total + llm_total when complete;
    None (→ SQL NULL, never a partial dict) when ANY subscore was omitted."""
    full = ScoreResult.model_validate(json.loads(_score_json(70, **_subscores(90))))
    payload = subscores_payload(full)
    assert payload == {**_subscores(90), "code_total": 90, "llm_total": 70}

    partial = _subscores(90)
    del partial["realistic_fit"]
    r = ScoreResult.model_validate(json.loads(_score_json(70, **partial)))
    assert subscores_payload(r) is None


# --------------------------------------------------------------------------- Scorer
def test_scorer_happy_path():
    out = Scorer(FakeLlm(_score_json(88)), model_id="scoring-model").score(_dissected(), _profile())
    assert out.score == 88
    assert out.strengths and out.gaps and out.strategic_assessment
    assert out.poster_type == "direct employer"


def test_scorer_retries_then_succeeds():
    llm = FakeLlm("not json at all", _score_json(75))
    out = Scorer(llm).score(_dissected(), _profile())
    assert len(llm.calls) == 2  # retried exactly once
    assert out.score == 75


def test_scorer_bad_json_after_retry_raises():
    with pytest.raises(ScorerError):
        Scorer(FakeLlm("nope", "still nope")).score(_dissected(), _profile())


def test_scorer_missing_field_raises():
    # negative: a structurally-valid JSON missing a required field → ScorerError (not silent)
    bad = json.loads(_score_json(70))
    del bad["poster_type"]
    with pytest.raises(ScorerError):
        Scorer(FakeLlm(json.dumps(bad), json.dumps(bad))).score(_dissected(), _profile())


def test_scorer_prompt_includes_profile_and_dissected_fields():
    llm = FakeLlm(_score_json(80))
    Scorer(llm).score(_dissected("Senior Data Engineer"), _profile())
    user = llm.calls[0]["user"]
    system = llm.calls[0]["system"]
    # the dissected fields are in the prompt
    assert "Senior Data Engineer" in user and "fintech" in user and "Riyadh" in user
    # the profile is in the prompt (skills + projects)
    assert "Python" in user and "OrderFlow" in user
    # the 7-factor framework + JSON-only contract is in the system prompt
    assert "7-factor" in system or "Core-skill match" in system
    assert "legitimacy_verified" in system


def test_prompt_regression_all_output_fields_present():
    """Prompt regression (cheap string asserts): the system prompt still requires every
    pre-existing output field AND the 7 new subscore keys — extending for subscores must
    never drop an existing instruction/field."""
    for pre_existing in (
        '"score"', '"strengths"', '"gaps"', '"strategic_assessment"',
        '"poster_type"', '"legitimacy_verified"',
    ):
        assert pre_existing in SCORING_SYSTEM_PROMPT
    for subscore_key in FACTOR_WEIGHTS:  # the 7 new keys, each named in the JSON contract
        assert f'"{subscore_key}"' in SCORING_SYSTEM_PROMPT
    # the factor descriptions (the definition of each subscore) survived too
    for factor_phrase in (
        "Core-skill match", "Tool/tech alignment", "Achievement relevance",
        "Seniority/scope", "ATS-keyword coverage", "Domain/sector fit", "Realistic fit",
    ):
        assert factor_phrase in SCORING_SYSTEM_PROMPT
    # and the JSON-only + holistic-judgment instructions are intact
    assert "Return ONLY a single JSON object" in SCORING_SYSTEM_PROMPT
    assert "holistic judgment" in SCORING_SYSTEM_PROMPT


def test_scorer_logs_shadow_line_info_and_warning(caplog):
    """SHADOW mode observability: every validated score logs `score shadow:` — INFO when
    |llm − code| <= 20 (or subscores missing), WARNING when the divergence exceeds 20."""
    import logging

    # Order-independence (belt to the migrations/env.py fix): a programmatic alembic run
    # earlier in the SAME process (the migration integration tests) used to
    # fileConfig()-DISABLE every already-imported app logger — a disabled logger emits zero
    # records and this test would fail only in full-suite ordering. Fixed at the source
    # (disable_existing_loggers=False); re-enable defensively here so this test never
    # depends on what earlier tests did to global logging state.
    logging.getLogger("jobfetcher.core.scorer").disabled = False
    caplog.set_level(logging.INFO, logger="jobfetcher.core.scorer")

    with caplog.at_level(logging.INFO, logger="jobfetcher.core.scorer"):
        Scorer(FakeLlm(_score_json(88, **_subscores(88)))).score(_dissected(), _profile())
    rec = next(r for r in caplog.records if "score shadow" in r.message)
    assert rec.levelno == logging.INFO
    assert "llm=88" in rec.getMessage() and "code=88" in rec.getMessage()
    assert "status=complete" in rec.getMessage()

    caplog.clear()
    # llm=50 vs code=90 → |delta|=40 > 20 → the WARNING variant
    with caplog.at_level(logging.INFO, logger="jobfetcher.core.scorer"):
        Scorer(FakeLlm(_score_json(50, **_subscores(90)))).score(_dissected(), _profile())
    rec = next(r for r in caplog.records if "score shadow" in r.message)
    assert rec.levelno == logging.WARNING
    assert "delta=40" in rec.getMessage()

    caplog.clear()
    # the boundary pinned, both edges: |delta| == 20 is NOT a warning (the rule is strictly
    # "> 20"), |delta| == 21 is — llm=70/code=90 vs llm=69/code=90.
    with caplog.at_level(logging.INFO, logger="jobfetcher.core.scorer"):
        Scorer(FakeLlm(_score_json(70, **_subscores(90)))).score(_dissected(), _profile())
    rec = next(r for r in caplog.records if "score shadow" in r.message)
    assert rec.levelno == logging.INFO
    assert "delta=20" in rec.getMessage()

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="jobfetcher.core.scorer"):
        Scorer(FakeLlm(_score_json(69, **_subscores(90)))).score(_dissected(), _profile())
    rec = next(r for r in caplog.records if "score shadow" in r.message)
    assert rec.levelno == logging.WARNING
    assert "delta=21" in rec.getMessage()

    caplog.clear()
    # no subscores → INFO with code=None / status=subscores_missing (never a raise/skip)
    with caplog.at_level(logging.INFO, logger="jobfetcher.core.scorer"):
        Scorer(FakeLlm(_score_json(70))).score(_dissected(), _profile())
    rec = next(r for r in caplog.records if "score shadow" in r.message)
    assert rec.levelno == logging.INFO
    assert "code=None" in rec.getMessage() and "status=subscores_missing" in rec.getMessage()


# --------------------------------------------------------------------------- VG2 (behavioral)
def test_vg2_high_score_is_strong_fit_with_explanation():
    """A high-score reply → strong_fit + non-empty strengths/gaps/assessment."""
    out = Scorer(FakeLlm(_score_json(85))).score(_dissected(), _profile())
    cat = derive_fit_category(out.score, threshold=60, hard_floor=50, near_miss_band=10)
    assert cat == "strong_fit"
    assert out.strengths and out.gaps and out.strategic_assessment.strip()


def test_vg2_low_score_is_misaligned():
    """The negative: a clearly-misaligned (low) score lands below the floor → misaligned —
    proves the routing discriminates, not just returns strong_fit."""
    out = Scorer(FakeLlm(_score_json(20))).score(_dissected("Registered Nurse"), _profile())
    cat = derive_fit_category(out.score, threshold=60, hard_floor=50, near_miss_band=10)
    assert cat == "misaligned"


# --------------------------------------------------------------------------- VG8 (threshold is config)
@pytest.mark.parametrize(
    "score, threshold, expected",
    [
        (60, 60, "strong_fit"),   # at threshold
        (59, 60, "near_miss"),    # 50-59 band (threshold-band .. threshold)
        (55, 60, "near_miss"),
        (49, 60, "misaligned"),   # below floor 50
        (75, 0, "strong_fit"),    # threshold 0 → everything is strong_fit
        (100, 101, "near_miss"),  # threshold above all → nothing strong (within band)
    ],
)
def test_derive_fit_category_bands(score, threshold, expected):
    # near_miss_band=10, hard_floor=50.
    assert (
        derive_fit_category(score, threshold=threshold, hard_floor=50, near_miss_band=10)
        == expected
    )


def test_derive_fit_category_stretch_band():
    # "stretch" = clears the hard floor but sits below the near-miss band. It only exists when
    # threshold - near_miss_band > hard_floor: threshold 70, band 10 → near band 60-69; floor
    # 50 → 50-59 is the stretch slice (real but distant), distinct from near_miss & misaligned.
    assert derive_fit_category(55, threshold=70, hard_floor=50, near_miss_band=10) == "stretch"
    assert derive_fit_category(65, threshold=70, hard_floor=50, near_miss_band=10) == "near_miss"
    assert derive_fit_category(45, threshold=70, hard_floor=50, near_miss_band=10) == "misaligned"


class _FakeScoreRepo:
    """In-memory repo for the score_gold orchestration: a profile + gold candidates in, the
    saved scores + status transitions tracked. Only the methods score_gold touches."""

    def __init__(self, profile_row, candidates, replies):
        self._profile_row = profile_row
        self._candidates = candidates
        self.saved: dict[str, dict] = {}
        self.status: dict[str, str] = {pid: "gold_candidate" for pid, _, _ in candidates}

    def get_profile(self, user_id):
        return self._profile_row

    def get_gold_candidates(self):
        return list(self._candidates)

    def save_score(self, *, cluster_id, score, fit_category, strengths, gaps,
                   strategic_assessment, poster_type, legitimacy_verified,
                   scoring_model, profile_hash, run_id=None, previous_score=None,
                   subscores=None):
        self.saved[cluster_id] = {
            "score": score, "fit_category": fit_category,
            "scoring_model": scoring_model, "profile_hash": profile_hash, "run_id": run_id,
            "subscores": subscores,
        }
        return cluster_id

    def mark_scored(self, posting_id):
        self.status[posting_id] = "scored"


def _profile_row(threshold):
    return {
        "profile": _profile().model_dump(),
        "threshold": threshold,
        "hard_floor": 50,
        "near_miss_band": 10,
    }


def _candidates():
    # three gold candidates with fixed scores 30, 65, 90 (the FakeLlm returns them in order)
    return [
        ("p-low", "c-low", _dissected("A")),
        ("p-mid", "c-mid", _dissected("B")),
        ("p-high", "c-high", _dissected("C")),
    ]


def _scorer_for(scores):
    return Scorer(FakeLlm(*[_score_json(s) for s in scores]))


def test_vg8_threshold_0_surfaces_all():
    repo = _FakeScoreRepo(_profile_row(0), _candidates(), None)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit",
                         scorer=_scorer_for([30, 65, 90]), max_workers=1)
    assert summary == {"gold": 3, "scored": 3, "surfaced": 3, "failed": 0, "deferred": 0}
    assert all(v["fit_category"] == "strong_fit" for v in repo.saved.values())


def test_vg8_threshold_above_all_surfaces_none():
    repo = _FakeScoreRepo(_profile_row(101), _candidates(), None)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit",
                         scorer=_scorer_for([30, 65, 90]), max_workers=1)
    assert summary["surfaced"] == 0
    assert not any(v["fit_category"] == "strong_fit" for v in repo.saved.values())


def test_vg8_threshold_60_splits_in_between():
    # SAME scores (30, 65, 90), only the config threshold changes → a DIFFERENT surfaced set,
    # with NO code change. This is the VG8 proof.
    repo = _FakeScoreRepo(_profile_row(60), _candidates(), None)
    # max_workers=1: the FakeLlm replies map to candidates by call order (order-sensitive)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit",
                         scorer=_scorer_for([30, 65, 90]), max_workers=1)
    assert summary["surfaced"] == 2  # 65 and 90 clear 60; 30 does not
    assert repo.saved["c-high"]["fit_category"] == "strong_fit"
    assert repo.saved["c-mid"]["fit_category"] == "strong_fit"
    assert repo.saved["c-low"]["fit_category"] == "misaligned"


def test_score_gold_skips_failed_scoring_and_continues():
    # a scoring failure (bad JSON twice for the middle candidate) → logged + skipped, the run
    # continues and scores the rest (mirrors land_silver).
    class _OneBoomScorer:
        model_id = "test-model"

        def __init__(self):
            self._n = 0

        def score(self, dissected, profile):
            self._n += 1
            if self._n == 2:
                raise ScorerError("boom")
            return ScoreResult.model_validate(json.loads(_score_json(80)))

    repo = _FakeScoreRepo(_profile_row(60), _candidates(), None)
    # max_workers=1: "the 2nd call" must map to p-mid deterministically (order-sensitive)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit",
                         scorer=_OneBoomScorer(), max_workers=1)
    assert summary == {"gold": 3, "scored": 2, "surfaced": 2, "failed": 1, "deferred": 0}
    assert repo.status["p-mid"] == "gold_candidate"  # the failed one is NOT marked scored


def test_score_gold_skips_llm_transport_error():
    class _BoomScorer:
        def score(self, dissected, profile):
            raise LlmError("transport down")

    repo = _FakeScoreRepo(_profile_row(60), [("p", "c", _dissected())], None)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit", scorer=_BoomScorer())
    assert summary == {"gold": 1, "scored": 0, "surfaced": 0, "failed": 1, "deferred": 0}


def test_score_gold_defers_on_expired_deadline():
    """H-2 negative: an expired deadline → all candidates deferred, no scorer calls, no saves;
    the run returns cleanly for the idempotent re-run to finish."""
    from jobfetcher.core.ingest import Deadline

    class _CountingScorer:
        calls = 0

        def score(self, dissected, profile):
            type(self).calls += 1
            return ScoreResult.model_validate(json.loads(_score_json(80)))

    repo = _FakeScoreRepo(_profile_row(60), _candidates(), None)
    summary = score_gold(
        run_id="r", repo=repo, profile_hash="ph-unit", scorer=_CountingScorer(), deadline=Deadline(0)
    )
    assert summary == {"gold": 3, "scored": 0, "surfaced": 0, "failed": 0, "deferred": 3}
    assert _CountingScorer.calls == 0
    assert repo.saved == {}  # nothing written
    assert all(v == "gold_candidate" for v in repo.status.values())  # nothing marked


def test_score_gold_uses_default_knobs_when_null():
    # a profile row with NULL knobs → the documented defaults (60/50/10) apply at runtime
    row = {"profile": _profile().model_dump(), "threshold": None,
           "hard_floor": None, "near_miss_band": None}
    repo = _FakeScoreRepo(row, _candidates(), None)
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph-unit",
                         scorer=_scorer_for([30, 65, 90]), max_workers=1)
    assert summary["surfaced"] == 2  # default threshold 60 → 65 & 90 surface


# --------------------------------------------------------------------------- score_event lineage
def test_score_gold_threads_lineage_into_save_score():
    """Migration 0004: every save carries the scorer's model id, the caller's profile_hash, and
    the run's correlation id — the lineage `save_score` stamps on the `score_event` row."""
    repo = _FakeScoreRepo(_profile_row(60), _candidates(), None)
    scorer = Scorer(FakeLlm(*[_score_json(s) for s in [30, 65, 90]]), model_id="pro-model")
    score_gold(run_id="run-lineage", repo=repo, profile_hash="ph-a", scorer=scorer, max_workers=1)
    assert all(
        v["scoring_model"] == "pro-model" and v["profile_hash"] == "ph-a"
        and v["run_id"] == "run-lineage"
        for v in repo.saved.values()
    )


def test_shadow_invariant_llm_score_is_persisted_untouched():
    """THE shadow invariant (ADR-0028-to-be): llm=55 with subscores computing code=90 —
    values chosen so llm and code land in DIFFERENT bands AND on different sides of the
    threshold (60, band 10), so ALL THREE assertions discriminate a code_total leak on
    every consumer path: persisted score 55 (not 90), fit_category near_miss (code 90
    would be strong_fit), surfaced 0 (code 90 would clear the cut). code_total rides
    ONLY inside the subscores blob, never substituted."""
    repo = _FakeScoreRepo(_profile_row(60), _candidates()[:1], None)
    scorer = Scorer(FakeLlm(_score_json(55, **_subscores(90))))
    summary = score_gold(run_id="r", repo=repo, profile_hash="ph", scorer=scorer, max_workers=1)
    saved = repo.saved["c-low"]
    assert saved["score"] == 55  # NOT 90 — shadow mode never touches the product number
    assert saved["subscores"]["code_total"] == 90
    assert saved["subscores"]["llm_total"] == 55
    assert saved["fit_category"] == "near_miss"  # banding ran on llm 55 — code 90 → strong_fit
    assert summary["surfaced"] == 0  # threshold cut ran on llm 55 — code 90 would surface


def test_score_gold_persists_null_subscores_when_llm_omits_them():
    # negative twin: a reply without subscores → subscores=None reaches save_score (NULL
    # column), while the score itself still persists normally.
    repo = _FakeScoreRepo(_profile_row(60), _candidates()[:1], None)
    score_gold(run_id="r", repo=repo, profile_hash="ph",
               scorer=_scorer_for([70]), max_workers=1)
    assert repo.saved["c-low"]["score"] == 70
    assert repo.saved["c-low"]["subscores"] is None


def test_score_gold_lineage_differs_when_inputs_differ():
    """Two scorings with a DIFFERENT model + profile_hash → the saves carry DISTINCT lineage
    (the negative twin of the threading test: lineage is per-call, never a baked-in constant)."""
    repo = _FakeScoreRepo(_profile_row(60), _candidates()[:1], None)
    score_gold(run_id="r1", repo=repo, profile_hash="ph-before",
               scorer=Scorer(FakeLlm(_score_json(50)), model_id="model-v1"), max_workers=1)
    first = dict(repo.saved["c-low"])
    repo._candidates = _candidates()[:1]  # re-promote the same candidate for a second pass
    score_gold(run_id="r2", repo=repo, profile_hash="ph-after",
               scorer=Scorer(FakeLlm(_score_json(70)), model_id="model-v2"), max_workers=1)
    second = dict(repo.saved["c-low"])
    assert (first["scoring_model"], first["profile_hash"], first["run_id"]) == (
        "model-v1", "ph-before", "r1")
    assert (second["scoring_model"], second["profile_hash"], second["run_id"]) == (
        "model-v2", "ph-after", "r2")
