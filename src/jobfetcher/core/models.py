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
