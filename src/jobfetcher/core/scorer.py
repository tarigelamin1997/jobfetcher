"""The `Scorer` (build Step 5): one gold candidate -> a calibrated, explainable `ScoreResult`.

Mirrors the `Dissector` exactly (the reference structured-LLM component): a grounded,
temperature-0 system prompt that returns ONLY JSON, a tolerant `_extract_json`, one
parse/validate retry, and a typed `ScorerError` on failure.

Two things that make this the Scorer, not the Dissector:
  - it reasons over the **silver dissection** (`DissectedPosting`: normalized_title, seniority,
    skills, sector, location) + the candidate `Profile` — it does NOT re-extract from raw JD
    text (the dissection already did that — ADR-0016).
  - it scores via the **7-factor ATS framework** (02-architecture "Scoring") and carries the
    **legitimacy/scam gate** + a **poster-type** label, the explainability that is the value.

`fit_category` is NOT produced here — it is derived in the orchestrator from `score` against
the per-user runtime threshold/floor/band (VG8). The model id (`deepseek-v4-pro` for scoring)
is selected by the injected `LlmClient`'s `LlmConfig.model`; `model_id` here is a provenance
label only, exactly like the Dissector.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from .dissector import DissectionError, _extract_json  # shared tolerant JSON extraction
from .models import ScoreResult

if TYPE_CHECKING:
    from .models import DissectedPosting
    from .ports import LlmClient
    from .profile import Profile

log = logging.getLogger(__name__)

# Tarig's documented 7-factor formula (ADR-0028-to-be — scorer subscores / shadow totals):
# the weights from the PDF scoring framework, written down in code for the FIRST time.
# Keys mirror the `ScoreResult` subscore field names exactly (a test pins the
# correspondence); the LLM's holistic `score` stays the product number (SHADOW mode) —
# this formula only produces the logged/persisted `code_total` for M7 calibration.
FACTOR_WEIGHTS: dict[str, float] = {
    "core_skill_match": 0.30,
    "tool_tech_alignment": 0.20,
    "achievement_relevance": 0.15,
    "seniority_scope": 0.15,
    "ats_keyword_coverage": 0.10,
    "domain_sector_fit": 0.05,
    "realistic_fit": 0.05,
}
# Module-load guard: a mis-edited weight table must fail the import, not skew every total.
assert sum(FACTOR_WEIGHTS.values()) == 1.0, "FACTOR_WEIGHTS must sum to exactly 1.0"

# |LLM holistic - code total| above this logs the shadow line at WARNING (a divergence worth
# eyeballing), below it at INFO. Observability-only — no behavior changes either way.
_SHADOW_DELTA_WARN = 20

SCORING_SYSTEM_PROMPT = """\
You are an expert technical recruiter scoring how well ONE candidate fits ONE job, using an \
ATS-style 7-factor framework. You are given the job's already-extracted structured fields and \
the candidate's profile — reason over those; do NOT invent requirements not present in them.

Return ONLY a single JSON object — no prose, no explanation, no markdown fences:
{"score": <int 0-100>, "core_skill_match": <int 0-100>, "tool_tech_alignment": <int 0-100>, \
"achievement_relevance": <int 0-100>, "seniority_scope": <int 0-100>, \
"ats_keyword_coverage": <int 0-100>, "domain_sector_fit": <int 0-100>, \
"realistic_fit": <int 0-100>, "strengths": [<short phrase>, ...], "gaps": [<short phrase>, ...], \
"strategic_assessment": <2-4 sentence narrative>, "poster_type": "direct employer" | \
"staffing" | "consulting" | "unknown", "legitimacy_verified": true | false}

Score with these 7 factors (weigh them; the score is your holistic judgment, 0-100):
1. Core-skill match — does the candidate have the role's essential skills?
2. Tool/tech alignment — overlap between the required tools/stack and the candidate's.
3. Achievement relevance — do the candidate's projects/experience map to this role's work?
4. Seniority/scope — is the role's level a fit for the candidate's level (not too junior/senior)?
5. ATS-keyword coverage — would the candidate's profile surface for this posting's keywords?
6. Domain/sector fit — does the candidate's background suit the company's sector?
7. Realistic fit — accounting for must-haves vs nice-to-haves, is this a genuine, winnable fit?

Report EACH factor as its own 0-100 subscore under its JSON key above (factor 1 -> \
"core_skill_match", 2 -> "tool_tech_alignment", 3 -> "achievement_relevance", 4 -> \
"seniority_scope", 5 -> "ats_keyword_coverage", 6 -> "domain_sector_fit", 7 -> \
"realistic_fit"); all 7 are required. `score` remains your holistic judgment, not a \
mechanical average of them.

