"""Tests for `scripts/check_ingestion.py` — the 2026-09-22 "did ingestion resume?" gate.

The script's whole value is that it does NOT cry wolf: a zero-fetch run before the monthly
quota reset, and a `not_a_fetch_day` skip, are both CORRECT behaviour. A check that reported
either as a failure would be ignored within a week (the B-5 lesson), so the negatives here
matter more than the positive. Each case is behavioral and pairs with its opposite.
"""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_ingestion as ci  # noqa: E402


def _summary(run_date: str, **ingest):
    base = {"fetched": 0, "bronzed": 0, "fetch_stopped": None, "fetch_failed_queries": 0}
    return {"statusCode": 200, "run_date": run_date, "ingest": {**base, **ingest}}


# --------------------------------------------------------------- last_quota_reset (pure)
@pytest.mark.parametrize(
    ("today", "expected"),
    [
        ("2026-09-22", "2026-09-22"),  # on the reset day itself
        ("2026-09-25", "2026-09-22"),  # after it
        ("2026-09-05", "2026-08-22"),  # before it -> last month's
        ("2026-01-03", "2025-12-22"),  # year boundary
        ("2026-03-01", "2026-02-22"),  # short month
    ],
)
def test_last_quota_reset(today, expected):
    assert ci.last_quota_reset(date.fromisoformat(today)).isoformat() == expected


# --------------------------------------------------------------- verdict (pure, the judgment)
def test_a_full_sweep_that_landed_postings_is_a_pass():
    level, msg = ci.verdict(
        _summary("2026-09-22", fetched=140, fetch_stopped=None), since=date(2026, 9, 22)
    )
    assert level == ci.PASS
    assert "140" in msg


def test_rate_limited_in_a_LEGACY_cycle_is_expected_not_a_failure():
    # TRAP 1, and the most important test here — it caught a real design error before the
    # script ever ran. The first draft failed any rate-limit after the last reset, which
    # would have FAILED on every ordinary day between exhaustion and rollover. Running out
    # mid-cycle is what a quota IS; only a cycle sized to fit tests the fix.
    level, msg = ci.verdict(
        _summary("2026-09-05", fetch_stopped=ci.STOP_RATE_LIMITED), since=date(2026, 9, 22)
    )
    assert level == ci.EXPECTED
    assert "NOT a regression" in msg


def test_rate_limited_AFTER_the_reset_is_a_hard_failure():
    # The opposite half: the quota should have rolled over, so this means the capacity
    # arithmetic is still wrong. This is the single condition that re-opens ERR-017.
    level, msg = ci.verdict(
        _summary("2026-09-25", fetch_stopped=ci.STOP_RATE_LIMITED), since=date(2026, 9, 22)
    )
    assert level == ci.FAIL
    assert "RE-OPEN ERR-017" in msg
    assert "do NOT probe" in msg  # never spend a request to ask about requests


def test_not_a_fetch_day_is_the_design_working():
    # TRAP 2: 2 days in 3 look like this on purpose.
    level, msg = ci.verdict(
        _summary("2026-09-05", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY), since=date(2026, 9, 22)
    )
    assert level == ci.EXPECTED
    assert "by design" in msg


def test_partial_errors_warns_and_surfaces_the_count():
    level, msg = ci.verdict(
        _summary("2026-09-22", fetched=12, fetch_stopped=ci.STOP_PARTIAL_ERRORS,
                 fetch_failed_queries=4),
        since=date(2026, 9, 22),
    )
    assert level == ci.WARN
    assert "4 query" in msg and "FLOOR" in msg


def test_budget_exhausted_warns_about_our_own_cap_not_the_providers():
    level, msg = ci.verdict(
        _summary("2026-09-22", fetch_stopped=ci.STOP_BUDGET_EXHAUSTED), since=date(2026, 9, 22)
    )
    assert level == ci.WARN
    assert "OWN request budget" in msg


def test_a_completed_sweep_that_found_nothing_warns_rather_than_passing():
    # `fetch_stopped: None` with `fetched: 0` is honest — the matrix WAS searched — but it is
    # not a pass either, and calling it one would hide a too-narrow targeting config.
    level, _ = ci.verdict(_summary("2026-09-22", fetched=0, fetch_stopped=None),
                          since=date(2026, 9, 22))
    assert level == ci.WARN


