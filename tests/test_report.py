"""Unit tests for the full-list report renderer (B-1): `render_full_list` produces a valid,
SELF-CONTAINED HTML page listing EVERY scored job (surfaced + below-threshold) — no external
asset, all user/LLM text escaped, the apply link scheme-allowlisted, and `render_full_list([])`
a valid "no jobs" page (never a crash/blank). Each carries a negative."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from typing import Any

from jobfetcher.core.capture_token import sign, verify
from jobfetcher.core.models import APPLICATION_STATUSES
from jobfetcher.core.ports import ShortlistItem
from jobfetcher.core.report import render_full_list

_GEN = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)
_CAPTURE_KEY = b"report-capture-key"


def _capture_link(pid: str, status: str) -> str:
    return (
        "https://cap.example.com/c?t="
        + sign(posting_id=pid, status=status, expires_at=int(time.time()) + 3600, key=_CAPTURE_KEY)
    )


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
        "scored_at": datetime(2026, 7, 9, 9, 0, tzinfo=timezone.utc),
    }
    base.update(over)
    return ShortlistItem(**base)


def test_render_full_list_is_self_contained_valid_page():
    # a full standalone HTML doc: doctype + inline <style> + inline <script>, and NOTHING
    # external (no CDN link/script/font/image) — the CSP-free page is self-contained.
    items = [_item(90, "a"), _item(72, "b", company="Beta Ltd")]
    html = render_full_list(items, threshold=60, run_date=date(2026, 7, 10), generated_at=_GEN)
    low = html.lower()
    assert low.startswith("<!doctype html>")
    assert "<style>" in low and "<script>" in low
    assert "<table" in low and "</html>" in low
    # no external asset of any kind
    assert "http-equiv" not in low  # no meta-refresh redirect
    assert "<link" not in low and "cdn" not in low
    assert "src=" not in low  # no external script/image src
    # every job is listed
    assert "Acme Corp" in html and "Beta Ltd" in html
    assert "90" in html and "72" in html
    assert "2026-07-10" in html  # the run-date header


def test_render_full_list_renders_capture_links_that_verify():
    # INV-001: with a capture_link, each row gains a "Mark" column with a signed link per
    # application status; every token verifies back to (posting_id, that status).
    html = render_full_list(
        [_item(90, "a")], threshold=60, run_date=date(2026, 7, 10), capture_link=_capture_link
    )
    assert "<th>Mark</th>" in html
    assert ">applied</a>" in html  # the 'applied' hint (Rung 1) is present, prominently
    now = int(time.time())
    tokens = re.findall(r"\?t=([A-Za-z0-9_\-.]+)", html)
    claims = [verify(t, key=_CAPTURE_KEY, now=now) for t in tokens]
    # the full outcome vocabulary is wired, all for posting 'a'
    assert {c.status for c in claims} == set(APPLICATION_STATUSES)
    assert all(c.posting_id == "a" for c in claims)
    # one of them is exactly the {a, applied} link the digest also emits
    assert ("a", "applied") in {(c.posting_id, c.status) for c in claims}


def test_render_full_list_no_mark_column_without_capture():
    # negative: no capture_link → no Mark column, no capture tokens (graceful, unchanged page)
    html = render_full_list([_item(90, "a")], threshold=60, run_date=date(2026, 7, 10))
    assert "<th>Mark</th>" not in html
    assert "?t=" not in html


def test_render_full_list_includes_below_threshold_rows_tagged():
    # the whole point (B-1): below-threshold jobs ARE listed (tagged for the filter), not hidden.
    items = [
        _item(90, "hi"),
        _item(40, "lo", normalized_title="Junior Analyst", company="Gamma"),
    ]
    html = render_full_list(items, threshold=60, run_date=date(2026, 7, 10))
    assert "Junior Analyst" in html and "Gamma" in html  # the below-threshold row is present
    assert 'data-below="1"' in html  # tagged below (the "show below-threshold" filter reads it)
    assert 'data-below="0"' in html  # the surfaced row tagged not-below
    # the header counts honestly: 2 scored, 1 at/above threshold
    assert "2 scored" in html and "1 at or above your threshold of 60" in html


def test_render_full_list_shows_override_and_application_status():
    item = _item(80, "x", score_override=95, application_status="applied")
    html = render_full_list([item], threshold=60, run_date=date(2026, 7, 10))
    assert "95" in html          # the human override
    assert "applied" in html      # the latest application-outcome status


def test_render_full_list_escapes_hostile_user_content():
    # security: a hostile title/company must be escaped, never injected as raw HTML.
    item = _item(80, title="<script>x</script>", company="A & B <b>", normalized_title=None)
    html = render_full_list([item], threshold=60, run_date=date(2026, 7, 10))
    assert "<script>x</script>" not in html  # the raw tag must not survive
    assert "&lt;script&gt;x&lt;/script&gt;" in html
    assert "A &amp; B" in html


def test_render_full_list_rejects_javascript_apply_url():
    # security: a hostile scheme on the untrusted apply_url must NOT become a clickable link.
    item = _item(80, apply_url="javascript:alert(1)")
    html = render_full_list([item], threshold=60, run_date=date(2026, 7, 10))
    assert "javascript:" not in html.lower()
    assert "no link" in html  # the row shows a plain "no link" state instead of an anchor


def test_render_full_list_escapes_apply_url_attribute_breakout():
    # an https url crafted to break out of the href: the scheme passes, but the quote is escaped.
    item = _item(80, apply_url='https://x.com/" onmouseover="alert(1)')
    html = render_full_list([item], threshold=60, run_date=date(2026, 7, 10))
    assert 'onmouseover="alert(1)"' not in html
    assert "&quot;" in html


def test_render_full_list_empty_is_valid_page():
    # VG5 spirit: zero scored jobs → a valid page (never a crash/blank), no table.
    html = render_full_list([], threshold=60, run_date=date(2026, 7, 10), generated_at=_GEN)
    assert html.strip()
    assert html.lower().startswith("<!doctype html>")
    assert "No scored jobs yet" in html
    assert "0 scored" in html


def test_render_full_list_none_fields_do_not_crash():
    # an item with None company/apply_url/status/override + empty strengths renders cleanly.
    item = _item(
        0, "z", company=None, apply_url=None, strategic_assessment=None,
        strengths=[], gaps=[], normalized_title=None, score_override=None,
        application_status=None, scored_at=None, fetched_at=None,
    )
    html = render_full_list([item], threshold=60, run_date=date(2026, 7, 10))
    assert "None" not in html  # no None leaked into the output
    assert "Senior Data Engineer" in html  # raw-title fallback
    assert "no link" in html
