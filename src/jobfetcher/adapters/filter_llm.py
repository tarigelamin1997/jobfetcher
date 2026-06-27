"""`LlmFilterStrategy` — the LLM-based gold filter (ADR-0016: gold = an LLM `FilterStrategy`).

Built + selectable, but **not** the v0 default — at v0 volume the deterministic filter is
enough (P1) and the strong-model judgment belongs to the Scorer. This mirrors the `Dissector`
pattern exactly: a cheap model (`deepseek-v4-flash`), temperature 0 (from `LlmConfig`),
structured JSON, one parse/validate retry.

It judges a **coarse** likely-fit of the already-dissected silver fields (+ the spec targeting)
against the candidate profile — NOT a fine score. Output: `{"likely_fit": bool, "reason": str}`.

**Fail-open (FAILURE-MODE, build-plan Step 4b):** any error — bad JSON after the retry, or an
LLM transport failure — raises `FilterError`, which the gold step catches and treats as
INCLUDE. A real fit must never be dropped before scoring; over-inclusion is cheap (the Scorer
filters), a dropped fit is invisible.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..core.dissector import DissectionError, _extract_json  # shared tolerant JSON extraction
from ..core.ports import FilterError, LlmError

if TYPE_CHECKING:
    from ..core.models import DissectedPosting
    from ..core.ports import LlmClient
    from ..core.profile import Profile
    from ..core.search_spec import SearchSpec

FILTER_SYSTEM_PROMPT = """\
You are a coarse pre-screen for a job-matching pipeline. Decide whether a job posting is a \
PLAUSIBLE fit for a candidate — a cheap gate before an expensive detailed scoring step.

Return ONLY a single JSON object — no prose, no markdown fences:
{"likely_fit": true | false, "reason": <one short phrase>}

Be PERMISSIVE. Your job is only to drop the OBVIOUSLY irrelevant (clearly the wrong field, \
wrong seniority by a wide margin, or a location/language the candidate cannot take). When in \
doubt, return true — a detailed scorer runs next and does the fine judgment. Do NOT reject a \
posting merely for missing some skills or being a slight stretch."""


def _candidate_summary(profile: "Profile") -> str:
    p = profile.preferences
    skills = ", ".join(s.name for s in profile.skills)
    return (
        f"Candidate: {profile.name}"
        + (f" — {profile.headline}" if profile.headline else "")
        + f"\nTarget titles: {', '.join(p.target_titles) or '(any)'}"
        + f"\nTarget locations: {', '.join(p.target_locations) or '(any)'}"
        + f"\nRemote preference: {p.remote_preference or '(unspecified)'}"
        + f"\nSeniority: {p.seniority_level or '(unspecified)'}"
        + f"\nSkills: {skills}"
        + (f"\nAvoid: {', '.join(p.avoid_keywords)}" if p.avoid_keywords else "")
    )


def _posting_summary(spec: "SearchSpec", posting: "DissectedPosting") -> str:
    skills = ", ".join(s.name for s in posting.skills) or "(none extracted)"
    loc = posting.location or posting.city or posting.country or "(unknown)"
    return (
        f"Posting title: {posting.normalized_title} (raw: {posting.raw_title})"
        f"\nSeniority: {posting.seniority or '(unknown)'}"
        f"\nLocation: {loc} | country queried: {', '.join(spec.targeting.countries)}"
        f"\nSector: {posting.sector or '(unknown)'}"
        f"\nSkills required: {skills}"
    )


class LlmFilterStrategy:
    """An LLM-backed `FilterStrategy`. Construct with an `LlmClient` (a cheap model)."""

    def __init__(self, llm: "LlmClient") -> None:
        self.llm = llm

    def filter(
        self, spec: "SearchSpec", profile: "Profile", posting: "DissectedPosting"
    ) -> bool:
        user = f"{_candidate_summary(profile)}\n\n---\n\n{_posting_summary(spec, posting)}"
        last_err: Exception | None = None
        for attempt in range(2):  # one parse/validate retry, like the Dissector
            system = FILTER_SYSTEM_PROMPT
            if attempt == 1:
                system += (
                    "\n\nYour previous reply was not valid JSON for the schema above. "
                    "Return ONLY the JSON object."
                )
            try:
                raw = self.llm.complete(system=system, user=user)
            except LlmError as e:
                # transport failure → fail open (caller includes)
                raise FilterError(f"LLM filter transport failed: {e}") from e
            try:
                data = _extract_json(raw)
                fit = data["likely_fit"]
                if not isinstance(fit, bool):
                    raise ValueError(f"likely_fit must be a bool, got {fit!r}")
                return fit
            except (DissectionError, KeyError, TypeError, ValueError, json.JSONDecodeError) as e:
                last_err = e
        raise FilterError(f"no valid filter verdict after one retry: {last_err}")