def test_a_pre_ERR017_summary_is_reported_as_unknown_not_crashed():
    # negative: a run written before PR #63 has no `fetch_stopped` at all. That IS the ERR-017
    # blind spot, and the script must say so rather than KeyError or silently pass.
    old = {"statusCode": 200, "run_date": "2026-09-04", "ingest": {"fetched": 0, "bronzed": 0}}
    level, msg = ci.verdict(old, since=date(2026, 9, 22))
    assert level == ci.UNKNOWN
    assert "older than PR #63" in msg


# --------------------------------------------------------------- latest_raw_date (pure)
def test_latest_raw_date_picks_the_newest_and_tolerates_junk_keys():
    keys = [
        "raw/jsearch/2026-08-30/a.json",
        "raw/jsearch/2026-09-01/b.json",
        "raw/jsearch/2026-08-31/c.json",
        "raw/_manifest",  # no date -> ignored, not a crash
    ]
    assert ci.latest_raw_date(keys) == "2026-09-01"
    assert ci.latest_raw_date([]) is None  # negative: empty bucket


# --------------------------------------------------------------- end to end, fake S3
class _FakeS3:
    """Minimal stand-in for the boto3 client (the injectable-client pattern from s3_raw.py)."""

    def __init__(self, raw_keys, summaries):
        self._raw = raw_keys
        self._summaries = summaries  # {key: dict}

    def list_objects_v2(self, **kw):
        prefix = kw["Prefix"]
        src = self._raw if prefix == "raw/" else list(self._summaries)
        return {"Contents": [{"Key": k} for k in src], "IsTruncated": False}

    def get_object(self, **kw):
        return {"Body": io.BytesIO(json.dumps(self._summaries[kw["Key"]]).encode())}


def test_main_exits_zero_when_nothing_is_failing(monkeypatch, capsys):
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-22/a.json"],
        {"runs/2026-09-22/r1.json": _summary("2026-09-22", fetched=140)},
    )
    assert ci.main(["--today", "2026-09-22"], client=fake) == 0
    out = capsys.readouterr().out
    assert "[PASS]" in out and "OK (no failing condition found)" in out


def test_main_exits_one_on_a_post_reset_rate_limit(monkeypatch, capsys):
    # the gate's teeth: a real regression must set a non-zero exit, not just print a word
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-01/a.json"],
        {"runs/2026-09-25/r1.json": _summary("2026-09-25",
                                             fetch_stopped=ci.STOP_RATE_LIMITED)},
    )
    assert ci.main(["--today", "2026-09-25"], client=fake) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_main_does_not_fail_the_run_before_the_reset(monkeypatch, capsys):
    # negative pair to the above, on the SAME data shape: identical summary, earlier date ->
    # exit 0. If this ever flips, the script has become the thing it was written to prevent.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-01/a.json"],
        {"runs/2026-09-05/r1.json": _summary("2026-09-05",
                                             fetch_stopped=ci.STOP_RATE_LIMITED)},
    )
    assert ci.main(["--today", "2026-09-05"], client=fake) == 0
    assert "[EXPECTED]" in capsys.readouterr().out


def test_before_the_test_cycle_starts_the_report_says_too_early_not_nothing_landed(
    monkeypatch, capsys
):
    # negative: `since` can be in the FUTURE. "Nothing has landed since <a future date>" reads
    # as a fault when it is only the calendar — and a check that sounds alarmed on a normal day
    # is the exact thing this script exists not to be.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-01/a.json"],
        {"runs/2026-09-05/r1.json": _summary("2026-09-05",
                                             fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)},
    )
    assert ci.main(["--today", "2026-09-05"], client=fake) == 0
    out = capsys.readouterr().out
    assert "too early to judge" in out
    assert "NOTHING has landed" not in out


def test_after_the_test_cycle_starts_a_stale_raw_prefix_says_so_plainly(monkeypatch, capsys):
    # the pair: once the cycle IS under way, silence is worth naming.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-01/a.json"],
        {"runs/2026-09-25/r1.json": _summary("2026-09-25",
                                             fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)},
    )
    ci.main(["--today", "2026-09-25"], client=fake)
    assert "NOTHING has landed since 2026-09-22" in capsys.readouterr().out
