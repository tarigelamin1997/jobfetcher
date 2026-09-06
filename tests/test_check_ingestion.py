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
    assert "predates PR #63" in msg


# --------------------------------------------------------------- latest_raw_date (pure)
def test_latest_raw_date_picks_the_newest_and_tolerates_junk_keys():
    keys = [
        "raw/jsearch/2026-08-30/a.json",
        "raw/jsearch/2026-09-01/b.json",
        "raw/jsearch/2026-08-31/c.json",
        "raw/_manifest",  # no date -> ignored, not a crash
    ]
    assert ci.latest_raw_date(keys) == date(2026, 9, 1)  # a real date, not a string
    assert ci.latest_raw_date([]) is None  # negative: empty bucket
    # negative (CodeRabbit): date-SHAPED is not date-VALID. An S3 key is untrusted input, and
    # `2026-13-45` matched the old regex and then exploded in `date.fromisoformat` — a traceback
    # instead of an exit code. Junk is skipped; a real date beside it still wins.
    assert ci.latest_raw_date(["raw/jsearch/2026-13-45/x.json"]) is None
    assert ci.latest_raw_date(
        ["raw/jsearch/2026-13-45/x.json", "raw/jsearch/2026-09-01/b.json"]
    ) == date(2026, 9, 1)
    assert ci.latest_raw_date(["raw/jsearch/2026-02-30/x.json"]) is None  # not a real day


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


# ===================== the re-verification round: what a fresh Examiner found =====================
# The first version refused to cry wolf so thoroughly that it could not bark. These are its
# blind spots, each with the test that would have caught it.


def _crashed(run_date: str, error: str = "OperationalError: could not connect"):
    """Exactly the shape `handlers/pipeline.py` writes on a stage failure — statusCode 500 and
    NO `ingest` key, to the same runs/ prefix a healthy run uses."""
    return {"statusCode": 500, "run_id": "deadbeef", "run_date": run_date, "error": error}


def test_a_crashed_run_is_a_FAIL_not_an_unknown_old_build():
    # THE BLOCKER. A 500 summary has no `ingest` key, so the pre-#63 branch swallowed it: a
    # pipeline dying every morning reported OK, exit 0, and told the operator to "check a run
    # from a newer build" — when the newer build was precisely what was failing. That is the
    # ERR-010 shape (38 unnoticed returned-500s) reproduced inside the tool built to catch it.
    level, msg = ci.verdict(_crashed("2026-09-25"), since=date(2026, 9, 22))
    assert level == ci.FAIL
    assert "statusCode 500" in msg
    assert "predates PR #63" not in msg  # the misdiagnosis must be gone
    # The ERR-010 framing deliberately does NOT live here — one 500 is a fact, a streak is the
    # pattern. See `failure_streak` and its test; a per-run message that shouts "38 days!" at a
    # single Aurora resume is a check people learn to discount.


def test_main_exits_one_when_every_run_crashed(monkeypatch, capsys):
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3([], {f"runs/2026-09-2{d}/r{d}.json": _crashed(f"2026-09-2{d}")
                        for d in range(3, 8)})
    assert ci.main(["--today", "2026-09-28"], client=fake) == ci.FAIL_EXIT
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "OK (no failing" not in out


def test_a_reassess_run_has_no_ingest_and_is_not_an_error():
    # negative pair: `mode=reassess` legitimately has no ingest stage. It must not be judged as
    # a broken shape, or every manual replay would light up the report.
    level, _ = ci.verdict(
        {"statusCode": 200, "run_date": "2026-09-25", "mode": "reassess", "reassess": {}},
        since=date(2026, 9, 22),
    )
    assert level == ci.EXPECTED


def test_an_impossible_run_of_skips_is_a_FAIL_even_though_each_line_is_expected():
    # A cadence of 3 can put at most 2 skips between sweeps. Seven in a row means the sweep is
    # not paused, it is dead — the $JOBFETCHER_FETCH_EVERY_N_DAYS failure mode. Every individual
    # line stays correctly EXPECTED; the whole picture is what is wrong.
    runs = [_summary(f"2026-09-{d:02d}", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)
            for d in range(20, 27)]
    msg = ci.cadence_anomaly(runs, every_n_days=3)
    assert msg is not None and "7 CONSECUTIVE" in msg
    assert "JOBFETCHER_FETCH_EVERY_N_DAYS" in msg


