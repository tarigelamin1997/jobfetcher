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


def clean(text: str | None) -> str:
    """Strip HTML tags + entities, NFKC-normalize unicode, collapse whitespace, trim.

    `None`/empty in → `""` out (never crashes). Pure and order-stable, so it can be replayed
    over immutable bronze and versioned with `pipeline_version`.
    """
    if not text:
        return ""
    # Entities first (so a literal "&lt;b&gt;" becomes a tag we then strip), then tags.
    out = html.unescape(text)
    out = _TAG_RE.sub(" ", out)
    out = html.unescape(out)  # second pass: tags may have hidden entities (e.g. &amp;amp;)
    out = unicodedata.normalize("NFKC", out)
    out = _WS_RE.sub(" ", out)
    return out.strip()
