"""`core/intake.py` — the rule deciding whether the daily digest must lead with an intake alert.
Pure functions, so every case is pinned here, and each alert case is paired with the silence that
must accompany it: an alert that fires on an ordinary day is furniture, and a reader who has
learned to skip it misses the day it is real."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from jobfetcher.adapters.jsearch_source import (
    STOP_BUDGET_EXHAUSTED,
    STOP_PARTIAL_ERRORS,
    STOP_RATE_LIMITED,
)
from jobfetcher.core.ingest import SKIP_NOT_A_FETCH_DAY, is_fetch_day
from jobfetcher.core.intake import (
    FIRST_CLEAN_CYCLE,
    IntakeAlert,
    is_sweep,
    latest_fetch_day,
    problem_on_day,
    sweep_problem,
)

CLEAN = date(2026, 9, 25)   # inside the first clean cycle
LEGACY = date(2026, 9, 7)   # before it — the quota was spent by the pre-fix daily sweep


def _ingest(stopped, **extra):
    return {"fetch_stopped": stopped, "fetched": 0, **extra}


# ── sweep_problem: each early stop says WHAT failed and WHERE to look ─────────────
def test_a_rate_limit_in_a_clean_cycle_is_loud_and_points_at_the_dashboard():
    alert = sweep_problem(_ingest(STOP_RATE_LIMITED), run_date=CLEAN)
    assert isinstance(alert, IntakeAlert)
    assert "quota" in alert.what and "RapidAPI dashboard" in alert.where


def test_a_rate_limit_in_the_legacy_cycle_is_silent():
    # The pair. Before FIRST_CLEAN_CYCLE a spent quota is expected; announcing it every morning
    # until the 22nd would teach the reader to ignore the banner before it ever matters.
    assert sweep_problem(_ingest(STOP_RATE_LIMITED), run_date=LEGACY) is None


def test_the_clean_cycle_starts_on_the_reset_day_itself():
    assert sweep_problem(_ingest(STOP_RATE_LIMITED), run_date=FIRST_CLEAN_CYCLE) is not None
    day_before = FIRST_CLEAN_CYCLE - timedelta(days=1)
    assert sweep_problem(_ingest(STOP_RATE_LIMITED), run_date=day_before) is None


def test_a_budget_stop_points_at_the_knob_to_change():
    alert = sweep_problem(_ingest(STOP_BUDGET_EXHAUSTED), run_date=CLEAN)
    assert alert is not None and "request_budget_per_run" in alert.where


@pytest.mark.parametrize(
    ("n", "expected"), [(1, "1 job search failed"), (4, "4 job searches failed")]
)
def test_partial_errors_count_the_failed_searches(n, expected):
    alert = sweep_problem(_ingest(STOP_PARTIAL_ERRORS, fetch_failed_queries=n), run_date=CLEAN)
    assert alert is not None and alert.what.startswith(expected)
    assert "CloudWatch" in alert.where


@pytest.mark.parametrize(
    "stopped", [STOP_PARTIAL_ERRORS, "some_future_reason"], ids=["logs", "s3-prefix"]
)
def test_a_pointer_to_logs_names_the_sweeps_date_not_today(stopped):
    # The banner repeats on the two days between sweeps, so "this run's logs" would send the
    # reader to TODAY's run — which made no requests. The pointer must carry the sweep's date.
    alert = sweep_problem(_ingest(stopped, fetch_failed_queries=2), run_date=CLEAN)
    assert alert is not None and CLEAN.isoformat() in alert.where


def test_an_unknown_stop_reason_is_loud_not_silently_healthy():
    # A reason this build does not know means the sweep ended early for an unknown cause —
    # the ERR-017 shape until proven otherwise.
    alert = sweep_problem(_ingest("some_future_reason"), run_date=CLEAN)
    assert alert is not None and "some_future_reason" in alert.what


# ── ...and the silences: nothing is announced for a healthy day or a non-sweep ───
@pytest.mark.parametrize("fetched", [140, 0])
def test_a_completed_sweep_is_healthy_whether_or_not_new_jobs_came_back(fetched):
    # THE design decision. The alert keys on how the sweep ENDED, never on whether new postings
    # arrived: re-fetching a known posting writes nothing, so an ordinary quiet week also looks
    # like "nothing new" — and must not raise an alert.
    assert sweep_problem({"fetch_stopped": None, "fetched": fetched}, run_date=CLEAN) is None


@pytest.mark.parametrize(
    "ingest",
    [
        {"fetch_stopped": SKIP_NOT_A_FETCH_DAY},  # not a fetch day
        {"fetched": 0},                           # a pre-#63 build: no fetch_stopped key at all
        None,                                     # a crashed run writes no ingest block
        "not-a-dict",
    ],
)
def test_no_sweep_means_no_alert(ingest):
    assert not is_sweep(ingest)
    assert sweep_problem(ingest, run_date=CLEAN) is None


# ── problem_on_day: folding every run written on one date ─────────────────────────
_OK = {"ingest": {"fetch_stopped": None, "fetched": 9}}
_BAD = {"ingest": {"fetch_stopped": STOP_RATE_LIMITED, "fetched": 0}}
_CRASHED = {"statusCode": 500, "error": "boom"}


@pytest.mark.parametrize("order", [[_BAD, _OK], [_OK, _BAD]], ids=["bad-first", "ok-first"])
def test_a_successful_retry_the_same_day_clears_the_alert_in_either_order(order):
    # Both orders: summaries come back in key order, and the key is a random run_id, so a fold
    # that depended on order would flip a coin in production.
    assert problem_on_day(order, run_date=CLEAN) is None


def test_a_failed_sweep_with_no_successful_retry_is_reported():
    alert = problem_on_day([_BAD, _CRASHED], run_date=CLEAN)
    assert alert is not None and "quota" in alert.what


@pytest.mark.parametrize("summaries", [[], [_CRASHED], ["junk", 42]])
def test_a_day_with_no_recorded_sweep_is_unknown_not_alarming(summaries):
    # A crashed run is announced by the returned-500 alarm; this rule does not guess about it.
    assert problem_on_day(summaries, run_date=CLEAN) is None


# ── latest_fetch_day: where a non-fetch day looks back to ─────────────────────────
@pytest.mark.parametrize("n", [2, 3, 4, 5, 7])
def test_latest_fetch_day_agrees_with_is_fetch_day_exhaustively(n):
    start = date(2026, 1, 1)
    for i in range(400):
        day = start + timedelta(days=i)
        got = latest_fetch_day(day, every_n_days=n)
        assert got is not None and got <= day and is_fetch_day(got, every_n_days=n)
        # nothing strictly between it and `day` is a fetch day: it really is the LATEST one
        assert not any(
            is_fetch_day(got + timedelta(days=k), every_n_days=n)
            for k in range(1, (day - got).days + 1)
        )


def test_the_days_between_sweeps_still_report_the_last_sweep():
    # Why this exists: without a look-back the alert would appear on the fetch day and vanish
    # for the next two, and something that comes and goes is easy to dismiss.
    for d in (22, 23, 24):
        assert latest_fetch_day(date(2026, 9, d), every_n_days=3) == date(2026, 9, 22)
    assert latest_fetch_day(date(2026, 9, 25), every_n_days=3) == date(2026, 9, 25)


@pytest.mark.parametrize("n", [1, 0])
def test_with_the_cadence_off_today_is_the_fetch_day(n):
    assert latest_fetch_day(CLEAN, every_n_days=n) == CLEAN