def test_a_normal_cadence_is_not_flagged_as_an_anomaly():
    # negative: skip, skip, sweep — the ordinary pattern must stay silent, or the check becomes
    # the furniture it was written to avoid.
    runs = [
        _summary("2026-09-20", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),
        _summary("2026-09-21", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),
        _summary("2026-09-22", fetched=140),
        _summary("2026-09-23", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),
    ]
    assert ci.cadence_anomaly(runs, every_n_days=3) is None


def test_unreadable_summaries_do_not_report_OK(monkeypatch, capsys):
    # The two were backwards: an EMPTY runs/ prefix exited 1 while "every object failed to read"
    # exited 0. "I could not read anything" is the louder failure.
    class _Unreadable(_FakeS3):
        def get_object(self, **kw):
            raise RuntimeError("AccessDenied")

    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _Unreadable(["raw/jsearch/2026-09-25/a.json"], {"runs/2026-09-25/r1.json": {}})
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.CANNOT_JUDGE_EXIT
    assert "CANNOT JUDGE" in capsys.readouterr().out


def test_an_empty_runs_prefix_cannot_judge_rather_than_failing(monkeypatch, capsys):
    # ...and its pair: nothing to read is "cannot judge" (exit 2), NOT "ERR-017 re-opened"
    # (exit 1). A wrapper checking $? must be able to tell a wrong bucket from a real defect.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    assert ci.main(["--today", "2026-09-25"], client=_FakeS3([], {})) == ci.CANNOT_JUDGE_EXIT


def test_an_unusable_run_date_is_unknown_not_a_failure():
    # negative: the date comparison is a string compare. Missing/unpadded/non-string dates must
    # land on UNKNOWN — guessing toward FAIL is the crying-wolf direction.
    for bad in ({"statusCode": 200}, {"run_date": 20260905}, {"run_date": "2026-9-05"}):
        level, _ = ci.verdict({"statusCode": 200, "ingest": {"fetch_stopped": None}, **bad},
                              since=date(2026, 9, 22))
        assert level == ci.UNKNOWN, bad


def test_a_non_object_summary_body_does_not_crash():
    # The exact bug class PR #70 fixed in the JSearch adapter, guarded here rather than
    # re-learned: a body that parses as JSON but is not an object.
    for body in ([1, 2, 3], "oops", 42, None):
        level, _ = ci.verdict(body, since=date(2026, 9, 22))
        assert level == ci.UNKNOWN


def test_list_keys_terminates_when_truncated_without_a_token():
    # negative: `IsTruncated` with no NextContinuationToken would re-request page 1 forever —
    # an unbounded BILLED loop, not just a hang.
    class _Broken:
        calls = 0

        def list_objects_v2(self, **kw):
            self.calls += 1
            assert self.calls < 50, "infinite pagination loop"
            return {"Contents": [{"Key": "runs/2026-09-25/a.json"}], "IsTruncated": True}

    assert ci._list_keys(_Broken(), "b", "runs/") == ["runs/2026-09-25/a.json"]


def test_days_must_be_at_least_one(monkeypatch):
    # `[-0:]` is the WHOLE list, so `--days 0` silently judged everything.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    with pytest.raises(SystemExit) as e:
        ci.main(["--days", "0"], client=_FakeS3([], {}))
    assert e.value.code == 2  # argparse usage error, not a silent surprise


def test_a_junk_date_argument_is_an_argparse_error_not_a_traceback(monkeypatch):
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    with pytest.raises(SystemExit) as e:
        ci.main(["--today", "notadate"], client=_FakeS3([], {}))
    assert e.value.code == 2


def test_every_run_of_a_day_is_judged_not_just_the_last_key(monkeypatch, capsys):
    # Keys are runs/{date}/{run_id}.json and run_id is a random uuid hex, so ordering by key
    # within a day is arbitrary: a retry could hide the failing run behind a passing one from
    # the same morning. Selection is by DATE, and every run in it is judged.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3([], {
        "runs/2026-09-25/a1b2c3d4.json": _summary("2026-09-25",
                                                  fetch_stopped=ci.STOP_RATE_LIMITED),
        "runs/2026-09-25/f9e8d7c6.json": _summary("2026-09-25", fetched=3),
    })
    assert ci.main(["--today", "2026-09-25", "--days", "1"], client=fake) == ci.FAIL_EXIT
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "[PASS]" in out  # both runs judged, not one


