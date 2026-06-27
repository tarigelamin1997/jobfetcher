"""OPTIONAL live DeepSeek scoring -- the VG2/VG3 validation gate on a REAL JD + the sample
profile, scored with the real `deepseek-v4-pro` model.

Run with:  pytest -m integration
SKIPS unless a DeepSeek key is present (`$DEEPSEEK_API_KEY` or Secrets Manager) and a probe
fixture exists. Costs ~2-3 scoring calls.

VG2 (behavioral): a real JD+profile -> a valid `ScoreResult` with non-empty strengths/gaps/
assessment. VG3 (determinism, best-effort at v0): the client is configured with temperature 0
(asserted), but `deepseek-v4-pro` is non-deterministic even at temp 0 (MoE routing / FP), so the
two scores are only checked against a generous best-effort band, not exact stability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jobfetcher.adapters.llm_openai import OpenAICompatLlmClient
from jobfetcher.config import LlmConfig
from jobfetcher.core.dissector import Dissector
from jobfetcher.core.ports import LlmAuthError
from jobfetcher.core.profile import Profile
from jobfetcher.core.scorer import Scorer
from tests.helpers import load_probe

pytestmark = pytest.mark.integration

_SCORING_MODEL = "deepseek-v4-pro"


def _sample_profile() -> Profile:
    root = Path(__file__).resolve().parents[1]
    sample = root / "config" / "profile.sample.yml"
    if not sample.exists():
        pytest.skip(f"sample profile not present: {sample}")
    return Profile.from_yaml(sample)


def _scoring_client() -> OpenAICompatLlmClient:
    # the scoring model is config (LlmConfig.model), not hardcoded in the Scorer.
    return OpenAICompatLlmClient(LlmConfig(model=_SCORING_MODEL))


def test_live_score_real_jd(capsys):
    jd_text, meta = load_probe("sample_sa.json")
    profile = _sample_profile()
    try:
        dissected = Dissector(OpenAICompatLlmClient()).dissect(jd_text, meta)
        result = Scorer(_scoring_client(), model_id=_SCORING_MODEL).score(dissected, profile)
    except LlmAuthError:
        pytest.skip("no resolvable DeepSeek key (env or Secrets Manager) -- live scoring skipped")

    # VG2: a valid contract with non-empty explainability.
    assert 0 <= result.score <= 100
    assert result.strengths and result.gaps and result.strategic_assessment.strip()
    assert result.poster_type
    with capsys.disabled():
        print(
            f"\n[live score] score={result.score} legit={result.legitimacy_verified} "
            f"poster={result.poster_type} strengths={result.strengths[:2]} gaps={result.gaps[:2]}"
        )


def test_live_score_is_deterministic(capsys):
    """VG3 is best-effort at v0 -- temperature 0 is sent (asserted), but deepseek-v4-pro is
    non-deterministic even at temp 0 (MoE routing / FP); a borderline job can occasionally drift
    across the threshold. Precise stability + calibration is deferred to M7 (the score_override
    accuracy loop).
    """
    jd_text, meta = load_probe("sample_sa.json")
    profile = _sample_profile()
    client = _scoring_client()
    # The real invariant we CAN guarantee: the scoring client sends temperature 0.
    assert client.config.temperature == 0.0
    try:
        dissected = Dissector(OpenAICompatLlmClient()).dissect(jd_text, meta)
        scorer = Scorer(client, model_id=_SCORING_MODEL)
        a = scorer.score(dissected, profile).score
        b = scorer.score(dissected, profile).score
    except LlmAuthError:
        pytest.skip("no resolvable DeepSeek key (env or Secrets Manager) -- live scoring skipped")
    with capsys.disabled():
        print(f"\n[live determinism] score_a={a} score_b={b} delta={abs(a - b)}")
    # VG3 best-effort sanity bound: a generous band that still catches a temp-!=-0 bug or a
    # broken/varying prompt, while tolerating model drift (see docstring). Exact stability deferred.
    assert abs(a - b) <= 20
