"""The daily digest renderer (build-plan Step 6; email UX v0.6.0; digest truthfulness v0.7):
turn the surfaced shortlist + the below-threshold count into a **scannable, card-per-job**
email — an HTML body **and** a plaintext fallback — for morning triage in 60 seconds.

The digest is TRUTHFUL about what changed: a **"New since last digest"** section leads with
full cards — an item is new only when its judgment is FRESH (`scored_at > since`, the last
digest send time) and that fresh judgment is news (a first scoring, or a graduation —
`previous_score < threshold <= score` — badged green **↑ old→new**). Everything else above
threshold lands in a compact **"still open"** section (count + the top 5 one-liners), so a
repeat job never masquerades as news. Same-role repeats are **collapsed render-time by
`fingerprint`** — one card per group, footnoted `seen n× — scores lo–hi`; a group straddling
the split renders ONCE (the whole group goes NEW iff any member is new). The split + grouping
are PURE functions (`split_new_and_still_open` / `collapse_duplicates` — no I/O,
unit-testable); `render_digest` consumes their output.

Dependency-free on purpose (P1): plain string templates, no jinja. Email clients strip
`<head>`/`<style>` and mangle flex/grid, so this uses **table-based layout + inline styles
only**, no external CSS/images/JS. All user/LLM text is HTML-escaped before interpolation (a JD
title/company/reason is untrusted input). **Zero matches is a first-class case** (VG5 negative):
it renders a valid "no strong matches today" email, never a crash or a blank body — and a
zero-NEW day says so honestly ("no new matches since {date}") while still sending.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from datetime import datetime

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
_GRAD_GREEN = "#137333"  # the graduation badge — green text, email-client-safe (no images)

# The still-open section shows at most this many compact one-liners; the rest is a count
# pointing at the export (ADR-0024) — the email stays scannable, the data stays reachable.
_STILL_OPEN_TOP_N = 5


@dataclass(frozen=True)
class DigestCard:
    """One rendered card: a fingerprint-group of `ShortlistItem`s collapsed to its best member
    (render-time dup collapse). `item` is the representative (highest score in the group);
    `seen_count`/`score_lo`/`score_hi` describe the group; `member_posting_ids` preserves the
    collapsed members' identity for the plaintext footnote."""

    item: "ShortlistItem"
    seen_count: int
    score_lo: int
    score_hi: int
    member_posting_ids: tuple[str, ...]


def split_new_and_still_open(
    items: "list[ShortlistItem]", *, since: "datetime | None", threshold: int
) -> "tuple[list[ShortlistItem], list[ShortlistItem]]":
    """Split the surfaced shortlist into `(new, still_open)` — the digest-truthfulness rule.

    An item is NEW iff `since is None` (the first-ever digest — everything is new) OR its
    judgment is FRESH (`scored_at > since` — written after the last digest went out) AND that
    fresh judgment is actually news: `previous_score is None` (a first scoring) or a
    graduation (`previous_score < threshold <= score` — it just crossed the bar). Everything
    else above threshold is STILL OPEN. Daily runs score a posting exactly ONCE, so a repeat's
    `scored_at` predates the last digest and it lands still-open — `previous_score` alone
    cannot carry the split (it stays NULL forever in daily operation). A fresh NON-graduated
    re-score (reassess, `previous_score >= threshold`) is still-open too — being re-judged is
    not news; and a graduation that happened BEFORE the last digest is never re-announced.

    A NULL `scored_at` (pathological — `save_score` always stamps it) is treated as NEW: an
    unknown judgment time must never silently demote a match (the unknown-age-included
    philosophy). Pure (no I/O); input order (score DESC from the Repository) is preserved
    within both halves."""
    if since is None:
        return list(items), []
    new: "list[ShortlistItem]" = []
    still_open: "list[ShortlistItem]" = []
    for item in items:
        if item.scored_at is None:
            new.append(item)  # unknown judgment time — defensively NEW, never hidden
        elif item.scored_at > since and (
            item.previous_score is None
            or item.previous_score < threshold <= item.score
        ):
            new.append(item)
        else:
            still_open.append(item)
    return new, still_open


