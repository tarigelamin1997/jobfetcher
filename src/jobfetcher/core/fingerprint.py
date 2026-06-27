"""The deterministic silver dedup key (the cheap half of cluster-and-surface, ADR-0005).

`fingerprint(title, company, location)` → a stable short hash of the normalized triple. Two
postings of the same role at the same company+location collide on it regardless of which
platform surfaced them — the exact-id `bronze_id` dedup catches re-fetches of the *same*
listing; this catches the *same job posted on multiple boards*. Pure + versioned: same inputs
→ same key, forever.
"""
from __future__ import annotations

import hashlib

_FP_LEN = 16  # 64 bits of sha256 hex — ample for personal-scale collision safety


def _norm(value: str | None) -> str:
    """Lowercase + collapse internal whitespace + trim (empty for None)."""
    return " ".join((value or "").split()).lower()


def fingerprint(title: str | None, company: str | None, location: str | None) -> str:
    """Stable `title|company|location` hash (sha256 hexdigest, truncated to 16 chars)."""
    key = "|".join((_norm(title), _norm(company), _norm(location)))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_FP_LEN]
