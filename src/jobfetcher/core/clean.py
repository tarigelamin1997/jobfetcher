"""Deterministic silver text cleaning — the cheap, pure, versioned half of the silver step
(the heavy half is the `Dissector`). Strips HTML, normalizes unicode, collapses whitespace.

Stdlib only (`html`, `re`, `unicodedata`) — no parser dependency. JSearch `job_description`
is mostly plain text but sometimes carries `<br>`, `&amp;`, and non-breaking spaces; this makes
it consistent before it reaches the LLM and the grounding check.
"""
from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_UNESCAPE_PASSES = 5  # bounds the unescape loop; deeper nesting than this is pathological


def clean(text: str | None) -> str:
    """Strip HTML tags + entities, NFKC-normalize unicode, collapse whitespace, trim.

    `None`/empty in → `""` out (never crashes). Pure and order-stable, so it can be replayed
    over immutable bronze and versioned with `pipeline_version`.
    """
    if not text:
        return ""
    # C6: unescape REPEATEDLY until stable — a double-encoded tag (`&amp;lt;b&amp;gt;`) only
    # becomes a real tag after the second unescape, so a fixed two passes can still miss
    # deeper nesting. Bounded loop (escaping strictly shrinks the string, so it always halts).
    out = text
    for _ in range(_MAX_UNESCAPE_PASSES):
        unescaped = html.unescape(out)
        if unescaped == out:
            break
        out = unescaped
    out = _TAG_RE.sub(" ", out)  # then strip the now-real tags
    out = unicodedata.normalize("NFKC", out)
    out = _WS_RE.sub(" ", out)
    return out.strip()