def collapse_duplicates(items: "list[ShortlistItem]") -> list[DigestCard]:
    """Render-time dup collapse: group items by `fingerprint` (a None/empty fingerprint is its
    OWN group — unknowns are never merged with each other), one `DigestCard` per group with the
    highest-scoring member as the representative + the group's seen-count and score range.

    Input order (score DESC) is preserved: a group sits where its first (= highest-scoring)
    member sat, so cards stay score-DESC. Pure (no I/O); `render_digest` groups the WHOLE
    surfaced set once and assigns each group to ONE section (NEW iff any member classifies
    new), so a group straddling the split never renders twice."""
    groups: dict[object, list["ShortlistItem"]] = {}
    for idx, item in enumerate(items):
        fp = (item.fingerprint or "").strip()
        key: object = ("fp", fp) if fp else ("solo", idx)
        groups.setdefault(key, []).append(item)
    cards: list[DigestCard] = []
    for members in groups.values():  # dicts preserve insertion order (first occurrence)
        rep = max(members, key=lambda m: m.score)
        scores = [m.score for m in members]
        cards.append(
            DigestCard(
                item=rep,
                seen_count=len(members),
                score_lo=min(scores),
                score_hi=max(scores),
                member_posting_ids=tuple(m.posting_id for m in members),
            )
        )
    return cards


def _is_graduation(item: "ShortlistItem", *, threshold: int) -> bool:
    """True when this item just crossed the bar upward: `previous_score < threshold <= score`
    (the ADR-0023 reassess graduation). `previous_score is None` = a first scoring — new, but
    NOT a graduation (there is nothing to compare against), so it never gets a badge."""
    return item.previous_score is not None and item.previous_score < threshold <= item.score


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
    since: "datetime | None" = None,
    full_list_url: str | None = None,
) -> tuple[str, str, str]:
    """Render `(subject, html_body, text_body)` for the daily digest.

    `items` are the surfaced matches (already `score >= threshold`, ordered by score DESC by the
    Repository); `below_count` is how many scored matches fell below the threshold (the footer).
    `since` is when the LAST digest went out (`repo.get_last_digest_sent_at`) — `None` = the
    first-ever digest.

    `full_list_url` (B-1) is a presigned https link to the full-list report page (every scored
    job). When present it LINKIFIES the two otherwise-dead lines — the below-threshold footer
    and the still-open "…and N more" overflow — so the compressed tail is reachable from the
    email. It is scheme-allowlisted via `_safe_apply_url`; a hostile scheme (or `None`) degrades
    gracefully to today's plain text (no link), never emitting an unsafe href.

    Digest truthfulness: the items are split into **"New since last digest"** (full cards,
    first; a graduation gets the green `↑ old→new` badge) and **"still open"** (a count + the
    top-5 compact one-liners) via the pure `split_new_and_still_open` (`scored_at` vs `since`
    + `previous_score` for the graduation call), and same-fingerprint repeats are collapsed to
    one card via `collapse_duplicates` (`seen n× — scores lo–hi`) — grouped over the WHOLE
    surfaced set, then each group assigned to ONE section (NEW iff any member is new), so a
    straddling group never renders twice. A zero-NEW day says so honestly ("no new matches
    since {date}") but STILL SENDS — the still-open section renders regardless (VG5 spirit:
    the email is never skipped or blank). Age-dropped jobs (the `digest_max_age_days` cutoff,
    applied by the Repository) simply never reach this renderer. Emits BOTH an HTML body and
    a plaintext fallback."""
    day = date.isoformat()
    # Scheme-allowlist the full-list link ONCE (untrusted-URL discipline, mirrors apply_url): a
    # non-http(s) or None value → the two dead lines keep today's plain text (graceful).
    safe_full_url = _safe_apply_url(full_list_url)
    new_items, _ = split_new_and_still_open(items, since=since, threshold=threshold)
    new_ids = {i.posting_id for i in new_items}
    new_cards: list[DigestCard] = []
    open_cards: list[DigestCard] = []
    for card in collapse_duplicates(items):
        # F4: the WHOLE fingerprint group lands in ONE section — NEW iff ANY member is new —
        # so a group straddling the split renders once (never a card AND a one-liner).
        if any(pid in new_ids for pid in card.member_posting_ids):
            new_cards.append(card)
        else:
            open_cards.append(card)
    n = len(new_cards)
    since_day = since.date().isoformat() if since is not None else None

    if n == 0 and not open_cards:
        # Nothing to show at all — the VG5 zero-path (first-ever wording) or an honest
        # "no new matches since {date}" when a prior digest exists. Always a valid email.
        if since_day is None:
            subject = f"JobFetcher — no matches ({day})"
            line = (
                f"No strong matches today — {below_count} scored, "
                f"all below your threshold of {threshold}."
                if below_count
                else f"No strong matches today (nothing scored above your threshold of {threshold})."
            )
        else:
            subject = f"JobFetcher — no new matches since {since_day} ({day})"
            line = (
                f"No new matches since {since_day} — {below_count} scored, "
                f"all below your threshold of {threshold}."
                if below_count
                else f"No new matches since {since_day} "
                f"(nothing new crossed your threshold of {threshold})."
            )
        text_body = f"{line}\n"
        html_body = _html_shell(day, f'<p style="color:#3c4043;">{escape(line)}</p>')
        return subject, html_body, text_body

    if n == 0:
        subject = f"JobFetcher — no new matches since {since_day} ({day})"
    else:
        subject = f"JobFetcher — {n} new match{'es' if n != 1 else ''} ({day})"
    footer = f"+{below_count} more scored below your threshold of {threshold}" if below_count else None

    # ---- plaintext fallback (the apply URL is prominent, on its own line) ----
    if n == 0:
        text_lines = [f"JobFetcher digest — {day} — no new matches since {since_day}", ""]
    else:
        text_lines = [
            f"JobFetcher digest — {day} — {n} new match{'es' if n != 1 else ''}", ""
        ]
    for card in new_cards:
        text_lines.extend(_card_text_lines(card, threshold=threshold))
    if open_cards:
        text_lines.extend(_still_open_text_lines(open_cards, safe_full_url))
    if footer:
        # B-1: linkify the below-threshold footer — the full list is where those N are reachable.
        text_lines.append(
            f"{footer} — see the full list: {safe_full_url}" if safe_full_url else footer
        )
    text_body = "\n".join(text_lines) + "\n"

    # ---- HTML body (new section first: one card per group; then the compact still-open) ----
    if n == 0:
        summary = (
            f'<p style="color:#3c4043;margin:0 0 16px;">No new matches since {since_day} '
            f"(nothing new crossed your threshold of {threshold}).</p>"
        )
    else:
        summary = (
            f"<p style=\"color:#3c4043;margin:0 0 16px;\">"
            f"<strong>{n}</strong> new role{'s' if n != 1 else ''} at or above your threshold "
            f"of {threshold}{f' · +{below_count} below' if below_count else ''}.</p>"
        )
    new_html = ""
    if new_cards:
        if since is not None:
            new_html += (
                '<h3 style="color:#202124;font-size:15px;margin:16px 0 8px;">'
                "New since last digest</h3>"
            )
        new_html += "".join(_card_html(card, threshold=threshold) for card in new_cards)
    open_html = _still_open_html(open_cards, safe_full_url) if open_cards else ""
    if not footer:
        footer_html = ""
    elif safe_full_url:
        # B-1: the below-threshold footer becomes a link to the full-list page.
        footer_html = (
            f'<p style="color:#80868b;font-size:13px;margin:8px 0 0;">{escape(footer)} &mdash; '
            f'<a href="{escape(safe_full_url, quote=True)}" '
            f'style="color:{_APPLY_BG};text-decoration:none;">see the full list &rarr;</a></p>'
        )
    else:
        footer_html = f'<p style="color:#80868b;font-size:13px;margin:8px 0 0;">{escape(footer)}</p>'
    html_body = _html_shell(day, summary + new_html + open_html + footer_html)
    return subject, html_body, text_body