def test_one_failed_run_is_a_fact_and_a_streak_is_the_ERR010_pattern():
    # One 500 is a transient (an Aurora resume, say) — real, worth flagging, not a pattern.
    # CONSECUTIVE days of them is ERR-010, which ran 38 days precisely because each morning
    # looked like the last. The per-run message stays proportionate; the escalation is here.
    one = [_crashed("2026-09-25")]
    assert ci.failure_streak(one) is None

    streak = [_crashed(f"2026-09-2{d}") for d in (3, 4, 5)]
    msg = ci.failure_streak(streak)
    assert msg is not None and "3 CONSECUTIVE DAYS" in msg and "ERR-010" in msg


def test_a_single_failure_message_does_not_overclaim():
    # negative: the first version asserted "This is the ERR-010 shape" on every single 500,
    # including a four-day-old Aurora resume. A check that dramatises is a check people discount.
    _, msg = ci.verdict(_crashed("2026-09-25"), since=date(2026, 9, 22))
    assert "ERR-010" not in msg
    assert "statusCode 500" in msg


def test_non_consecutive_failures_are_not_called_a_streak():
    # negative pair: failures on the 23rd and the 25th, healthy on the 24th -> no pattern.
    mixed = [_crashed("2026-09-23"), _summary("2026-09-24", fetched=5), _crashed("2026-09-25")]
    assert ci.failure_streak(mixed) is None


# ---------------- CodeRabbit on PR #74: sparse dates are not consecutive days ----------------


def test_a_gap_in_the_dates_resets_the_streak_instead_of_faking_one():
    # CodeRabbit. Both streak checks walked sorted date STRINGS and incremented on each match
    # without asking whether the days touch — so 09-01, 09-10 and 09-20 counted as "3
    # consecutive", and a MISSING run date (an S3 object that never landed, a --days window with
    # a hole) manufactured a FAIL out of nothing. That is the crying-wolf direction.
    sparse = [_summary(d, fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)
              for d in ("2026-09-01", "2026-09-10", "2026-09-20")]
    assert ci.cadence_anomaly(sparse, every_n_days=3) is None

    sparse_fail = [_crashed("2026-09-01"), _crashed("2026-09-10")]
    assert ci.failure_streak(sparse_fail) is None


def test_genuinely_adjacent_days_still_trip_both_streaks():
    # the pair: adjacency is required, not sufficient-by-accident. Contiguous dates still fire.
    runs = [_summary(f"2026-09-{d:02d}", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)
            for d in (10, 11, 12)]
    assert ci.cadence_anomaly(runs, every_n_days=3) is not None
    assert ci.failure_streak([_crashed("2026-09-11"), _crashed("2026-09-12")]) is not None


def test_one_missing_day_breaks_an_otherwise_long_run():
    # 09-10, 09-11, [09-12 missing], 09-13, 09-14 -> longest adjacent run is 2, under the
    # cadence of 3. A hole in the data must not be read as continuity.
    runs = [_summary(f"2026-09-{d:02d}", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)
            for d in (10, 11, 13, 14)]
    assert ci.cadence_anomaly(runs, every_n_days=3) is None


@pytest.mark.parametrize("sweep_first", [False, True])
def test_a_day_with_any_sweep_is_not_a_skip_day(sweep_first):
    # negative for the per-day fold: several runs share a date (a retry). If ANY of them swept,
    # that day is not a skip day, so it must break the streak.
    #
    # BOTH ORDERS, and that is the whole point. The single-order version of this test was
    # TAUTOLOGICAL with respect to the property it claimed: a last-write-wins fold
    # (`by_day[day] = skipped`) passed it 41/41, and survived only because the skip happened to
    # be listed before the sweep. In production the order is arbitrary — `main()` sorts
    # `runs/{date}/{run_id}.json` keys and `run_id` is a random uuid hex — so that mutant would
    # have produced a COIN-FLIP false FAIL in the field with a fully green suite.
    same_day = [
        _summary("2026-09-12", fetched=140),                            # a re-trigger that swept
        _summary("2026-09-12", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),  # and the scheduled skip
    ]
    if not sweep_first:
        same_day.reverse()
    runs = [
        _summary("2026-09-10", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),
        _summary("2026-09-11", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY),
        *same_day,
    ]
    assert ci.cadence_anomaly(runs, every_n_days=3) is None


