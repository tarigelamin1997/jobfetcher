"""Step-6 Notifier unit tests (no AWS): the `render_digest` renderer (matches → subject/html/
text carry score/title/company/apply-link + the below-count footer; zero matches → a valid
"no matches" email — VG5 negative), `SesNotifier` over a fake SES client (correct
Source/To/Subject/Html+Text; SES error → NotifierError; missing sender → NotifierError), and
the `notify()` orchestration over a fake repo + fake notifier (counts; zero-matches still
sends; send failure RAISES). Each carries a negative."""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from jobfetcher.adapters.ses_notifier import _SENDER_ENV, SesNotifier
from jobfetcher.core.ingest import notify
from jobfetcher.core.notifier import render_digest
from jobfetcher.core.ports import NotifierError, ShortlistItem


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

    assert "2 matches" in subject and "2026-06-27" in subject
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
    assert "1 match (" in subject  # singular, no 'es'
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
    """Minimal repo for the notify() orchestration: a profile row + a canned shortlist."""

    def __init__(self, threshold, surfaced, below):
        self._row = {"profile": {}, "threshold": threshold,
                     "hard_floor": 50, "near_miss_band": 10}
        self._surfaced = surfaced
        self._below = below

    def get_profile(self, user_id):
        return self._row

    def get_scored_shortlist(self, *, threshold):
        # `notify()` is the single threshold authority — it passes the resolved threshold in.
        self.seen_threshold = threshold
        return list(self._surfaced), self._below


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
    assert "2 matches" in sent["subject"]
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
