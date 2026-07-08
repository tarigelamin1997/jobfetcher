"""Data contracts for the silver dissection (ADR-0016).

The split is the first accuracy lever (plan §30):
  - `PostingMetadata` — deterministic fields taken straight from the source payload; the
    LLM never re-guesses these (title, location, language, employment type, seniority).
  - `LlmExtraction` — the free-text fields the LLM produces (skills + levels, sector,
    normalized title).
  - `DissectedPosting` — the merge. This is what the silver `posting` / dimensional schema
    (build Step 2) will hold, so authoring it here de-risks that schema.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# The application-outcome vocabulary (migration 0005) — the ONE definition every layer
# validates against: the `application_event` CHECK constraint (db/tables.py builds its SQL
# from this tuple), the repository's loud pre-INSERT validation, and the `scripts/track.py`
# argparse subcommands. A tuple (not a set) so the CHECK's SQL and the CLI's subcommand
# order are deterministic. Adding a status = append here + an additive migration.
APPLICATION_STATUSES: tuple[str, ...] = (
    "applied",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
)


class RequirementLevel(str, Enum):
    must = "must"        # explicitly required / essential / minimum
    nice = "nice"        # preferred / plus / bonus / advantageous
    implied = "implied"  # not stated, but directly entailed by a stated responsibility


class Skill(BaseModel):
    """One extracted skill, grounded in a JD phrase. `extra=ignore` tolerates LLM chatter;
    the required fields + the grounding check are what enforce quality."""

    model_config = {"extra": "ignore"}

    name: str = Field(..., min_length=1)
    level: RequirementLevel
    evidence: str = Field(..., min_length=1, description="exact JD phrase supporting this skill")


class PostingMetadata(BaseModel):
    """Deterministic fields from the source payload — never re-guessed by the LLM."""

    model_config = {"extra": "forbid"}

    raw_title: str = Field(..., min_length=1)
    language: str = Field(..., min_length=1)
    location: str | None = None
    city: str | None = None
    country: str | None = None
    employment_type: str | None = None
    seniority: str | None = None  # parsed deterministically from the title, else None


class LlmExtraction(BaseModel):
    """Exactly what the LLM returns — the free-text part only."""

    model_config = {"extra": "ignore"}

    skills: list[Skill] = Field(default_factory=list)
    sector: str | None = None
    normalized_title: str = Field(..., min_length=1)


class DissectedPosting(BaseModel):
    """The merged result: deterministic metadata + the grounded LLM extraction."""

    model_config = {"extra": "forbid"}

    # from metadata (deterministic)
    raw_title: str
    language: str
    location: str | None = None
    city: str | None = None
    country: str | None = None
    employment_type: str | None = None
    seniority: str | None = None
    # from the LLM (free-text, grounded)
    normalized_title: str
    sector: str | None = None
    skills: list[Skill] = Field(default_factory=list)
    # provenance
    model: str = Field(..., description="the LLM model id that produced the extraction")
    dropped_skill_count: int = Field(default=0, ge=0, description="skills cut by grounding")


class ScoreResult(BaseModel):
    """The Scorer's structured output (build Step 5) — exactly what the LLM returns when it
    scores one gold candidate against the candidate `Profile` via the 7-factor ATS framework
    (02-architecture "Scoring"). Mirrors `DissectedPosting`: `extra=ignore` tolerates LLM
    chatter; the required fields + bounds are what enforce quality.

    `fit_category` is **NOT** here — it is derived in code from `score` against the per-user
    runtime threshold/floor/band (VG8), never asked of the LLM (band routing must not vary
    with the model). The LLM judges only what it can see in the JD: the legitimacy/scam gate
    (`legitimacy_verified`) + the poster-type label (`poster_type`).

    The 7 per-factor subscores (one per factor of the framework, 0-100 each) are OPTIONAL —
    an older model reply without them still validates (`None`), so the pipeline never breaks
    on their absence; a present-but-out-of-range subscore is rejected like any bad field
    (the existing ScorerError/skip path). `score` remains the product number (SHADOW mode,
    ADR-0028-to-be): the code-side weighted total is computed FROM these in
    `core/scorer.py`, logged and persisted for calibration — never substituted."""

    model_config = {"extra": "ignore"}

    score: int = Field(..., ge=0, le=100, description="overall ATS fit, 0-100")
    # Per-factor subscores (optional — see the class docstring). Field names mirror
    # `FACTOR_WEIGHTS` in core/scorer.py exactly (a test pins the correspondence).
    core_skill_match: int | None = Field(None, ge=0, le=100)
    tool_tech_alignment: int | None = Field(None, ge=0, le=100)
    achievement_relevance: int | None = Field(None, ge=0, le=100)
    seniority_scope: int | None = Field(None, ge=0, le=100)
    ats_keyword_coverage: int | None = Field(None, ge=0, le=100)
    domain_sector_fit: int | None = Field(None, ge=0, le=100)
    realistic_fit: int | None = Field(None, ge=0, le=100)
    strengths: list[str] = Field(default_factory=list, description="why this is a fit")
    gaps: list[str] = Field(default_factory=list, description="what's missing / a stretch")
    strategic_assessment: str = Field(
        ..., min_length=1, description="a short narrative: how to play this application"
    )
    poster_type: str = Field(
        ..., min_length=1, description="direct employer | staffing | consulting | unknown"
    )
    legitimacy_verified: bool = Field(
        ..., description="True if the posting reads as a legitimate role, not a scam"
    )
