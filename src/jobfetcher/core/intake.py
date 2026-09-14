"""Is JSearch intake healthy? The rule behind the daily digest's intake alert.

**What / Why / So-what.** *What:* pure functions that turn a run summary's ingest block into a
short `(what failed, where to look)` alert, or `None`. *Why:* a run that fetches nothing used to
be indistinguishable from a quiet day (ERR-017); `fetch_stopped` made it diagnosable, and this
makes it *announced* — in the digest the user already reads, not in a fourth alarm nobody
reads (INV-004). *So-what:* the user learns intake broke on the day it breaks, and every day
after until a sweep succeeds.

**It keys on how the last sweep ENDED, never on whether new postings arrived.** Re-fetching a
posting we already have writes nothing — `upsert_bronze` is `on_conflict_do_nothing` and
`put_raw` skips existing keys — so "no new jobs landed" is what an ordinary quiet market week
looks like too. An alert built on it would cry wolf; one built on `fetch_stopped` does not.

Pure: no I/O. The handler supplies today's ingest block on a fetch day, or the last fetch day's
run summaries otherwise, so the alert does not flicker off for the two days between sweeps.
`scripts/check_ingestion.py` shares `FIRST_CLEAN_CYCLE` with this module; its verdict ladder is
its own, because it answers a wider operator question (did postings actually land?).
"""
from __future__ import annotations

from datetime import date
from typing import Any, NamedTuple

from ..adapters.jsearch_source import (
    STOP_BUDGET_EXHAUSTED,
    STOP_PARTIAL_ERRORS,
    STOP_RATE_LIMITED,
)
from .ingest import SKIP_NOT_A_FETCH_DAY

# The first quota cycle running ENTIRELY on the fixed cadence — the first whose outcome actually
# tests the ERR-017 capacity fix.
#
# WHY NOT SIMPLY "THE LAST RESET". Being rate-limited *mid-cycle* is ordinary once a month's
# allowance is spent; that is what a quota is. It only proves something is wrong when it happens
# in a cycle the fixed arithmetic was sized to fit. September 2026's allowance was burned by the
# OLD daily sweep before the fix reached the live Lambda, so a 429 anywhere in that cycle says
# nothing about the new behaviour. Judging against the last reset instead would alarm on every
# ordinary day between exhaustion and rollover — the false alarm that makes a warning unreadable.
#
# Lives here, not in the script, because the email alert and `check_ingestion.py` must share ONE
# baseline — two copies of "what counts as a clean cycle" would drift, which is ERR-016's shape.
FIRST_CLEAN_CYCLE = date(2026, 9, 22)


class IntakeAlert(NamedTuple):
    """Short on purpose: WHAT failed, and WHERE to look. One line in the email, not a paragraph —
    the run summary and the logs hold the detail."""

    what: str
    where: str


def is_sweep(ingest: Any) -> bool:
    """True when this ingest block records an actual sweep attempt: a build that reports
    `fetch_stopped` (PR #63 onwards) on a day that was a fetch day."""
    return (
        isinstance(ingest, dict)
        and "fetch_stopped" in ingest
        and ingest.get("fetch_stopped") != SKIP_NOT_A_FETCH_DAY
    )


def sweep_problem(
    ingest: Any, *, run_date: date, since: date = FIRST_CLEAN_CYCLE
) -> IntakeAlert | None:
    """The alert for ONE sweep (the one that ran on `run_date`), or `None` when it is healthy,
    not a sweep at all, or an expected legacy rate-limit (a cycle that began before `since`).

    Where the pointer is a log or an S3 prefix, it names the sweep's date: on the two days
    between sweeps the banner is about an EARLIER run, and "this run's logs" would send the
    reader to the wrong day."""
    if not is_sweep(ingest):
        return None
    stopped = ingest.get("fetch_stopped")
    if stopped is None:
        return None  # the whole matrix was searched: healthy, even if nothing new came back
    if stopped == STOP_RATE_LIMITED:
        if run_date < since:
            return None  # legacy cycle: the pre-fix daily sweep spent this quota, expected
        return IntakeAlert(
            "Intake stopped: JSearch monthly quota used up",
            "Check usage on the RapidAPI dashboard",
        )
    if stopped == STOP_BUDGET_EXHAUSTED:
        return IntakeAlert(
            "Job search cut short: request budget below the sweep size",
            "Raise request_budget_per_run in search_config.yml",
        )
    day = run_date.isoformat()
    if stopped == STOP_PARTIAL_ERRORS:
        n = ingest.get("fetch_failed_queries", 0)
        return IntakeAlert(
            f"{n} job search{'es' if n != 1 else ''} failed at JSearch",
            f"Check the CloudWatch logs for the {day} run",
        )
    # A reason this build does not know. Loud rather than silently healthy: the sweep ended early
    # and we cannot say why, which is the ERR-017 shape until proven otherwise.
    return IntakeAlert(
        f"Intake ended early ({stopped})", f"Check runs/{day}/ in the S3 data bucket"
    )


def problem_on_day(
    summaries: list[Any], *, run_date: date, since: date = FIRST_CLEAN_CYCLE
) -> IntakeAlert | None:
    """Fold every run summary written for ONE date. A healthy sweep that day wins — a successful
    retry fixed it — otherwise the first problem is reported. No sweep recorded at all gives
    `None`: a crashed run writes no ingest block, and announcing THAT is the returned-500
    alarm's job, not this one's."""
    problems: list[IntakeAlert] = []
    for summary in summaries:
        ingest = summary.get("ingest") if isinstance(summary, dict) else None
        if not is_sweep(ingest):
            continue
        alert = sweep_problem(ingest, run_date=run_date, since=since)
        if alert is None:
            return None
        problems.append(alert)
    return problems[0] if problems else None


def latest_fetch_day(on_or_before: date, *, every_n_days: int) -> date | None:
    """The most recent fetch day on or before `on_or_before` — closed form, the pair to
    `core.ingest.next_fetch_day`. `every_n_days <= 1` means every day is a fetch day."""
    if every_n_days <= 1:
        return on_or_before
    ordinal = on_or_before.toordinal()
    candidate = ordinal - (ordinal % every_n_days)
    return date.fromordinal(candidate) if candidate >= 1 else None
