#!/usr/bin/env python3
"""preview_digest.py — render the daily digest with representative sample data and write it to
`export/digest_preview.html`, so you can SEE the email design in a browser without sending one
(email design is visual). Also writes the plaintext to `export/digest_preview.txt`.

The sample exercises every branch of the truthful digest: a genuinely-new match, a
**graduation** (green `↑ 55→72` badge), a **collapsed dup group** (seen 3× — scores 75–88),
the no-apply-link state, and the compact **still-open** section (already-surfaced matches). It
also passes a sample `full_list_url` so the **linked state** (B-1) of the below-threshold footer
and the "…and N more" overflow renders, and writes the full-list report page itself to
`export/full_list_preview.html`.

    python scripts/preview_digest.py    # -> export/digest_preview.html (open it in a browser)
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobfetcher.core.notifier import render_digest  # noqa: E402
from jobfetcher.core.ports import ShortlistItem  # noqa: E402
from jobfetcher.core.report import render_full_list  # noqa: E402

# A sample presigned-style link (https so it passes the notifier's scheme allowlist) — the
# real one is minted per run by S3ReportStore.presign; here it just shows the linked state.
_FULL_LIST_URL = "https://jobfetcher-data.example.com/reports/2026-07-10/jobs-preview.html?sig=demo"

# When the "last digest" went out — the new/still-open split compares each item's scored_at
# to this: a judgment written AFTER it (fresh) can be news; anything older is still-open.
_SINCE = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)
_FRESH = datetime(2026, 7, 6, 9, 0, tzinfo=timezone.utc)  # scored after the last digest
_STALE = datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc)  # scored before the last digest

# A varied sample: a genuinely-new match, a graduation (previous 55 → 72 across threshold 60),
# a same-fingerprint dup trio (collapses to ONE card, seen 3× — scores 75–88), a missing apply
# link, and three still-open matches (scored before the last digest, no fresh news).
_SAMPLE = [
    # --- new since last digest (scored_at after _SINCE) ---
    ShortlistItem(
        posting_id="1", title="Senior Data Engineer", company="Canonical",
        apply_url="https://jobs.example.com/apply/1", normalized_title="Data Platform Engineer",
        score=93, fit_category="strong_fit",
        strengths=["Deep Spark + streaming match", "AWS + Terraform depth"],
        gaps=["No Databricks certification"], city="Riyadh", country="sa",
        scored_at=_FRESH,
    ),
    ShortlistItem(  # a dup trio: one fingerprint, three boards — one card, seen 3×, 75–88
        posting_id="2a", title="Data Engineer", company="Qode",
        apply_url="https://jobs.example.com/apply/2a", normalized_title="Data Engineer",
        score=88, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb", scored_at=_FRESH,
    ),
    ShortlistItem(
        posting_id="2b", title="Data Engineer", company="Qode",
        apply_url="https://jobs.example.com/apply/2b", normalized_title="Data Engineer",
        score=82, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb", scored_at=_FRESH,
    ),
    ShortlistItem(
        posting_id="2c", title="Data Engineer (Platform)", company="Qode",
        apply_url="https://jobs.example.com/apply/2c", normalized_title="Data Engineer",
        score=75, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb", scored_at=_FRESH,
    ),
    ShortlistItem(  # a graduation: 55 → 72 crossed the threshold (60) → green ↑ badge
        posting_id="3", title="Analytics Engineer", company="Tamara",
        apply_url="https://jobs.example.com/apply/3", normalized_title="Analytics Engineer",
        score=72, fit_category="strong_fit",
        strengths=["dbt + warehouse modeling"], gaps=["Looker experience"],
        city="Riyadh", country="sa", previous_score=55, scored_at=_FRESH,
    ),
    ShortlistItem(
        posting_id="4", title="Data Architect", company="Alfanar",
        apply_url=None, normalized_title="Data Architect",  # exercises the no-link state
        score=82, fit_category="strong_fit",
        strengths=["Warehouse modeling + CDC experience"],
        gaps=["Enterprise-scale governance"], city="", country="sa",
        scored_at=_FRESH,
    ),
    # --- still open (scored BEFORE the last digest — the daily-repeat shape) ---
    ShortlistItem(
        posting_id="5", title="Lead Data Engineer", company="stc",
        apply_url="https://jobs.example.com/apply/5", normalized_title="Lead Data Engineer",
        score=85, fit_category="strong_fit", strengths=["AWS platform build"], gaps=[],
        city="Riyadh", country="sa", scored_at=_STALE,
    ),
    ShortlistItem(
        posting_id="6", title="Data Platform Engineer", company="Careem",
        apply_url="https://jobs.example.com/apply/6", normalized_title="Data Platform Engineer",
        score=78, fit_category="strong_fit", strengths=["Kafka + Airflow"], gaps=[],
        city="Dubai", country="ae", previous_score=80, scored_at=_STALE,
    ),
    ShortlistItem(
        posting_id="7", title="BI / Data Engineer", company="Noon",
        apply_url="https://jobs.example.com/apply/7", normalized_title="Data Engineer",
        score=66, fit_category="strong_fit", strengths=["SQL + ETL"], gaps=["Retail domain"],
        city="Riyadh", country="sa", previous_score=64, scored_at=_STALE,
    ),
]


def main() -> None:
    # Score-DESC like the Repository guarantees (ORDER BY score DESC) — the renderer's
    # grouping/ordering assumes that input invariant, so the preview honors it too.
    items = sorted(_SAMPLE, key=lambda i: i.score, reverse=True)
    subject, html, text = render_digest(
        items, below_count=17, threshold=60, date=date.today(), since=_SINCE,
        full_list_url=_FULL_LIST_URL,
    )
    # The full-list report page itself: reuse the same sample + a few synthetic below-threshold
    # rows so the "show below-threshold" filter has something to hide/show.
    below_sample = [
        ShortlistItem(
            posting_id=f"below-{i}", title=f"Junior Role {i}", company=f"SmallCo {i}",
            apply_url=f"https://jobs.example.com/apply/below-{i}",
            normalized_title="Data Analyst", score=score, fit_category="near_miss",
            strengths=["SQL basics"], gaps=["needs more platform depth"],
            city="Riyadh", country="sa", scored_at=_FRESH,
        )
        for i, score in enumerate((55, 48, 40))
    ]
    full_list_html = render_full_list(
        sorted(items + below_sample, key=lambda i: i.score, reverse=True),
        threshold=60, run_date=date.today(), generated_at=datetime.now(timezone.utc),
    )
    out = ROOT / "export"
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest_preview.html").write_text(html, encoding="utf-8")
    (out / "digest_preview.txt").write_text(text, encoding="utf-8")
    (out / "full_list_preview.html").write_text(full_list_html, encoding="utf-8")
    print(f"subject: {subject}")
    print(f"wrote {out / 'digest_preview.html'}  (open it in a browser)")
    print(f"wrote {out / 'digest_preview.txt'}")
    print(f"wrote {out / 'full_list_preview.html'}  (the full-list report page)")


if __name__ == "__main__":
    main()
