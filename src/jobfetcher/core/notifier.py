"""The daily digest renderer (build-plan Step 6): turn the surfaced shortlist + the
below-threshold count into a clean, scannable email — an HTML body **and** a plaintext
fallback — for morning triage in 60 seconds.

Dependency-free on purpose (P1): plain string templates, no jinja. All user/LLM text is HTML-
escaped before interpolation (a JD title or company is untrusted input). **Zero matches is a
first-class case** (VG5 negative): it renders a valid "no strong matches today" email, never a
crash or a blank body.
"""
from __future__ import annotations

from datetime import date
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from .ports import ShortlistItem

# Only these URL schemes may become a clickable link. `apply_url` is untrusted JSearch input;
# `html.escape` stops attribute-breakout but NOT a hostile scheme (`javascript:`/`data:`/
# `vbscript:`), so the scheme is allowlisted before we ever emit an href (or present a "link"
# in the plaintext body).
_SAFE_URL_SCHEMES = frozenset({"http", "https"})


def _safe_apply_url(apply_url: str | None) -> str | None:
    """Return `apply_url` only if it is an `http`/`https` link, else `None` — so a
    `javascript:`/`data:`/`vbscript:` (or otherwise schemed) URL never renders as a link."""
    if not apply_url:
        return None
    try:
        scheme = urlsplit(apply_url).scheme.lower()
    except ValueError:
        return None
    return apply_url if scheme in _SAFE_URL_SCHEMES else None


def _one_line_why(item: "ShortlistItem") -> str:
    """A single scannable 'why' line: the first strength, else the strategic assessment, else
    a neutral fallback. Never empty — the digest row always has a reason."""
    if item.strengths:
        first = str(item.strengths[0]).strip()
        if first:
            return first
    if item.strategic_assessment and item.strategic_assessment.strip():
        return item.strategic_assessment.strip()
    return "Matched your profile above the threshold."


def _display_title(item: "ShortlistItem") -> str:
    """The normalized title if the dissection produced one, else the raw source title."""
    return (item.normalized_title or item.title or "Untitled role").strip()


def render_digest(
    items: "list[ShortlistItem]",
    below_count: int,
    *,
    threshold: int,
    date: "date",
) -> tuple[str, str, str]:
    """Render `(subject, html_body, text_body)` for the daily digest.

    `items` are the surfaced matches (already `score >= threshold`, ordered by score DESC by
    the Repository); `below_count` is how many scored matches fell below the threshold (the
    footer). With zero matches, returns a valid "no strong matches today" email (VG5 negative),
    not a crash or a blank body. Emits BOTH an HTML body and a plaintext fallback."""
    day = date.isoformat()
    n = len(items)

    if n == 0:
        subject = f"JobFetcher — no matches ({day})"
        # below_count is the total scored when zero surfaced (0 above + N below).
        line = (
            f"No strong matches today ({below_count} scored, all below threshold {threshold})."
            if below_count
            else f"No strong matches today (0 scored above threshold {threshold})."
        )
        text_body = f"{line}\n"
        html_body = (
            "<html><body style=\"font-family:Arial,Helvetica,sans-serif;\">"
            f"<h2>JobFetcher digest — {escape(day)}</h2>"
            f"<p>{escape(line)}</p>"
            "</body></html>"
        )
        return subject, html_body, text_body

    subject = f"JobFetcher — {n} match{'es' if n != 1 else ''} ({day})"

    footer = f"+{below_count} below threshold {threshold}" if below_count else None

    # ---- plaintext fallback ----
    text_lines = [f"JobFetcher digest — {day} — {n} match{'es' if n != 1 else ''}", ""]
    for item in items:
        title = _display_title(item)
        company = (item.company or "Unknown company").strip()
        text_lines.append(f"[{item.score}] {title} — {company}")
        text_lines.append(f"    why: {_one_line_why(item)}")
        text_lines.append(f"    apply: {_safe_apply_url(item.apply_url) or '(no link)'}")
        text_lines.append("")
    if footer:
        text_lines.append(footer)
    text_body = "\n".join(text_lines) + "\n"

    # ---- HTML body ----
    rows = []
    for item in items:
        title = escape(_display_title(item))
        company = escape((item.company or "Unknown company").strip())
        why = escape(_one_line_why(item))
        safe_url = _safe_apply_url(item.apply_url)
        if safe_url:
            apply_cell = f'<a href="{escape(safe_url, quote=True)}">Apply</a>'
        else:
            apply_cell = "<em>no link</em>"
        rows.append(
            "<tr>"
            f'<td style="padding:6px 10px;font-weight:bold;">{item.score}</td>'
            f'<td style="padding:6px 10px;">{title}</td>'
            f'<td style="padding:6px 10px;">{company}</td>'
            f'<td style="padding:6px 10px;color:#555;">{why}</td>'
            f'<td style="padding:6px 10px;">{apply_cell}</td>'
            "</tr>"
        )
    table = (
        '<table style="border-collapse:collapse;width:100%;" cellspacing="0">'
        "<thead><tr style=\"text-align:left;border-bottom:1px solid #ccc;\">"
        '<th style="padding:6px 10px;">Score</th>'
        '<th style="padding:6px 10px;">Title</th>'
        '<th style="padding:6px 10px;">Company</th>'
        '<th style="padding:6px 10px;">Why</th>'
        '<th style="padding:6px 10px;">Link</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    footer_html = f'<p style="color:#888;">{escape(footer)}</p>' if footer else ""
    html_body = (
        "<html><body style=\"font-family:Arial,Helvetica,sans-serif;\">"
        f"<h2>JobFetcher digest — {escape(day)}</h2>"
        f"<p>{n} match{'es' if n != 1 else ''} at or above threshold {threshold}.</p>"
        f"{table}{footer_html}"
        "</body></html>"
    )

    return subject, html_body, text_body
