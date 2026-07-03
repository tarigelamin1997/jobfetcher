#!/usr/bin/env python3
"""SearchSpec — the validated, fully-explicit user search-input contract.

The user provides the complete search targeting + knobs (nothing assumed); this
Pydantic model validates it and fails loudly on any missing/invalid field. It is
the single source for: the query fan-out (job_titles x countries), the gold-filter
target sets (cities, states, employment, remote), and the per-user geo scope that
flows downstream into dim_location + analytics.

v0 intake: the user fills config/search_config.local.yml (gitignored), validated
here at load. The committed search_config.sample.yml is the complete template.
Lives in src/jobfetcher/core/ (promoted from scripts/ at build Step 1).

Quick check:
  python -m jobfetcher.core.search_spec                         # validate the sample
  python -m jobfetcher.core.search_spec config/search_config.local.yml
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class DatePosted(str, Enum):
    all = "all"
    today = "today"
    three_days = "3days"
    week = "week"
    month = "month"


class RemoteMode(str, Enum):
    off = "off"          # on-site only: drop job_is_remote in the gold filter
    include = "include"  # keep remote postings, flagged
    only = "only"        # remote only (remote_jobs_only=true)


class EmploymentType(str, Enum):
    """JSearch's employment-type vocabulary — the only values the `/search` filter accepts.
    An enum (not a free `str`) so a typo fails loudly at config-load, like DatePosted/RemoteMode."""

    fulltime = "FULLTIME"
    parttime = "PARTTIME"
    contractor = "CONTRACTOR"
    intern = "INTERN"


class Targeting(BaseModel):
    """The four user-named fields. All required; titles/countries must be non-empty."""

    model_config = {"extra": "forbid"}

    job_titles: list[str] = Field(..., description="-> JSearch `query` text (the role)")
    countries: list[str] = Field(..., description="-> JSearch `country` param; ISO-3166-1 alpha-2")
    cities: list[str] = Field(..., description="-> gold filter on job_city ([] = no city filter)")
    states: list[str] = Field(..., description="-> gold filter on job_state ([] = none; usually null for GCC)")

    @field_validator("job_titles", "countries")
    @classmethod
    def _non_empty(cls, v, info):
        if not v:
            raise ValueError(f"{info.field_name} must be a non-empty list (nothing is assumed)")
        return v

    @field_validator("job_titles", "cities", "states")
    @classmethod
    def _no_blanks(cls, v, info):
        if any(not str(item).strip() for item in v):
            raise ValueError(f"{info.field_name} contains a blank entry")
        return v

    @field_validator("countries")
    @classmethod
    def _iso2(cls, v):
        for c in v:
            if not (isinstance(c, str) and len(c) == 2 and c.isalpha()):
                raise ValueError(f"country '{c}' is not an ISO-3166-1 alpha-2 code (e.g. 'sa')")
        return [c.lower() for c in v]


class Budget(BaseModel):
    model_config = {"extra": "forbid"}

    max_pages_per_query: int = Field(..., ge=1, le=20)
    request_budget_per_run: int = Field(..., ge=1)


class SearchSpec(BaseModel):
    """The complete, fully-explicit search input. No field has a default, so the
    user MUST provide every value (the 'nothing taken for granted' contract)."""

    model_config = {"extra": "forbid"}

    source: str
    secret_name: str
    aws_region: str

    targeting: Targeting

    # knobs (explicit — no defaults)
    date_posted: DatePosted
    language: str
    employment_types: list[EmploymentType]  # FULLTIME|PARTTIME|CONTRACTOR|INTERN; [] = no filter
    remote: RemoteMode

    # Shortlist strictness — the three "how strict is my shortlist" knobs (all user-set, all
    # required). `threshold` gates the emailed shortlist today; `hard_floor` + `near_miss_band`
    # also label every job (strong_fit | near_miss | stretch | misaligned via derive_fit_category)
    # — the near-miss/stretch labels feed the upcoming near-miss-watch feature (a later migration).
    threshold: int = Field(..., ge=0, le=100)       # only surface jobs scoring at least this
    hard_floor: int = Field(..., ge=0, le=100)      # below this = misaligned (ignored entirely)
    near_miss_band: int = Field(..., ge=0, le=100)  # width of the "almost" band just below threshold

    budget: Budget

    @field_validator("source", "secret_name", "aws_region", "language")
    @classmethod
    def _non_blank(cls, v, info):
        if not str(v).strip():
            raise ValueError(f"{info.field_name} must be non-empty")
        return v

    @model_validator(mode="after")
    def _floor_below_threshold(self) -> "SearchSpec":
        # The give-up floor can't sit above the shortlist bar — that would make the whole
        # 0..threshold range "misaligned" and leave no room for near_miss/stretch.
        if self.hard_floor > self.threshold:
            raise ValueError(
                f"hard_floor ({self.hard_floor}) must be <= threshold ({self.threshold})"
            )
        return self

    @classmethod
    def from_yaml_text(cls, text: str) -> "SearchSpec":
        """Parse + validate a YAML string (source-agnostic: a local file, an S3 object, …).
        Raises `ValidationError` on any missing/invalid field."""
        data = yaml.safe_load(text) or {}
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SearchSpec":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"search config not found: {p}  "
                "(copy config/search_config.sample.yml -> config/search_config.local.yml and fill it)"
            )
        return cls.from_yaml_text(p.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "config/search_config.sample.yml"
    spec = SearchSpec.from_yaml(path)
    n = len(spec.targeting.job_titles) * len(spec.targeting.countries)
    print(f"OK - {path} is a valid SearchSpec")
    print(f"  {len(spec.targeting.job_titles)} titles x {len(spec.targeting.countries)} countries "
          f"= {n} base queries | date_posted={spec.date_posted.value} language={spec.language} "
          f"remote={spec.remote.value} threshold={spec.threshold}")
    print(f"  gold targets: cities={spec.targeting.cities} states={spec.targeting.states}")