Rules (accuracy depends on these):
1. `score` is 0-100. A strong, well-aligned fit scores high (>=60); a clearly-misaligned role \
(wrong field, wrong seniority by a wide margin, missing every must-have skill) scores LOW \
(well below 50). Do not inflate — discrimination is the whole point.
2. `strengths` and `gaps` must each be concrete and grounded in the given fields; never empty \
for a real posting. `strategic_assessment` is a short narrative on how to play this application.
3. `legitimacy_verified`: false if the posting reads like a scam, bait, or is too vague to be \
a real role; true otherwise. `poster_type` is informational only — it never changes the score.
4. Output valid JSON and nothing else."""


class ScorerError(Exception):
    """The LLM output could not be parsed/validated into a `ScoreResult` (after one retry)."""


def compute_code_total(result: ScoreResult) -> tuple[int | None, str]:
    """The code-side weighted total over the 7 subscores (`FACTOR_WEIGHTS`) — SHADOW mode
    (ADR-0028-to-be): computed, logged, and persisted for M7 calibration; NEVER the product
    number (`result.score`, the LLM's holistic judgment, stays it everywhere).

    Pure and total: all 7 subscores present -> `(round(weighted_sum), "complete")` (Python
    `round`, i.e. half-to-even — pinned by test); ANY missing/None -> `(None,
    "subscores_missing")`. Never raises (fail-open, the ERR-006 philosophy) — out-of-range
    values can't reach here because the Pydantic bounds already rejected them at validation
    (which surfaces as the existing ScorerError/skip path)."""
    values = {name: getattr(result, name, None) for name in FACTOR_WEIGHTS}
    if any(v is None for v in values.values()):
        return None, "subscores_missing"
    return round(sum(FACTOR_WEIGHTS[name] * v for name, v in values.items())), "complete"


def subscores_payload(result: ScoreResult) -> dict[str, int] | None:
    """The persisted `subscores` JSONB shape (migration 0006): the 7 factors + `code_total`
    + `llm_total` (the LLM's holistic score at this write) — what `save_score` stores on
    `score`/`score_event` for M7 calibration. Returns `None` when the LLM omitted ANY
    subscore (the column stays NULL — never a partial dict)."""
    code_total, _status = compute_code_total(result)
    if code_total is None:
        return None
    payload: dict[str, int] = {name: getattr(result, name) for name in FACTOR_WEIGHTS}
    payload["code_total"] = code_total
    payload["llm_total"] = result.score
    return payload


def _profile_summary(profile: "Profile") -> str:
    p = profile.preferences
    skills = ", ".join(
        s.name + (f" ({s.level})" if s.level else "") for s in profile.skills
    )
    certs = ", ".join(c.name for c in profile.certifications) or "(none)"
    projects = "; ".join(
        pr.name + (f" — {pr.summary}" if pr.summary else "") for pr in profile.projects
    ) or "(none)"
    return (
        f"Candidate: {profile.name}"
        + (f" — {profile.headline}" if profile.headline else "")
        + (f"\nSummary: {profile.summary}" if profile.summary else "")
        + f"\nSkills: {skills}"
        + f"\nCertifications: {certs}"
        + f"\nProjects: {projects}"
        + f"\nTarget titles: {', '.join(p.target_titles) or '(any)'}"
        + f"\nTarget sectors: {', '.join(p.target_sectors) or '(any)'}"
        + f"\nTarget locations: {', '.join(p.target_locations) or '(any)'}"
        + f"\nSeniority: {p.seniority_level or '(unspecified)'}"
        + f"\nRemote preference: {p.remote_preference or '(unspecified)'}"
        + (f"\nAvoid: {', '.join(p.avoid_keywords)}" if p.avoid_keywords else "")
    )


def _posting_summary(dissected: "DissectedPosting") -> str:
    skills = (
        ", ".join(f"{s.name} [{s.level.value}]" for s in dissected.skills)
        or "(none extracted)"
    )
    loc = dissected.location or dissected.city or dissected.country or "(unknown)"
    return (
        f"Job title: {dissected.normalized_title} (raw: {dissected.raw_title})"
        f"\nSeniority: {dissected.seniority or '(unknown)'}"
        f"\nSector: {dissected.sector or '(unknown)'}"
        f"\nLocation: {loc}"
        f"\nEmployment type: {dissected.employment_type or '(unknown)'}"
        f"\nRequired skills: {skills}"
    )


class Scorer:
    """Scores one gold candidate against the candidate `Profile`. Pure: the dissection + the
    profile in, a `ScoreResult` out — no storage, no fetching (the orchestrator owns those)."""

    def __init__(self, llm: "LlmClient", *, model_id: str = "deepseek-v4-pro") -> None:
        self.llm = llm
        self.model_id = model_id  # provenance label; the live model is the client's config model

    def score(self, dissected: "DissectedPosting", profile: "Profile") -> ScoreResult:
        user = f"{_profile_summary(profile)}\n\n---\n\n{_posting_summary(dissected)}"
        last_err: Exception | None = None
        for attempt in range(2):  # one parse/validate retry, like the Dissector
            system = SCORING_SYSTEM_PROMPT
            if attempt == 1:
                system += (
                    "\n\nYour previous reply was not valid JSON for the schema above. "
                    "Return ONLY the JSON object."
                )
            raw = self.llm.complete(system=system, user=user)
            try:
                result = ScoreResult.model_validate(_extract_json(raw))
            except (DissectionError, json.JSONDecodeError, ValidationError) as e:
                last_err = e
                continue
            # SHADOW mode (ADR-0028-to-be): the code total is computed + logged after every
            # validated score, but `result.score` (the LLM holistic) stays the product number.
            code_total, status = compute_code_total(result)
            delta = None if code_total is None else code_total - result.score
            shadow_log = (
                log.warning if delta is not None and abs(delta) > _SHADOW_DELTA_WARN else log.info
            )
            shadow_log(
                "score shadow: llm=%d code=%s delta=%s status=%s",
                result.score,
                code_total,
                delta,
                status,
            )
            return result
        raise ScorerError(f"no valid score after one retry: {last_err}")
