"""The candidate `Profile` contract — the scoring + gold-filter source of truth.

This is the JSONB payload stored in `profile.profile` (skills, certs, projects, prefs).
The numeric knobs `threshold` / `hard_floor` / `near_miss_band` are **profile-row columns**
(ADR-0016 / build Step 5), NOT part of this contract — they live on the `profile` table so
changing the shortlist cutoff is editing one DB value, never re-validating the JSONB blob.

Two loaders, both loud on invalid input (`from_yaml` for the committed sample +
`config/profile.local.yml`; `from_jsonb` for the `profile.profile` column read back via the
Repository). A `[TO BE FILLED]` profile is a blocker, not a draft — every required field
must be present or load fails.

The real profile (with PII) is gitignored → `config/profile.local.yml`; only the sanitized
`config/profile.sample.yml` is committed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ProfileSkill(BaseModel):
    """One candidate skill: a name plus optional self-rated depth + years of use."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1)
    level: str | None = Field(default=None, description="e.g. expert | proficient | learning")
    years: float | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("skill name must be non-empty")
        return v


class Certification(BaseModel):
    """A named certification (issuer optional)."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1)
    issuer: str | None = None


class Project(BaseModel):
    """A portfolio project: a name + a short what/why summary."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1)
    summary: str | None = None


class Preferences(BaseModel):
    """The candidate's targeting + dealbreakers. `target_*` are the positive signals the
    filter/scorer match against; `avoid_keywords` are negative (a hit excludes from gold)."""

    model_config = {"extra": "forbid"}

    target_titles: list[str] = Field(default_factory=list)
    target_sectors: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    remote_preference: str | None = Field(default=None, description="off | include | only")
    seniority_level: str | None = None
    avoid_keywords: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    """The candidate profile — the JSONB `profile.profile` payload. Required: a name, at
    least one skill, and preferences (the filter/scorer have nothing to match on otherwise);
    certs/projects are optional context."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1)
    headline: str | None = None
    summary: str | None = None
    skills: list[ProfileSkill] = Field(..., min_length=1)
    certifications: list[Certification] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    preferences: Preferences

    @field_validator("name")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("profile name must be non-empty")
        return v

    @classmethod
    def from_jsonb(cls, data: Any) -> "Profile":
        """Build from the `profile.profile` JSONB column (a dict). Loud on anything invalid
        — a malformed stored profile must fail, never silently score against an empty one."""
        if not isinstance(data, dict):
            raise ValueError(f"profile JSONB must be an object, got {type(data).__name__}")
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Profile":
        """Load + validate a YAML profile (the committed sample or `profile.local.yml`).
        Raises `FileNotFoundError` if absent, `ValidationError` on any missing/invalid field."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"profile not found: {p}  "
                "(copy config/profile.sample.yml -> config/profile.local.yml and fill it)"
            )
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "config/profile.sample.yml"
    prof = Profile.from_yaml(path)
    print(f"OK - {path} is a valid Profile")
    print(f"  {prof.name} | {len(prof.skills)} skills | {len(prof.certifications)} certs "
          f"| {len(prof.projects)} projects")
    print(f"  target_titles={prof.preferences.target_titles}")
    print(f"  target_locations={prof.preferences.target_locations}")
