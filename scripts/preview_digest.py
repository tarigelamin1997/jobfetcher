#!/usr/bin/env python3
"""preview_digest.py — render the daily digest with representative sample data and write it to
`export/digest_preview.html`, so you can SEE the email design in a browser without sending one
(email design is visual). Also writes the plaintext to `export/digest_preview.txt`.

The sample exercises every branch of the truthful digest: a genuinely-new match, a
**graduation** (green `↑ 55→72` badge), a **collapsed dup group** (seen 3× — scores 75–88),
the no-apply-link state, and the compact **still-open** section (already-surfaced matches).

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

# When the "last digest" went out — makes the new/still-open split (and its section headers)
# render; the split itself rides each item's previous_score.
_SINCE = datetime(2026, 7, 1, 6, 0, tzinfo=timezone.utc)

# A varied sample: a genuinely-new match, a graduation (previous 55 → 72 across threshold 60),
# a same-fingerprint dup trio (collapses to ONE card, seen 3× — scores 75–88), a missing apply
# link, and three still-open matches (previous_score already >= threshold).
_SAMPLE = [
    # --- new since last digest ---
    ShortlistItem(
        posting_id="1", title="Senior Data Engineer", company="Canonical",
        apply_url="https://jobs.example.com/apply/1", normalized_title="Data Platform Engineer",
        score=93, fit_category="strong_fit",
        strengths=["Deep Spark + streaming match", "AWS + Terraform depth"],
        gaps=["No Databricks certification"], city="Riyadh", country="sa",
    ),
    ShortlistItem(  # a dup trio: one fingerprint, three boards — one card, seen 3×, 75–88
        posting_id="2a", title="Data Engineer", company="Qode",
        apply_url="https://jobs.example.com/apply/2a", normalized_title="Data Engineer",
        score=88, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb",
    ),
    ShortlistItem(
        posting_id="2b", title="Data Engineer", company="Qode",
        apply_url="https://jobs.example.com/apply/2b", normalized_title="Data Engineer",
        score=82, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb",
    ),
    ShortlistItem(
        posting_id="2c", title="Data Engineer (Platform)", company="Qode",
        apply_url="https://jobs.example.com/apply/2c", normalized_title="Data Engineer",
        score=75, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
        fingerprint="fp-qode-de-dxb",
    ),
    ShortlistItem(  # a graduation: 55 → 72 crossed the threshold (60) → green ↑ badge
        posting_id="3", title="Analytics Engineer", company="Tamara",
        apply_url="https://jobs.example.com/apply/3", normalized_title="Analytics Engineer",
        score=72, fit_category="strong_fit",
        strengths=["dbt + warehouse modeling"], gaps=["Looker experience"],
        city="Riyadh", country="sa", previous_score=55,
    ),
    ShortlistItem(
        posting_id="4", title="Data Architect", company="Alfanar",
        apply_url=None, normalized_title="Data Architect",  # exercises the no-link state
        score=82, fit_category="strong_fit",
        strengths=["Warehouse modeling + CDC experience"],
        gaps=["Enterprise-scale governance"], city="", country="sa",
    ),
    # --- still open (previous_score already >= threshold → surfaced before) ---
    ShortlistItem(
        posting_id="5", title="Lead Data Engineer", company="stc",
        apply_url="https://jobs.example.com/apply/5", normalized_title="Lead Data Engineer",
        score=85, fit_category="strong_fit", strengths=["AWS platform build"], gaps=[],
        city="Riyadh", country="sa", previous_score=85,
    ),
    ShortlistItem(
        posting_id="6", title="Data Platform Engineer", company="Careem",
        apply_url="https://jobs.example.com/apply/6", normalized_title="Data Platform Engineer",
        score=78, fit_category="strong_fit", strengths=["Kafka + Airflow"], gaps=[],
        city="Dubai", country="ae", previous_score=80,
    ),
    ShortlistItem(
        posting_id="7", title="BI / Data Engineer", company="Noon",
        apply_url="https://jobs.example.com/apply/7", normalized_title="Data Engineer",
        score=66, fit_category="strong_fit", strengths=["SQL + ETL"], gaps=["Retail domain"],
        city="Riyadh", country="sa", previous_score=64,
    ),
]


def main() -> None:
    subject, html, text = render_digest(
        _SAMPLE, below_count=17, threshold=60, date=date.today(), since=_SINCE
    )
    out = ROOT / "export"
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest_preview.html").write_text(html, encoding="utf-8")
    (out / "digest_preview.txt").write_text(text, encoding="utf-8")
    print(f"subject: {subject}")
    print(f"wrote {out / 'digest_preview.html'}  (open it in a browser)")
    print(f"wrote {out / 'digest_preview.txt'}")


if __name__ == "__main__":
    main()
