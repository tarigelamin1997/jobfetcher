"""Step-6 Notifier unit tests (no AWS): the `render_digest` renderer (matches → subject/html/
text carry score/title/company/apply-link + the below-count footer; zero matches → a valid
"no matches" email — VG5 negative), the digest-truthfulness pure functions
(`split_new_and_still_open` / `collapse_duplicates`) + the graduation badge + the new/still-open
sections, `SesNotifier` over a fake SES client (correct Source/To/Subject/Html+Text; SES error →
NotifierError; missing sender → NotifierError), and the `notify()` orchestration over a fake
repo + fake notifier (counts; zero-matches still sends; send failure RAISES; since/max-age
threading). Each carries a negative."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from jobfetcher.adapters.ses_notifier import _SENDER_ENV, SesNotifier
from jobfetcher.core.ingest import notify
from jobfetcher.core.notifier import (
    collapse_duplicates,
    render_digest,
    split_new_and_still_open,
)
from jobfetcher.core.ports import NotifierError, ShortlistItem

# A canned "last digest went out" time for the truthfulness tests (since != None), plus a
# judgment written AFTER it (fresh — can be news) and one BEFORE it (stale — a daily repeat).
_SINCE = datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc)
_FRESH = _SINCE + timedelta(hours=6)
_STALE = _SINCE - timedelta(days=3)


# --------------------------------------------------------------------------- builders
def _item(score: int, pid: str = "p", **over: Any) -> ShortlistItem:
    base: dict[str, Any] = {
        "posting_id": pid,
        "title": "Senior Data Engineer",
        "company": "Acme Corp",
        "apply_url": "https://jobs.example.com/apply/123",
        "normalized_title": "Data Engineer",
        "score": score,
        "fit_category": "strong_fit",
        "strengths": ["strong Python", "fintech background"],
        "gaps": ["no Spark"],
        "strategic_assessment": "Lead with the pipeline project.",
        "city": "Riyadh",
        "country": "sa",
    }
    base.update(over)
    return ShortlistItem(**base)


# --------------------------------------------------------------------------- render_digest (matches)
def test_render_digest_matches_carry_core_fields():
    items = [_item(90, "p1"), _item(72, "p2", company="Beta Ltd")]
    subject, html, text = render_digest(items, below_count=3, threshold=60, date=date(2026, 6, 27))

    assert "2 new matches" in subject and "2026-06-27" in subject
    # score, title, company, apply link present in BOTH bodies
    for body in (html, text):
        assert "90" in body and "72" in body
        assert "Data Engineer" in body
        assert "Acme Corp" in body and "Beta Ltd" in body
        assert "https://jobs.example.com/apply/123" in body
    # the below-count footer (new phrasing)
    assert "+3 more scored below your threshold of 60" in text
    assert "+3 more scored below your threshold of 60" in html
    # html has a clickable apply link
    assert 'href="https://jobs.example.com/apply/123"' in html


def test_render_digest_prominent_apply_button_and_card_fields():
    # the email-UX headline: every surfaced job renders a PROMINENT Apply BUTTON (a styled <a>,
    # not a buried text link) + the score badge, fit label, location, and a gap line.
    item = _item(90, city="Riyadh", country="sa")
    _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    # a button-styled anchor to the apply url (inline-block + background = a button, not plain text)
    assert 'href="https://jobs.example.com/apply/123"' in html
    assert "display:inline-block" in html and "Apply" in html
    # score badge + fit label + location + gap all present in the card
    assert "90</span>" in html                 # the score badge pill
    assert "strong fit" in html                 # fit_category label (underscore → space)
    assert "Riyadh, SA" in html                 # Company · Location (country upper-cased)
    assert "no Spark" in html                    # the gap line
    # plaintext keeps the full apply URL on its own line + the location
    assert "apply: https://jobs.example.com/apply/123" in text
    assert "Riyadh, SA" in text


def test_render_digest_missing_apply_url_shows_no_link_state():
    # negative: a job without an apply link renders a clear "no link" state, not a broken/empty
    # button, and emits NO anchor for it.
    item = _item(80, apply_url=None)
    _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "No apply link available" in html
    assert "<a " not in html  # no anchor emitted for the missing link
    assert "(no link)" in text


def test_render_digest_singular_match_wording():
    subject, html, _ = render_digest([_item(80)], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "1 new match (" in subject  # singular, no 'es'
    assert "more scored below" not in html  # no below-count footer when nothing is below


def test_render_digest_orders_as_given_and_uses_raw_title_fallback():
    # normalized_title None → falls back to the raw source title (never blank)
    item = _item(85, normalized_title=None)
    _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "Senior Data Engineer" in html and "Senior Data Engineer" in text


# --------------------------------------------------------------------------- render_digest (VG5 negative)
def test_render_digest_zero_matches_is_valid_no_matches_email():
    # VG5 negative: zero surfaced → a valid "no matches" email, NOT a crash or blank body.
    subject, html, text = render_digest([], below_count=7, threshold=60, date=date(2026, 6, 27))
    assert "no matches" in subject.lower()
    assert html.strip() and text.strip()  # never blank
    assert "7 scored" in text and "threshold of 60" in text
    assert "7 scored" in html and "threshold of 60" in html
    assert html.lower().startswith("<html>")


def test_render_digest_zero_scored_at_all_still_valid():
    # negative edge: nothing scored at all (below_count 0) → still a valid email.
    subject, html, text = render_digest([], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "no matches" in subject.lower()
    assert "nothing scored above your threshold of 60" in text
    assert html.strip()


def test_render_digest_escapes_html_in_user_content():
    # security: a hostile title/company must be escaped into the HTML body, not injected raw.
    item = _item(80, title="<script>x</script>", company="A & B <b>", normalized_title=None)
    _, html, _ = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "<script>x</script>" not in html  # the raw tag must not survive
    assert "&lt;script&gt;" in html
    assert "A &amp; B" in html


def test_render_digest_rejects_javascript_scheme_apply_url():
    # security: a hostile scheme on the untrusted apply_url must NOT become a clickable link —
    # neither in HTML nor in the plaintext body.
    item = _item(80, apply_url="javascript:alert(1)")
    _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert "javascript:" not in html.lower()
    assert "javascript:" not in text.lower()
    assert "<a " not in html  # no anchor was emitted for the unsafe URL
    assert "(no link)" in text


def test_render_digest_attribute_breakout_apply_url_is_escaped():
    # an https URL crafted to break out of the href attribute: the scheme passes the allowlist,
    # but the quote MUST be escaped (no attribute breakout, no injected event handler).
    hostile = 'https://x.com/" onmouseover="alert(1)'
    item = _item(80, apply_url=hostile)
    _, html, _ = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    assert 'onmouseover="alert(1)"' not in html  # the raw breakout must not survive
    assert "&quot;" in html  # the " was escaped


def test_render_digest_http_and_https_render_clickable_link():
    for url in ("http://jobs.test/apply", "https://jobs.test/apply"):
        item = _item(80, apply_url=url)
        _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
        assert f'href="{url}"' in html  # the button anchor points at the url (style attr follows)
        assert url in text


def test_render_digest_none_and_empty_fields_do_not_crash():
    # an item with None/empty company/apply_url/strategic_assessment + empty strengths renders
    # cleanly — no None leak, no IndexError, score 0 renders.
    item = _item(
        0,
        company=None,
        apply_url=None,
        strategic_assessment=None,
        strengths=[],
        normalized_title=None,
    )
    _, html, text = render_digest([item], below_count=0, threshold=60, date=date(2026, 6, 27))
    for body in (html, text):
        assert "None" not in body  # no None leaked into the rendered output
        assert "0" in body  # score 0 renders
    assert "(no link)" in text  # missing apply_url → plain "(no link)", not a link
    item2 = _item(50, company="", apply_url="", strategic_assessment="", strengths=[])
    # empty strings must not crash either
    render_digest([item2], below_count=0, threshold=60, date=date(2026, 6, 27))


# --------------------------------------------------------------------------- split_new_and_still_open (pure)
def test_split_first_ever_digest_everything_is_new():
    # A1/N2: since=None (no digest ever sent) → ALL items are new — even one whose
    # previous_score already sits above the threshold. The first-ever digest is unbounded.
    items = [_item(90, "a", previous_score=88), _item(80, "b")]
    new, still_open = split_new_and_still_open(items, since=None, threshold=60)
    assert new == items
    assert still_open == []


def test_split_scored_at_vs_since_semantics():
    # F1 — the daily-operation truth table. NEW iff the judgment is FRESH (scored_at > since)
    # AND it is news (first scoring, or a graduation); everything else is STILL OPEN.
    a = _item(80, "a", previous_score=None, scored_at=_STALE)  # (a) daily repeat: scored once,
    #     before the last digest → STILL OPEN (previous_score alone would mislabel this NEW)
    b = _item(75, "b", previous_score=None, scored_at=_FRESH)  # (b) fresh first scoring → NEW
    c = _item(88, "c", previous_score=88, scored_at=_FRESH)    # (c) fresh reassess, NOT a
    #     graduation → STILL OPEN even though the judgment is fresh (being re-judged isn't news)
    d = _item(72, "d", previous_score=55, scored_at=_FRESH,     # (d) fresh HONEST graduation
              prior_profile_changed=True)                       #     (profile changed) → NEW
    e = _item(70, "e", previous_score=50, scored_at=_STALE)    # (e) graduation that happened
    #     BEFORE the last digest → STILL OPEN (already announced, never re-announced)
    items = [a, b, c, d, e]
    new, still_open = split_new_and_still_open(items, since=_SINCE, threshold=60)
    assert [i.posting_id for i in new] == ["b", "d"]
    assert [i.posting_id for i in still_open] == ["a", "c", "e"]
    # (f) first-ever digest (since=None): the same items are ALL new
    new_f, open_f = split_new_and_still_open(items, since=None, threshold=60)
    assert [i.posting_id for i in new_f] == ["a", "b", "c", "d", "e"]
    assert open_f == []


def test_split_null_scored_at_is_new_defensively():
    # negative/defensive: a NULL scored_at (save_score always stamps it — pathological) must
    # never silently demote a match → NEW, the unknown-age-included philosophy.
    items = [_item(90, "x", previous_score=88, scored_at=None)]
    new, still_open = split_new_and_still_open(items, since=_SINCE, threshold=60)
    assert [i.posting_id for i in new] == ["x"]
    assert still_open == []


def test_split_previous_exactly_at_threshold_is_still_open():
    # negative boundary: a FRESH re-score with previous_score == threshold means it ALREADY
    # surfaced — not a graduation, not new.
    items = [_item(70, "edge", previous_score=60, scored_at=_FRESH)]
    new, still_open = split_new_and_still_open(items, since=_SINCE, threshold=60)
    assert new == []
    assert [i.posting_id for i in still_open] == ["edge"]


# --------------------------------------------------------------------------- collapse_duplicates (pure)
def test_collapse_same_fingerprint_one_card_with_count_and_range():
    # C1 + C2: two same-fingerprint items collapse to ONE card (grouping reduces the card
    # count) with the highest score as representative + the seen-count and lo–hi range.
    items = [
        _item(88, "a", fingerprint="fp-1"),
        _item(80, "solo", fingerprint="fp-2"),
        _item(75, "b", fingerprint="fp-1"),
    ]
    cards = collapse_duplicates(items)
    assert len(cards) == 2  # 3 items → 2 cards
    top = cards[0]
    assert top.item.posting_id == "a" and top.item.score == 88  # representative = highest
    assert top.seen_count == 2
    assert (top.score_lo, top.score_hi) == (75, 88)
    assert top.member_posting_ids == ("a", "b")  # collapsed identity preserved
    assert cards[1].item.posting_id == "solo" and cards[1].seen_count == 1


def test_collapse_all_dups_single_card():
    # N4: every item shares one fingerprint → exactly ONE card.
    items = [_item(88, "a", fingerprint="fp"), _item(82, "b", fingerprint="fp"),
             _item(75, "c", fingerprint="fp")]
    cards = collapse_duplicates(items)
    assert len(cards) == 1
    assert cards[0].seen_count == 3
    assert (cards[0].score_lo, cards[0].score_hi) == (75, 88)
    assert cards[0].member_posting_ids == ("a", "b", "c")


def test_collapse_missing_fingerprint_never_merges():
    # negative: None/empty/blank fingerprints are UNKNOWN, not equal — each stays its own card.
    items = [_item(90, "a", fingerprint=None), _item(85, "b", fingerprint=None),
             _item(80, "c", fingerprint="  ")]
    assert len(collapse_duplicates(items)) == 3


# --------------------------------------------------------------------------- graduation badge
def test_graduation_badge_renders_in_html_and_text():
    # B1: a FRESH, HONEST graduation (scored after the last digest, previous < threshold <= score,
    # AND under a changed profile) → the green ↑ old→new badge in BOTH bodies.
    item = _item(72, previous_score=55, scored_at=_FRESH, prior_profile_changed=True)
    _, html, text = render_digest([item], below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=_SINCE)
    assert "55&rarr;72" in html and "#137333" in html  # green text badge, old→new
    assert "↑ 55→72" in text


def test_no_badge_when_profile_unchanged_even_though_score_crossed():
    # THE honest-graduation negative (the exact scan bug): previous < threshold <= score AND a
    # fresh judgment, BUT prior_profile_changed is False — the crossing is LLM sampling noise, not
    # a skill gain. NO badge anywhere, and it lands STILL-OPEN (not announced as new).
    item = _item(72, previous_score=55, scored_at=_FRESH, prior_profile_changed=False)
    subject, html, text = render_digest([item], below_count=0, threshold=60,
                                        date=date(2026, 6, 27), since=_SINCE)
    assert "&#8593;" not in html and "55&rarr;72" not in html  # no green badge
    assert "↑" not in text
    assert "no new matches since" in subject.lower()  # folded to still-open, never announced
    assert "1 earlier match still open" in html


def test_no_badge_when_previous_already_above_threshold():
    # B2: a fresh re-score with previous >= threshold — it already surfaced; nothing crossed,
    # no badge anywhere (it lands still-open, where badges never render).
    item = _item(90, previous_score=88, scored_at=_FRESH)
    _, html, text = render_digest([item], below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=_SINCE)
    assert "&#8593;" not in html and "88&rarr;90" not in html
    assert "↑" not in text


def test_no_badge_when_previous_score_is_none_but_item_is_new():
    # B2/N6 pinned: previous_score None + a fresh judgment ⇒ the item IS new (first-ever
    # scoring) but gets NO badge — there is no old score to graduate from.
    item = _item(90, previous_score=None, scored_at=_FRESH)
    subject, html, text = render_digest([item], below_count=0, threshold=60,
                                        date=date(2026, 6, 27), since=_SINCE)
    assert "1 new match" in subject  # it IS new...
    assert "&#8593;" not in html     # ...but never badged
    assert "↑" not in text


def test_no_graduations_means_no_badges():
    # N3: a mixed digest with zero graduations renders zero badges — a fresh first scoring,
    # a fresh non-graduated re-score, and a stale daily repeat.
    items = [_item(90, "a", previous_score=None, scored_at=_FRESH),
             _item(85, "b", previous_score=85, scored_at=_FRESH),
             _item(70, "c", scored_at=_STALE)]
    _, html, text = render_digest(items, below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=_SINCE)
    assert "&#8593;" not in html
    assert "↑" not in text


# --------------------------------------------------------------------------- new/still-open sections
def test_new_section_first_then_still_open_compact():
    # A2 (render level): the fresh new item leads as a FULL card; the stale (daily-repeat)
    # item renders as a compact one-liner (count + score · title — company · Apply), not a card.
    new_item = _item(72, "new1", previous_score=None, scored_at=_FRESH, company="NewCo")
    open_item = _item(88, "old1", scored_at=_STALE, company="OldCo",
                      apply_url="https://jobs.test/apply/old1")
    _, html, text = render_digest([open_item, new_item], below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=_SINCE)
    assert "New since last digest" in html
    assert "1 earlier match still open" in html
    assert html.index("New since last digest") < html.index("earlier match")  # new leads
    # exactly ONE full card (the ✓ why-line only renders on cards) — the open item is compact
    assert html.count("&#10003;") == 1
    assert "OldCo" in html and "https://jobs.test/apply/old1" in html
    # plaintext mirrors both sections
    assert "1 earlier match still open:" in text
    assert "88 · " in text and "OldCo" in text


def test_still_open_beyond_top5_shows_more_line():
    # 6 still-open matches (daily-repeat shape: scored once, BEFORE the last digest) → the
    # top 5 render as one-liners; the 6th folds into the "…and 1 more — see your export"
    # overflow (count truthful, email stays scannable).
    opens = [_item(90 - i, f"o{i}", scored_at=_STALE, company=f"Comp{i}ny")
             for i in range(6)]
    _, html, text = render_digest(opens, below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=_SINCE)
    assert "6 earlier matches still open" in html
    assert "and 1 more" in html and "see your export" in html
    assert "Comp0ny" in html and "Comp4ny" in html
    assert "Comp5ny" not in html  # the 6th is counted, not listed
    assert "…and 1 more — see your export" in text


def test_zero_new_email_says_so_and_still_open_renders():
    # N1: nothing new, but earlier matches remain (one daily repeat, one fresh-but-not-news
    # re-score) → subject + body say "no new matches since {date}", the still-open section
    # still renders, and the email is still a valid send.
    opens = [_item(88, "o1", scored_at=_STALE),
             _item(70, "o2", previous_score=75, scored_at=_FRESH)]
    subject, html, text = render_digest(opens, below_count=3, threshold=60,
                                        date=date(2026, 6, 27), since=_SINCE)
    assert "no new matches since 2026-06-20" in subject
    assert "No new matches since 2026-06-20" in html
    assert "2 earlier matches still open" in html
    assert "no new matches since 2026-06-20" in text.lower()
    assert "2 earlier matches still open:" in text
    assert "+3 more scored below your threshold of 60" in text


def test_zero_everything_with_prior_digest_is_honest_no_new_email():
    # negative edge: a prior digest exists and NOTHING is left at all → still a valid email
    # that says "no new matches since {date}" (never a crash/blank/skip).
    subject, html, text = render_digest([], below_count=2, threshold=60,
                                        date=date(2026, 6, 27), since=_SINCE)
    assert "no new matches since 2026-06-20" in subject
    assert "2 scored" in text and "threshold of 60" in text
    assert html.strip()


def test_dup_footnote_renders_on_collapsed_card():
    # C1 (render level): a same-fingerprint pair renders ONE card footnoted `seen 2× — scores
    # lo–hi`, with the collapsed members' ids preserved in the plaintext footnote.
    items = [_item(88, "dup-a", fingerprint="fp"), _item(75, "dup-b", fingerprint="fp")]
    _, html, text = render_digest(items, below_count=0, threshold=60,
                                  date=date(2026, 6, 27), since=None)
    assert html.count("&#10003;") == 1  # one card, not two
    assert "seen 2&times;" in html and "75&ndash;88" in html
    assert "seen 2× — scores 75–88" in text
    assert "(ids: dup-a, dup-b)" in text  # collapsed identity, plaintext only


def test_dup_group_straddling_sections_renders_once_in_new():
    # F4: a fingerprint group with one NEW member (fresh) and one STILL-OPEN member (stale)
    # must render ONCE — the WHOLE group goes NEW (any member new) with its full seen-count
    # and range — never twice (a full card AND a still-open one-liner).
    stale = _item(88, "tw-old", fingerprint="fp-tw", scored_at=_STALE)
    fresh = _item(72, "tw-new", fingerprint="fp-tw", previous_score=None, scored_at=_FRESH)
    subject, html, text = render_digest([stale, fresh], below_count=0, threshold=60,
                                        date=date(2026, 6, 27), since=_SINCE)
    assert "1 new match" in subject
    assert html.count("&#10003;") == 1                     # exactly ONE full card in the email
    assert "seen 2&times;" in html and "72&ndash;88" in html  # the group's full count + range
    assert "earlier match" not in html                     # no still-open section — the stale
    assert "still open" not in text                        # twin moved WITH its group
    assert "(ids: tw-old, tw-new)" in text                 # both identities preserved once
    assert text.count("tw-old") == 1                       # ...and only once (no second line)


# --------------------------------------------------------------------------- full-list link (B-1)
_FULL_URL = "https://data.example.com/reports/2026-06-27/jobs-r.html?sig=abc"


def test_full_list_url_linkifies_footer_and_overflow():
    # B-1: with a full_list_url BOTH dead lines become clickable https links — the below-
    # threshold footer AND the still-open "…and N more" overflow — in HTML and plaintext.
    opens = [_item(90 - i, f"o{i}", scored_at=_STALE, company=f"Comp{i}ny") for i in range(6)]
    _, html, text = render_digest(opens, below_count=3, threshold=60, date=date(2026, 6, 27),
                                  since=_SINCE, full_list_url=_FULL_URL)
    # HTML: the presigned link is an href on both lines; the old dead "see your export" is gone
    assert f'href="{_FULL_URL}"' in html
    assert html.count(f'href="{_FULL_URL}"') == 2  # overflow line + footer line
    assert "see the full list" in html
    assert "see your export" not in html
    # plaintext: the url appears on both the overflow line and the footer line
    assert f"see the full list: {_FULL_URL}" in text
    assert text.count(_FULL_URL) == 2
    assert "see your export" not in text
    assert f"+3 more scored below your threshold of 60 — see the full list: {_FULL_URL}" in text


def test_full_list_url_none_keeps_plain_text():
    # graceful default: no full_list_url → today's plain text is unchanged (no link).
    opens = [_item(90 - i, f"o{i}", scored_at=_STALE) for i in range(6)]
    _, html, text = render_digest(opens, below_count=3, threshold=60, date=date(2026, 6, 27),
                                  since=_SINCE)
    assert "see the full list" not in html and "see the full list" not in text
    assert "see your export" in html and "see your export" in text
    assert "+3 more scored below your threshold of 60" in text


def test_full_list_url_hostile_scheme_is_not_linkified():
    # security: a hostile scheme on the full_list_url must NOT become a link — it degrades to
    # today's plain text, exactly like a None url (reuses the _safe_apply_url allowlist).
    opens = [_item(90 - i, f"o{i}", scored_at=_STALE) for i in range(6)]
    _, html, text = render_digest(opens, below_count=3, threshold=60, date=date(2026, 6, 27),
                                  since=_SINCE, full_list_url="javascript:alert(1)")
    assert "javascript:" not in html.lower() and "javascript:" not in text.lower()
    assert "see the full list" not in html  # no link emitted for the unsafe url
    assert "see your export" in html and "see your export" in text  # degraded to plain text


# --------------------------------------------------------------------------- SesNotifier
class _FakeSes:
    """Captures send_email calls; returns a canned MessageId."""

    def __init__(self, message_id: str = "ses-msg-1") -> None:
        self.calls: list[dict[str, Any]] = []
        self._message_id = message_id

    def send_email(self, **kw: Any) -> dict:
        self.calls.append(kw)
        return {"MessageId": self._message_id}


def test_ses_notifier_sends_with_correct_shape():
    client = _FakeSes()
    notifier = SesNotifier(sender="from@x.com", client=client)
    mid = notifier.send(
        subject="Subj", html_body="<p>h</p>", text_body="t", recipients=["to@x.com"]
    )
    assert mid == "ses-msg-1"
    call = client.calls[0]
    assert call["Source"] == "from@x.com"
    assert call["Destination"]["ToAddresses"] == ["to@x.com"]
    assert call["Message"]["Subject"]["Data"] == "Subj"
    assert call["Message"]["Body"]["Html"]["Data"] == "<p>h</p>"
    assert call["Message"]["Body"]["Text"]["Data"] == "t"
    assert call["Message"]["Body"]["Html"]["Charset"] == "UTF-8"


def test_ses_notifier_missing_sender_raises(monkeypatch):
    # negative: no sender env + no explicit sender → NotifierError at construction.
    monkeypatch.delenv(_SENDER_ENV, raising=False)
    with pytest.raises(NotifierError, match=_SENDER_ENV):
        SesNotifier()


def test_ses_notifier_blank_sender_env_raises(monkeypatch):
    monkeypatch.setenv(_SENDER_ENV, "   ")
    with pytest.raises(NotifierError, match=_SENDER_ENV):
        SesNotifier()


def test_ses_notifier_reads_sender_from_env(monkeypatch):
    monkeypatch.setenv(_SENDER_ENV, "env@x.com")
    client = _FakeSes()
    SesNotifier(client=client).send(
        subject="s", html_body="h", text_body="t", recipients=["to@x.com"]
    )
    assert client.calls[0]["Source"] == "env@x.com"


def test_ses_notifier_wraps_ses_error_in_notifier_error():
    # negative: a MessageRejected-style SES failure → NotifierError, never a raw boto3 error.
    class _Boom(_FakeSes):
        def send_email(self, **kw):
            raise RuntimeError("MessageRejected: identity not verified")

    notifier = SesNotifier(sender="from@x.com", client=_Boom())
    with pytest.raises(NotifierError, match="send_email failed"):
        notifier.send(subject="s", html_body="h", text_body="t", recipients=["to@x.com"])


def test_ses_notifier_no_recipients_raises():
    notifier = SesNotifier(sender="from@x.com", client=_FakeSes())
    with pytest.raises(NotifierError, match="recipient"):
        notifier.send(subject="s", html_body="h", text_body="t", recipients=[])


# --------------------------------------------------------------------------- notify() orchestration
class _FakeRepo:
    """Minimal repo for the notify() orchestration: a profile row + a canned shortlist + the
    last-digest send time (`None` = no digest ever sent — the N7 first-ever case)."""

    def __init__(self, threshold, surfaced, below, last_sent=None):
        self._row = {"profile": {}, "threshold": threshold,
                     "hard_floor": 50, "near_miss_band": 10}
        self._surfaced = surfaced
        self._below = below
        self._last_sent = last_sent

    def get_profile(self, user_id):
        return self._row

    def get_last_digest_sent_at(self, *, user_id):
        return self._last_sent

    def get_scored_shortlist(self, *, threshold, since=None, max_age_days=None):
        # `notify()` is the single threshold authority — it passes the resolved threshold in;
        # it also threads `since` (the last digest time) + the digest age bound through.
        self.seen_threshold = threshold
        self.seen_since = since
        self.seen_max_age_days = max_age_days
        return list(self._surfaced), self._below

    def get_all_scored(self, *, max_age_days=None):
        # B-1: the full-list report input — the whole scored set (surfaced + below). The fake
        # reuses the surfaced list (enough to render a page); records the age bound threaded in.
        self.seen_all_max_age = max_age_days
        return list(self._surfaced)


class _FakeReportStore:
    """Captures the full-list report upload + presign; can be forced to fail either step (the
    B-1 non-fatal-degradation contract)."""

    url = "https://data.example.com/reports/2026-06-27/jobs-r.html?sig=abc"

    def __init__(self, *, fail_put: bool = False, fail_presign: bool = False) -> None:
        self.puts: list[dict[str, Any]] = []
        self._fail_put = fail_put
        self._fail_presign = fail_presign

    def put_report(self, *, html: str, key: str) -> None:
        if self._fail_put:
            raise RuntimeError("s3 put_object boom")
        self.puts.append({"html": html, "key": key})

    def presign(self, *, key: str, expires: int) -> str:
        if self._fail_presign:
            raise RuntimeError("presign boom")
        return self.url


class _FakeNotifier:
    def __init__(self, *, fail: bool = False):
        self.sent: list[dict[str, Any]] = []
        self._fail = fail

    def send(self, *, subject, html_body, text_body, recipients):
        if self._fail:
            raise NotifierError("send blew up")
        self.sent.append({"subject": subject, "html": html_body,
                          "text": text_body, "recipients": recipients})
        return "msg-1"


def test_notify_sends_and_counts():
    repo = _FakeRepo(threshold=60, surfaced=[_item(90), _item(70, "p2")], below=4)
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier,
                 recipient_email="to@x.com", run_date=date(2026, 6, 27))
    assert out == {"surfaced": 2, "below_threshold": 4, "sent": 1}
    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent["recipients"] == ["to@x.com"]
    assert "2 new matches" in sent["subject"]
    assert "+4 more scored below your threshold of 60" in sent["text"]


def test_notify_zero_matches_still_sends():
    # VG5 negative through the orchestrator: nothing surfaced → still exactly one email sent.
    repo = _FakeRepo(threshold=60, surfaced=[], below=5)
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com")
    assert out == {"surfaced": 0, "below_threshold": 5, "sent": 1}
    assert len(notifier.sent) == 1
    assert "no matches" in notifier.sent[0]["subject"].lower()


def test_notify_send_failure_raises():
    # email is the v0 surface — a send failure is a run failure, NOT a swallowed warning.
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=0)
    with pytest.raises(NotifierError):
        notify(run_id="r", repo=repo, notifier=_FakeNotifier(fail=True),
               recipient_email="to@x.com")


def test_notify_missing_profile_raises():
    class _NoProfileRepo(_FakeRepo):
        def get_profile(self, user_id):
            return None

    repo = _NoProfileRepo(threshold=60, surfaced=[], below=0)
    with pytest.raises(Exception, match="no profile"):
        notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="to@x.com")


def test_notify_null_threshold_falls_back_to_default():
    # a NULL threshold → the documented default (60) is used for the digest header text.
    repo = _FakeRepo(threshold=None, surfaced=[_item(90)], below=0)
    notifier = _FakeNotifier()
    notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com")
    assert "threshold of 60" in notifier.sent[0]["html"]


def test_notify_passes_resolved_threshold_to_shortlist():
    # notify() is the single threshold authority: it passes the resolved threshold to
    # get_scored_shortlist (which no longer re-derives its own constant).
    repo = _FakeRepo(threshold=75, surfaced=[_item(90)], below=0)
    notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="to@x.com")
    assert repo.seen_threshold == 75


def test_notify_null_threshold_passes_default_to_shortlist():
    repo = _FakeRepo(threshold=None, surfaced=[_item(90)], below=0)
    notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="to@x.com")
    assert repo.seen_threshold == 60  # the documented default, not a re-derived constant


def test_notify_empty_recipient_raises_before_rendering():
    # an empty recipient fails LOUDLY up front (NotifierError), not later at SES.
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=0)
    with pytest.raises(NotifierError, match="recipient_email"):
        notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="")


def test_notify_first_ever_digest_resolves_since_none():
    # N7: no run_log rows → get_last_digest_sent_at is None → since=None reaches the
    # shortlist + renderer, so EVERYTHING is new (even a previously-scored item).
    repo = _FakeRepo(threshold=60, surfaced=[_item(90, previous_score=88)], below=0)
    notifier = _FakeNotifier()
    notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
           run_date=date(2026, 6, 27))
    assert repo.seen_since is None
    assert "1 new match" in notifier.sent[0]["subject"]  # not shunted to still-open


def test_notify_threads_since_and_max_age_to_shortlist():
    # notify() resolves since = the last digest send time and threads max_age_days (the
    # handler passes spec.digest_max_age_days) into the shortlist query. The surfaced item is
    # a daily repeat (scored BEFORE the last digest) → still open, honestly not news.
    repo = _FakeRepo(threshold=60, surfaced=[_item(90, scored_at=_STALE)], below=0,
                     last_sent=_SINCE)
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
                 run_date=date(2026, 6, 27), max_age_days=90)
    assert repo.seen_since == _SINCE
    assert repo.seen_max_age_days == 90
    # the previously-surfaced 90 is STILL OPEN → the digest honestly reports no new matches
    assert "no new matches since 2026-06-20" in notifier.sent[0]["subject"]
    assert "1 earlier match still open" in notifier.sent[0]["html"]
    assert out == {"surfaced": 1, "below_threshold": 0, "sent": 1}  # counts unchanged


def test_notify_default_max_age_is_none_unbounded():
    # negative: no max_age_days arg → None reaches the repo (no silent default age cutoff).
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=0)
    notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="to@x.com")
    assert repo.seen_max_age_days is None


# --------------------------------------------------------------------------- notify() + report (B-1)
def test_notify_with_report_store_builds_and_embeds_link():
    # positive (mocked S3+repo): a report is uploaded at the dated key and its presigned https
    # link is embedded in the digest (the below-threshold footer, since below=4).
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=4)
    store = _FakeReportStore()
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
                 run_date=date(2026, 6, 27), report_store=store)
    assert out == {"surfaced": 1, "below_threshold": 4, "sent": 1}
    # uploaded once, at reports/{run_date}/jobs-{run_id}.html, with real HTML
    assert len(store.puts) == 1
    assert store.puts[0]["key"] == "reports/2026-06-27/jobs-r.html"
    assert store.puts[0]["html"].lower().startswith("<!doctype html>")
    # the presigned link is in BOTH bodies (linkified footer)
    sent = notifier.sent[0]
    assert store.url in sent["html"] and store.url in sent["text"]


def test_notify_report_upload_failure_is_nonfatal():
    # NEGATIVE #1a: the S3 upload raises → notify STILL sends (degraded, no link) and does not
    # fail. (mark_digest_sent runs in the handler AFTER notify returns — so a non-raising notify
    # is exactly what keeps the send-once guard writing.)
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=4)
    store = _FakeReportStore(fail_put=True)
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
                 run_date=date(2026, 6, 27), report_store=store)
    assert out["sent"] == 1  # the digest still went out
    sent = notifier.sent[0]
    assert store.url not in sent["html"] and store.url not in sent["text"]  # no link
    assert "+4 more scored below your threshold of 60" in sent["text"]  # plain footer survives


def test_notify_report_presign_failure_is_nonfatal():
    # NEGATIVE #1b: the upload succeeds but presign raises → still sends, degraded to no link.
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=4)
    store = _FakeReportStore(fail_presign=True)
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
                 run_date=date(2026, 6, 27), report_store=store)
    assert out["sent"] == 1
    assert store.url not in notifier.sent[0]["html"]


def test_notify_zero_scored_with_report_store_still_sends():
    # NEGATIVE #2: zero scored jobs + a report store → a valid "no matches" email still sends;
    # the empty full-list page is built without crashing, and the run does not fail.
    repo = _FakeRepo(threshold=60, surfaced=[], below=0)
    store = _FakeReportStore()
    notifier = _FakeNotifier()
    out = notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com",
                 run_date=date(2026, 6, 27), report_store=store)
    assert out == {"surfaced": 0, "below_threshold": 0, "sent": 1}
    assert "no matches" in notifier.sent[0]["subject"].lower()


def test_notify_no_report_store_sends_without_link():
    # default: no report_store → get_all_scored is never called, digest has today's plain text.
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=4)
    notifier = _FakeNotifier()
    notify(run_id="r", repo=repo, notifier=notifier, recipient_email="to@x.com")
    assert not hasattr(repo, "seen_all_max_age")  # the report path was not taken
    assert "see the full list" not in notifier.sent[0]["html"]


def test_notify_threads_max_age_to_get_all_scored():
    # the report scopes to the SAME age window as the digest (spec.digest_max_age_days).
    repo = _FakeRepo(threshold=60, surfaced=[_item(90)], below=1)
    notify(run_id="r", repo=repo, notifier=_FakeNotifier(), recipient_email="to@x.com",
           report_store=_FakeReportStore(), max_age_days=90)
    assert repo.seen_all_max_age == 90
