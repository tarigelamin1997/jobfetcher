"""`DeterministicFilterStrategy` — the v0 default gold filter (no LLM).

P1 (CLAUDE.md): at v0 volume (10-30 jobs/day) a redundant LLM call per posting earns
nothing the Scorer doesn't already do. A cheap rule-based filter cuts the obviously-irrelevant
(wrong title, wrong country, a dealbreaker keyword) and is **coarse + permissive** by design —
the Scorer does the fine judgment, so a borderline posting is kept, not dropped.

Rule (likely-fit iff ALL hold):
  1. **title** — for SOME spec `job_title`, ALL of its meaningful tokens appear in the posting
     title (normalized or raw): "Data Architect" needs `data` AND `architect`. (H-3: the old
     any-single-shared-token rule passed *Azure Architect* / *Alliances Manager* for a
     "Data Architect" spec — measured live; each junk pass costs a pro-model scoring call.
     Semantic adjacency, e.g. "Analytics Architect", is deliberately NOT this filter's job —
     that's the LlmFilterStrategy, selectable via $GOLD_FILTER_STRATEGY.)
  2. **location** — the posting's queried geo (`country`, then `city`) matches the spec's
     `targeting.countries` / `cities`. An empty target set means "no constraint" (matches all).
  3. **no dealbreaker** — none of `profile.preferences.avoid_keywords` appears in the title.

The profile is accepted for symmetry with the port + the avoid-keyword rule; the spec carries
the authoritative query targeting (geo + titles) that the postings were actually pulled against.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.models import DissectedPosting
    from ..core.profile import Profile
    from ..core.search_spec import SearchSpec

# Title tokens too generic to anchor a match on their own (avoid a false "fit" on "engineer").
_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "and", "or", "for", "to", "in", "with", "senior", "junior",
     "lead", "principal", "staff", "mid", "level", "i", "ii", "iii"}
)


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    raw = re.split(r"[^a-z0-9]+", text.lower())
    return {t for t in raw if t and t not in _STOPWORDS}


class DeterministicFilterStrategy:
    """A coarse, permissive rule-based `FilterStrategy` (no LLM)."""

    def filter(
        self, spec: "SearchSpec", profile: "Profile", posting: "DissectedPosting"
    ) -> bool:
        return (
            self._title_matches(spec, posting)
            and self._location_matches(spec, posting)
            and not self._hits_avoid_keyword(profile, posting)
        )

    @staticmethod
    def _title_matches(spec: "SearchSpec", posting: "DissectedPosting") -> bool:
        # H-3 subset rule: SOME target title must have ALL its meaningful tokens present in
        # the posting title. Titles that tokenize to nothing (all stopwords) can't constrain.
        targets = [_tokens(t) for t in spec.targeting.job_titles]
        targets = [t for t in targets if t]
        if not targets:
            return True  # no targeting → don't constrain
        posting_tokens = _tokens(posting.normalized_title) | _tokens(posting.raw_title)
        return any(target <= posting_tokens for target in targets)

    @staticmethod
    def _location_matches(spec: "SearchSpec", posting: "DissectedPosting") -> bool:
        countries = {c.lower() for c in spec.targeting.countries}
        if countries and posting.country and posting.country.lower() not in countries:
            return False
        cities = {c.strip().lower() for c in spec.targeting.cities if c.strip()}
        if cities:
            # an empty cities list = all cities in the country (per the SearchSpec contract)
            posting_city = (posting.city or posting.location or "").lower()
            if not any(city in posting_city for city in cities):
                return False
        return True

    @staticmethod
    def _hits_avoid_keyword(profile: "Profile", posting: "DissectedPosting") -> bool:
        avoid = [k.strip().lower() for k in profile.preferences.avoid_keywords if k.strip()]
        if not avoid:
            return False
        haystack = " ".join(
            filter(None, [posting.raw_title, posting.normalized_title, posting.location])
        ).lower()
        return any(kw in haystack for kw in avoid)