@pytest.mark.parametrize("crash_first", [False, True])
def test_a_day_with_any_crash_is_a_failure_day(crash_first):
    # The mirror fold, with the opposite operator and the same order-independence requirement:
    # a crash at 06:00 plus a successful manual retry at 07:00 still means the SCHEDULED run
    # failed that day. `failure_streak` therefore folds with OR where `cadence_anomaly` folds
    # with AND — the asymmetry is deliberate.
    day_11 = [_crashed("2026-09-11"), _summary("2026-09-11", fetched=9)]
    day_12 = [_crashed("2026-09-12"), _summary("2026-09-12", fetched=9)]
    if not crash_first:
        day_11.reverse()
        day_12.reverse()
    msg = ci.failure_streak([*day_11, *day_12])
    assert msg is not None and "2 CONSECUTIVE DAYS" in msg


def test_one_unreadable_summary_alongside_a_readable_one_cannot_judge(monkeypatch, capsys):
    # CodeRabbit. The guard was `unreadable and not summaries`, so a single readable sibling was
    # enough to print OK and exit 0 over an inaccessible object — and the failing run may be
    # exactly the one we could not open. "I did not look" must never render as "nothing wrong".
    class _HalfBroken(_FakeS3):
        def get_object(self, **kw):
            if kw["Key"].endswith("bad.json"):
                raise RuntimeError("AccessDenied")
            return super().get_object(**kw)

    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _HalfBroken(["raw/jsearch/2026-09-25/a.json"], {
        "runs/2026-09-25/good.json": _summary("2026-09-25", fetched=5),
        "runs/2026-09-25/bad.json": _summary("2026-09-25", fetched=0),
    })
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.CANNOT_JUDGE_EXIT
    assert "CANNOT JUDGE" in capsys.readouterr().out


def test_a_confirmed_failure_still_outranks_an_unreadable_object(monkeypatch, capsys):
    # ...and its pair: a KNOWN defect is more actionable than an unknown one, so FAIL wins.
    class _HalfBroken(_FakeS3):
        def get_object(self, **kw):
            if kw["Key"].endswith("bad.json"):
                raise RuntimeError("AccessDenied")
            return super().get_object(**kw)

    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _HalfBroken([], {
        "runs/2026-09-25/crash.json": _crashed("2026-09-25"),
        "runs/2026-09-25/bad.json": _summary("2026-09-25"),
    })
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.FAIL_EXIT


# ------------- the delta review: the headline question must reach the exit code -------------


def test_nothing_landed_in_the_cycle_under_test_is_a_FAIL_not_just_prose(monkeypatch, capsys):
    # THE HEADLINE QUESTION. `raw/` staleness used to be prose only: the report could print
    # "42 days ago - NOTHING has landed" and still exit 0, because nothing about it fed the
    # verdict. A green $? beside an alarming paragraph is the ERR-017 shape exactly — a run
    # that reports success while doing nothing.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-08-14/a.json"],  # nothing since well before the cycle began
        {"runs/2026-09-25/r1.json": _summary("2026-09-25", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)},
    )
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.FAIL_EXIT
    assert "THIS IS THE FAILURE" in capsys.readouterr().out


def test_an_empty_raw_prefix_after_the_cycle_starts_is_also_a_FAIL(monkeypatch):
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3([], {"runs/2026-09-25/r1.json": _summary("2026-09-25", fetched=0)})
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.FAIL_EXIT


def test_the_same_staleness_before_the_cycle_starts_is_NOT_a_failure(monkeypatch, capsys):
    # negative pair, identical data, earlier date: before the cycle under test there is nothing
    # to conclude, so this must stay exit 0. If it ever flips, the check has become furniture.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-08-14/a.json"],
        {"runs/2026-09-05/r1.json": _summary("2026-09-05", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)},
    )
    assert ci.main(["--today", "2026-09-05"], client=fake) == ci.OK_EXIT
    assert "too early to judge" in capsys.readouterr().out