def _card_text_lines(card: DigestCard, *, threshold: int) -> list[str]:
    """One card's plaintext block: head (+ the `↑ old→new` graduation marker) · why · gap ·
    apply · the dup footnote (`seen n× — scores lo–hi` + the collapsed member ids)."""
    item = card.item
    loc = _location(item)
    head = f"[{item.score}] {_display_title(item)} — {(item.company or 'Unknown company').strip()}"
    if loc:
        head = f"{head} · {loc}"
    if _is_graduation(item, threshold=threshold):
        head = f"{head}  ↑ {item.previous_score}→{item.score}"
    lines = [head, f"    why: {_one_line_why(item)}"]
    gap = _first_gap(item)
    if gap:
        lines.append(f"    gap: {gap}")
    lines.append(f"    apply: {_safe_apply_url(item.apply_url) or '(no link)'}")
    if card.seen_count > 1:
        lines.append(
            f"    seen {card.seen_count}× — scores {card.score_lo}–{card.score_hi} "
            f"(ids: {', '.join(card.member_posting_ids)})"
        )
    lines.append("")
    return lines


def _still_open_text_lines(
    cards: list[DigestCard], full_list_url: str | None = None
) -> list[str]:
    """The still-open section, plaintext: the count line + top-N compact one-liners + the
    "…and n more" overflow line. `full_list_url` (already scheme-checked) linkifies that
    overflow line at the full-list page; without it, today's "see your export" text (B-1)."""
    on = len(cards)
    lines = [f"{on} earlier match{'es' if on != 1 else ''} still open:"]
    for card in cards[:_STILL_OPEN_TOP_N]:
        rep = card.item
        company = (rep.company or "Unknown company").strip()
        lines.append(
            f"  {rep.score} · {_display_title(rep)} — {company} · "
            f"{_safe_apply_url(rep.apply_url) or '(no link)'}"
        )
    if on > _STILL_OPEN_TOP_N:
        more = on - _STILL_OPEN_TOP_N
        tail = f"see the full list: {full_list_url}" if full_list_url else "see your export"
        lines.append(f"  …and {more} more — {tail}")
    lines.append("")
    return lines


