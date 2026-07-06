#!/usr/bin/env python3
"""preview_digest.py — render the daily digest with representative sample data and write it to
`export/digest_preview.html`, so you can SEE the email design in a browser without sending one
(email design is visual). Also writes the plaintext to `export/digest_preview.txt`.

    python scripts/preview_digest.py    # -> export/digest_preview.html (open it in a browser)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobfetcher.core.notifier import render_digest  # noqa: E402
from jobfetcher.core.ports import ShortlistItem  # noqa: E402

# A varied sample: high/borderline scores, a missing apply link, a missing gap, long text — so
# the preview exercises every branch of the card (badge · location · why · gap · Apply / no-link).
_SAMPLE = [
    ShortlistItem(
        posting_id="1", title="Senior Data Engineer", company="Canonical",
        apply_url="https://jobs.example.com/apply/1", normalized_title="Data Platform Engineer",
        score=93, fit_category="strong_fit",
        strengths=["Deep Spark + streaming match", "AWS + Terraform depth"],
        gaps=["No Databricks certification"], city="Riyadh", country="sa",
    ),
    ShortlistItem(
        posting_id="2", title="Data Engineer", company="Qode",
        apply_url="https://jobs.example.com/apply/2", normalized_title="Data Engineer",
        score=88, fit_category="strong_fit",
        strengths=["Strong Python/SQL + dbt"], gaps=[], city="Dubai", country="ae",
    ),
    ShortlistItem(
        posting_id="3", title="Data Architect", company="Alfanar",
        apply_url=None, normalized_title="Data Architect",  # exercises the no-link state
        score=82, fit_category="strong_fit",
        strengths=["Warehouse modeling + CDC experience"],
        gaps=["Enterprise-scale governance"], city="", country="sa",
    ),
]


def main() -> None:
    subject, html, text = render_digest(_SAMPLE, below_count=17, threshold=60, date=date.today())
    out = ROOT / "export"
    out.mkdir(parents=True, exist_ok=True)
    (out / "digest_preview.html").write_text(html, encoding="utf-8")
    (out / "digest_preview.txt").write_text(text, encoding="utf-8")
    print(f"subject: {subject}")
    print(f"wrote {out / 'digest_preview.html'}  (open it in a browser)")
    print(f"wrote {out / 'digest_preview.txt'}")


if __name__ == "__main__":
    main()