def test_a_window_with_holes_warns_that_the_streak_detectors_are_weakened(monkeypatch, capsys):
    # The blind spot the adjacency fix leaves: a dead sweep whose summaries are periodically
    # missing escapes the streak detectors. The trade-off is right, but a detector that can be
    # defused silently is one nobody should trust twice — so the report says the window has holes.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-25/a.json"],
        {f"runs/2026-09-{d}/r.json": _summary(f"2026-09-{d}",
                                              fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)
         for d in ("20", "22", "25")},  # 6-day span, 3 summaries -> 3 holes
    )
    ci.main(["--today", "2026-09-25", "--days", "5"], client=fake)
    out = capsys.readouterr().out
    assert "3 of the last 6 calendar days have NO run summary" in out
    assert "weakened" in out


def test_a_contiguous_window_does_not_warn_about_holes(monkeypatch, capsys):
    # negative: an unbroken window must stay quiet, or the warning becomes furniture.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2026-09-25/a.json"],
        {f"runs/2026-09-2{d}/r.json": _summary(f"2026-09-2{d}", fetched=5) for d in (3, 4, 5)},
    )
    ci.main(["--today", "2026-09-25", "--days", "3"], client=fake)
    assert "NO run summary" not in capsys.readouterr().out


def test_a_stray_non_json_object_does_not_pin_the_exit_code_at_cannot_judge(monkeypatch):
    # An S3-console folder placeholder / .keep / truncated write under runs/{date}/ is
    # unreadable, and would otherwise force exit 2 forever even though every real summary was
    # readable and passing. Only .json keys are summaries.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")

    class _WithPlaceholder(_FakeS3):
        def list_objects_v2(self, **kw):
            page = super().list_objects_v2(**kw)
            if kw["Prefix"] == "runs/":
                page["Contents"].append({"Key": "runs/2026-09-25/"})  # not a summary
            return page

    fake = _WithPlaceholder(["raw/jsearch/2026-09-25/a.json"],
                            {"runs/2026-09-25/r1.json": _summary("2026-09-25", fetched=5)})
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.OK_EXIT


def test_one_date_validator_so_a_nonsense_date_is_invisible_to_nobody():
    # Two validators disagreed: `verdict` only regex-checked the shape, so "2026-13-45" was
    # judged there but skipped by the streak detectors. Now both use `_run_day`.
    nonsense = {"statusCode": 500, "run_date": "2026-13-45"}
    level, _ = ci.verdict(nonsense, since=date(2026, 9, 22))
    assert level == ci.UNKNOWN                    # not FAIL — an unusable date is not evidence
    assert ci._run_day(nonsense) is None
    assert ci.failure_streak([nonsense, nonsense]) is None


# --------- CodeRabbit round 2: unjudged is not OK, and key dates are untrusted input ---------


def test_an_unknown_verdict_cannot_judge_rather_than_reporting_OK(monkeypatch, capsys):
    # An UNREADABLE object and an UNJUDGEABLE one are the same fact in different clothes: a run
    # in the window whose state we do not know. Only the first affected the exit code, so a
    # pre-#63 summary printed UNKNOWN and then exited 0 — "I could not judge this" rendering as
    # "nothing wrong", which is the blocker this script was rewritten for, one level down.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    old_build = {"statusCode": 200, "run_date": "2026-09-25",
                 "ingest": {"fetched": 0, "bronzed": 0}}  # no fetch_stopped key
    fake = _FakeS3(["raw/jsearch/2026-09-25/a.json"], {
        "runs/2026-09-25/good.json": _summary("2026-09-25", fetched=5),
        "runs/2026-09-25/old.json": old_build,
    })
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.CANNOT_JUDGE_EXIT
    out = capsys.readouterr().out
    assert "[UNKNOWN]" in out and "CANNOT JUDGE" in out


def test_all_judgeable_runs_still_exit_zero(monkeypatch):
    # negative pair: the moment every run is judgeable, exit 0 returns. Without this the change
    # above would just be a permanently-red check, which is the same uselessness inverted.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(["raw/jsearch/2026-09-25/a.json"],
                   {"runs/2026-09-25/good.json": _summary("2026-09-25", fetched=5)})
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.OK_EXIT