def _card_html(card: DigestCard, *, threshold: int) -> str:
    """One group = one bordered card: score badge (+ green `↑ old→new` graduation badge) ·
    title · fit · Company·Location · why · gap · the `seen n×` dup footnote · Apply."""
    item = card.item
    title = escape(_display_title(item))
    company = escape((item.company or "Unknown company").strip())
    loc = escape(_location(item))
    why = escape(_one_line_why(item))
    fit = escape((item.fit_category or "").replace("_", " ").strip())
    badge = _badge_color(item.fit_category)
    company_line = f"{company} &middot; {loc}" if loc else company

    # The graduation badge: green TEXT next to the score badge (inline styles only — no
    # images/CSS classes, so every email client renders it). `↑ 55→72` = previous→current.
    grad_html = ""
    if _is_graduation(item, threshold=threshold):
        grad_html = (
            f'<span style="color:{_GRAD_GREEN};font-size:13px;font-weight:bold;'
            f'margin-left:6px;">&#8593;&nbsp;{item.previous_score}&rarr;{item.score}</span>'
        )

    gap = _first_gap(item)
    gap_html = (
        f'<div style="color:#a8641b;font-size:13px;margin:4px 0 0;">&#9888; {escape(gap)}</div>'
        if gap else ""
    )

    # The dup-collapse footnote: this card stands for `seen_count` same-fingerprint postings.
    seen_html = ""
    if card.seen_count > 1:
        seen_html = (
            f'<div style="color:#80868b;font-size:12px;margin:4px 0 0;">'
            f"seen {card.seen_count}&times; &mdash; scores "
            f"{card.score_lo}&ndash;{card.score_hi}</div>"
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
        f"{grad_html}"
        f'<span style="font-weight:bold;font-size:16px;color:#202124;margin-left:8px;">{title}</span>'
        f"{fit_label}"
        f'<div style="color:#5f6368;font-size:14px;margin:6px 0 0;">{company_line}</div>'
        f'<div style="color:#3c4043;font-size:14px;margin:8px 0 0;">&#10003; {why}</div>'
        f"{gap_html}"
        f"{seen_html}"
        f"<div>{apply_html}</div>"
        "</td></tr></table>"
    )


def _still_open_html(cards: list[DigestCard], full_list_url: str | None = None) -> str:
    """The still-open section, HTML: the count heading + top-N compact one-liners
    (`{score} · {title} — {company} · Apply`) + the "…and n more" overflow line. Deliberately
    NOT full cards — these already surfaced in an earlier digest; they stay reachable, not loud.
    `full_list_url` (already scheme-checked) linkifies the overflow line to the full-list page."""
    on = len(cards)
    parts = [
        '<h3 style="color:#202124;font-size:15px;margin:20px 0 8px;">'
        f"{on} earlier match{'es' if on != 1 else ''} still open</h3>"
    ]
    for card in cards[:_STILL_OPEN_TOP_N]:
        rep = card.item
        title = escape(_display_title(rep))
        company = escape((rep.company or "Unknown company").strip())
        safe_url = _safe_apply_url(rep.apply_url)
        link = (
            f' &middot; <a href="{escape(safe_url, quote=True)}" '
            f'style="color:{_APPLY_BG};text-decoration:none;font-weight:bold;">Apply &rarr;</a>'
            if safe_url else ""
        )
        parts.append(
            '<div style="color:#3c4043;font-size:14px;margin:0 0 6px;">'
            f"<span style=\"font-weight:bold;\">{rep.score}</span> &middot; {title} "
            f"&mdash; {company}{link}</div>"
        )
    if on > _STILL_OPEN_TOP_N:
        more = on - _STILL_OPEN_TOP_N
        if full_list_url:
            tail = (
                f'<a href="{escape(full_list_url, quote=True)}" '
                f'style="color:{_APPLY_BG};text-decoration:none;">see the full list &rarr;</a>'
            )
        else:
            tail = "see your export"
        parts.append(
            '<p style="color:#80868b;font-size:13px;margin:6px 0 0;">'
            f"&hellip;and {more} more &mdash; {tail}</p>"
        )
    return "".join(parts)


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
