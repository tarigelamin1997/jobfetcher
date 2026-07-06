"""The daily digest renderer (build-plan Step 6; email UX v0.6.0): turn the surfaced shortlist
+ the below-threshold count into a **scannable, card-per-job** email — an HTML body **and** a
plaintext fallback — for morning triage in 60 seconds. Each job is one card with a prominent
**Apply** button (the old dense 5-column table buried the link in the last column).

Dependency-free on purpose (P1): plain string templates, no jinja. Email clients strip
`<head>`/`<style>` and mangle flex/grid, so this uses **table-based layout + inline styles
only**, no external CSS/images/JS. All user/LLM text is HTML-escaped before interpolation (a JD
title/company/reason is untrusted input). **Zero matches is a first-class case** (VG5 negative):
it renders a valid "no strong matches today" email, never a crash or a blank body.
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
# `vbscript:`), so the scheme is allowlisted before we ever emit an href (or a "link" in text).
_SAFE_URL_SCHEMES = frozenset({"http", "https"})

# Score-badge background per fit category (the surfaced set is strong_fit, but colour by category
# for correctness). White text on all of these.
_BADGE_COLORS = {
    "strong_fit": "#137333",  # green
    "near_miss": "#b06000",   # amber
    "stretch": "#5f6368",     # grey
    "misaligned": "#5f6368",
}
_BADGE_DEFAULT = "#5f6368"
_APPLY_BG = "#1a73e8"  # the Apply button — a solid, obvious call to action


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
    """A single scannable 'why' line: the first strength, else the strategic assessment, else a
    neutral fallback. Never empty — the card always has a reason."""
    if item.strengths:
        first = str(item.strengths[0]).strip()
        if first:
            return first
    if item.strategic_assessment and item.strategic_assessment.strip():
        return item.strategic_assessment.strip()
    return "Matched your profile above the threshold."


def _first_gap(item: "ShortlistItem") -> str | None:
    """The first gap phrase for an honest-triage line, or `None` if none was recorded."""
    for g in item.gaps:
        s = str(g).strip()
        if s:
            return s
    return None


def _display_title(item: "ShortlistItem") -> str:
    """The normalized title if the dissection produced one, else the raw source title."""
    return (item.normalized_title or item.title or "Untitled role").strip()


def _location(item: "ShortlistItem") -> str:
    """`City, COUNTRY` / `City` / `COUNTRY` / `""` — the country is a 2-letter code (upper-cased)."""
    city = (item.city or "").strip()
    country = (item.country or "").strip().upper()
    if city and country:
        return f"{city}, {country}"
    return city or country


def _badge_color(fit_category: str | None) -> str:
    return _BADGE_COLORS.get((fit_category or "").strip(), _BADGE_DEFAULT)


def render_digest(
    items: "list[ShortlistItem]",
    below_count: int,
    *,
    threshold: int,
    date: "date",
) -> tuple[str, str, str]:
    """Render `(subject, html_body, text_body)` for the daily digest.

    `items` are the surfaced matches (already `score >= threshold`, ordered by score DESC by the
    Repository); `below_count` is how many scored matches fell below the threshold (the footer).
    With zero matches, returns a valid "no strong matches today" email (VG5), not a crash/blank.
    Emits BOTH an HTML body (card-per-job with a prominent Apply button) and a plaintext fallback."""
    day = date.isoformat()
    n = len(items)

    if n == 0:
        subject = f"JobFetcher — no matches ({day})"
        line = (
            f"No strong matches today — {below_count} scored, all below your threshold of {threshold}."
            if below_count
            else f"No strong matches today (nothing scored above your threshold of {threshold})."
        )
        text_body = f"{line}\n"
        html_body = _html_shell(day, f'<p style="color:#3c4043;">{escape(line)}</p>')
        return subject, html_body, text_body

    subject = f"JobFetcher — {n} match{'es' if n != 1 else ''} ({day})"
    footer = f"+{below_count} more scored below your threshold of {threshold}" if below_count else None

    # ---- plaintext fallback (the apply URL is prominent, on its own line) ----
    text_lines = [f"JobFetcher digest — {day} — {n} match{'es' if n != 1 else ''}", ""]
    for item in items:
        loc = _location(item)
        head = f"[{item.score}] {_display_title(item)} — {(item.company or 'Unknown company').strip()}"
        text_lines.append(f"{head} · {loc}" if loc else head)
        text_lines.append(f"    why: {_one_line_why(item)}")
        gap = _first_gap(item)
        if gap:
            text_lines.append(f"    gap: {gap}")
        text_lines.append(f"    apply: {_safe_apply_url(item.apply_url) or '(no link)'}")
        text_lines.append("")
    if footer:
        text_lines.append(footer)
    text_body = "\n".join(text_lines) + "\n"

    # ---- HTML body (one card per job) ----
    cards = "".join(_card_html(item) for item in items)
    summary = (
        f"<p style=\"color:#3c4043;margin:0 0 16px;\">"
        f"<strong>{n}</strong> role{'s' if n != 1 else ''} at or above your threshold of {threshold}"
        f"{f' · +{below_count} below' if below_count else ''}.</p>"
    )
    footer_html = (
        f'<p style="color:#80868b;font-size:13px;margin:8px 0 0;">{escape(footer)}</p>'
        if footer else ""
    )
    html_body = _html_shell(day, summary + cards + footer_html)
    return subject, html_body, text_body


def _card_html(item: "ShortlistItem") -> str:
    """One job = one bordered card: score badge · title · fit · Company·Location · why · gap · Apply."""
    title = escape(_display_title(item))
    company = escape((item.company or "Unknown company").strip())
    loc = escape(_location(item))
    why = escape(_one_line_why(item))
    fit = escape((item.fit_category or "").replace("_", " ").strip())
    badge = _badge_color(item.fit_category)
    company_line = f"{company} &middot; {loc}" if loc else company

    gap = _first_gap(item)
    gap_html = (
        f'<div style="color:#a8641b;font-size:13px;margin:4px 0 0;">&#9888; {escape(gap)}</div>'
        if gap else ""
    )

    safe_url = _safe_apply_url(item.apply_url)
    if safe_url:
        apply_html = (
            f'<a href="{escape(safe_url, quote=True)}" '
            f'style="display:inline-block;background:{_APPLY_BG};color:#ffffff;padding:10px 20px;'
            'border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px;margin-top:12px;">'
            "Apply &rarr;</a>"
        )
    else:
        apply_html = (
            '<div style="color:#80868b;font-size:13px;margin-top:12px;font-style:italic;">'
            "No apply link available</div>"
        )

    fit_label = (
        f'<span style="color:#5f6368;font-size:12px;margin-left:8px;">{fit}</span>' if fit else ""
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border:1px solid #e2e4e8;border-radius:8px;background:#ffffff;margin:0 0 12px;">'
        '<tr><td style="padding:14px 16px;">'
        f'<span style="display:inline-block;background:{badge};color:#ffffff;font-weight:bold;'
        f'font-size:13px;padding:2px 9px;border-radius:12px;">{item.score}</span>'
        f'<span style="font-weight:bold;font-size:16px;color:#202124;margin-left:8px;">{title}</span>'
        f"{fit_label}"
        f'<div style="color:#5f6368;font-size:14px;margin:6px 0 0;">{company_line}</div>'
        f'<div style="color:#3c4043;font-size:14px;margin:8px 0 0;">&#10003; {why}</div>'
        f"{gap_html}"
        f"<div>{apply_html}</div>"
        "</td></tr></table>"
    )


def _html_shell(day: str, inner: str) -> str:
    """The email frame: a light-grey page, a centered 640px column, the header, then `inner`."""
    return (
        '<html><body style="margin:0;padding:0;background:#f4f5f7;'
        'font-family:Arial,Helvetica,sans-serif;">'
        '<div style="max-width:640px;margin:0 auto;padding:20px 16px;">'
        f'<h2 style="color:#202124;margin:0 0 12px;">JobFetcher digest &middot; {escape(day)}</h2>'
        f"{inner}"
        '<p style="color:#9aa0a6;font-size:12px;margin:20px 0 0;">'
        "You're receiving this because JobFetcher scored today's roles against your profile.</p>"
        "</div></body></html>"
    )