def test_a_confirmed_failure_outranks_an_unjudgeable_run(monkeypatch):
    # ordering: a KNOWN defect is more actionable than an unknown one, so FAIL still wins.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(["raw/jsearch/2026-09-25/a.json"], {
        "runs/2026-09-25/crash.json": _crashed("2026-09-25"),
        "runs/2026-09-25/old.json": {"statusCode": 200, "run_date": "2026-09-25",
                                     "ingest": {"fetched": 0}},
    })
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.FAIL_EXIT


def test_a_junk_date_in_a_runs_prefix_does_not_crash_the_report(monkeypatch, capsys):
    # negative (CodeRabbit): `runs/2026-13-45/` is date-shaped junk. The old prefix regex fed it
    # straight to date.fromisoformat -> ValueError traceback instead of a clean exit code.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(["raw/jsearch/2026-09-25/a.json"], {
        "runs/2026-13-45/junk.json": _summary("2026-09-25", fetched=5),
        "runs/2026-09-25/good.json": _summary("2026-09-25", fetched=5),
    })
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.OK_EXIT  # junk prefix skipped
    assert "Traceback" not in capsys.readouterr().out


def test_dates_in_keys_parses_rather_than_pattern_matching():
    assert ci.dates_in_keys(["a/2026-09-01/x", "a/2026-13-45/x", "a/2026-02-30/x"]) == [
        date(2026, 9, 1)
    ]
    assert ci.dates_in_keys([]) == []
    # sorted + deduped, so callers can take [-1] for "newest"
    assert ci.dates_in_keys(["a/2026-09-03/x", "a/2026-09-01/y", "a/2026-09-03/z"]) == [
        date(2026, 9, 1), date(2026, 9, 3)
    ]


# ------- CodeRabbit round 3: a date after the report's cutoff is not evidence for it -------


def test_a_future_raw_key_does_not_satisfy_the_landing_check(monkeypatch, capsys):
    # A future-dated `raw/` key made "has anything landed in the cycle under test?" true, so
    # the command returned OK about a window holding no data at all. Reachable through the
    # documented --today flag alone: pin the cutoff to a past date and later real keys are
    # "future". Also why an age could print negative.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(
        ["raw/jsearch/2027-01-01/future.json"],  # after the cutoff -> not evidence
        {"runs/2026-09-25/r1.json": _summary("2026-09-25", fetch_stopped=ci.SKIP_NOT_A_FETCH_DAY)},
    )
    assert ci.main(["--today", "2026-09-25"], client=fake) == ci.FAIL_EXIT
    out = capsys.readouterr().out
    assert "THIS IS THE FAILURE" in out
    assert "-" not in out.split("days ago")[0][-4:]  # no negative age


def test_a_future_runs_prefix_does_not_displace_the_real_window(monkeypatch, capsys):
    # `days[-args.days:]` takes the LAST N dates, and future dates sort last — so a single
    # future-dated prefix could push every real run out of the window and leave the report
    # judging nothing that happened.
    monkeypatch.setenv("JOBFETCHER_DATA_BUCKET", "b")
    fake = _FakeS3(["raw/jsearch/2026-09-25/a.json"], {
        "runs/2027-01-01/future.json": _summary("2027-01-01", fetched=999),
        "runs/2026-09-25/real.json": _crashed("2026-09-25"),
    })
    assert ci.main(["--today", "2026-09-25", "--days", "1"], client=fake) == ci.FAIL_EXIT
    out = capsys.readouterr().out
    assert "2026-09-25" in out and "2027-01-01" not in out  # the REAL run was judged


def test_dates_in_keys_caps_at_the_cutoff():
    keys = ["a/2026-09-01/x", "a/2026-09-30/y", "a/2027-01-01/z"]
    assert ci.dates_in_keys(keys, not_after=date(2026, 9, 25)) == [date(2026, 9, 1)]
    assert ci.dates_in_keys(keys) == [                       # uncapped keeps everything
        date(2026, 9, 1), date(2026, 9, 30), date(2027, 1, 1)
    ]
    # the boundary is INCLUSIVE — today's own data is evidence for today
    assert ci.dates_in_keys(["a/2026-09-25/x"], not_after=date(2026, 9, 25)) == [
        date(2026, 9, 25)
    ]
