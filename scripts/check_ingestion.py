#!/usr/bin/env python3
"""check_ingestion.py — did the JSearch sweep actually resume? A read-only PASS/FAIL.

    python scripts/check_ingestion.py                 # verdict on the latest runs
    python scripts/check_ingestion.py --runs 10       # look further back
    python scripts/check_ingestion.py --today 2026-09-22   # pin "now" (for a dry check)

**Why this exists as a command and not as a note.** [ERR-017] ran for two months because a
run that fetched nothing was indistinguishable from a run with nothing to fetch, and the
follow-up check — *"on or after the 22nd, confirm postings actually landed"* — was carried in
a handoff document as prose and never run. A standard not wired into a command is a
suggestion (`docs/00-design-philosophy.md`), so this is the command.

**It is READ-ONLY and it must stay that way.** It lists S3 and reads run summaries. It does
**not** call JSearch — the quota is the thing under test, and spending a request to ask
whether we have requests left is the ERR-013 mistake (measuring with the wrong instrument).
For actual usage figures, read the RapidAPI dashboard.

**The two traps it exists to avoid**, both of which would train a reader to ignore it:

  1. **Running out of quota mid-cycle is what a quota IS**, not a regression. The free tier
     is 200 requests/month and rolls over on the 22nd; between exhaustion and rollover the
     tool legitimately fetches nothing. Reporting that as a failure is how a check becomes
     furniture.
  2. **`not_a_fetch_day` on 2 days in 3 is the DESIGN WORKING** (`FETCH_EVERY_N_DAYS`), not a
     fault. The Lambda runs daily; the sweep does not.

The one condition that re-opens ERR-017 is `rate_limited` **inside a cycle that began on or
after `FIRST_CLEAN_CYCLE`** — the first cycle the fixed cadence was actually sized to fit
inside. Judging against merely "the last reset" instead would FAIL on every ordinary day
between exhaustion and rollover; that error was caught by this script's own tests before it
ever ran, which is the entire argument for writing the judgment as a pure function.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobfetcher.adapters.jsearch_source import (  # noqa: E402
    STOP_BUDGET_EXHAUSTED,
    STOP_PARTIAL_ERRORS,
    STOP_RATE_LIMITED,
)
from jobfetcher.core.ingest import (  # noqa: E402
    FETCH_EVERY_N_DAYS,
    SKIP_NOT_A_FETCH_DAY,
    SOURCE_MONTHLY_QUOTA,
    is_fetch_day,
)

_BUCKET_ENV = "JOBFETCHER_DATA_BUCKET"

# The day of the month the JSearch/RapidAPI monthly allowance rolls over. Established from
# evidence, not from the plan's marketing: the S3 audit trail showed a two-month cycle
# resetting on the 22nd, and the RapidAPI dashboard agreed (ERR-017).
QUOTA_RESET_DAY = 22

# The first quota cycle that runs ENTIRELY on the fixed cadence, and therefore the first one
# whose outcome actually tests the ERR-017 capacity fix.
#
# WHY THIS IS NOT SIMPLY "THE LAST RESET". Being rate-limited *mid-cycle* is ordinary once the
# month's allowance is spent — that is what a quota IS. It only proves something is wrong when
# it happens in a cycle the fixed arithmetic was supposed to fit inside (~10 sweeps x the sweep
# cost, comfortably under the plan). September 2026's allowance was burned by the OLD daily
# sweep before the fix reached the live Lambda on 2026-09-05, so a 429 anywhere in that cycle
# is a legacy of the old behaviour and says nothing about the new one.
#
# Judging against the last reset instead would have produced a FAIL on every ordinary day
# between exhaustion and rollover — the false alarm that makes a check unreadable. Override
# with --first-clean-cycle when the baseline moves (a plan change, a cadence change).
FIRST_CLEAN_CYCLE = date(2026, 9, 22)

# Verdict levels. Only FAIL sets a non-zero exit — EXPECTED and WARN are information, because
# a check that cries wolf on a healthy day stops being read (the B-5 lesson).
PASS, EXPECTED, WARN, FAIL, UNKNOWN = "PASS", "EXPECTED", "WARN", "FAIL", "UNKNOWN"


def last_quota_reset(today: date, *, reset_day: int = QUOTA_RESET_DAY) -> date:
    """The most recent monthly quota reset on or before `today`. Pure."""
    if today.day >= reset_day:
        return today.replace(day=reset_day)
    prev = today.replace(day=1)
    prev = prev.replace(year=prev.year - 1, month=12) if prev.month == 1 else prev.replace(
        month=prev.month - 1
    )
    return prev.replace(day=reset_day)


def verdict(
    summary: dict[str, Any], *, since: date = FIRST_CLEAN_CYCLE
) -> tuple[str, str]:
    """`(level, message)` for one run summary. **Pure — this is the whole judgment**, so it is
    unit-testable without S3, and every trap above is encoded here rather than in the plumbing.

    `since` is the start of the first quota cycle that tests the fix (`FIRST_CLEAN_CYCLE`). A
    run's own `run_date` decides which side of it the run falls on, so replaying an old summary
    can never raise a false alarm.
    """
    ingest = summary.get("ingest") or {}
    run_date = summary.get("run_date", "?")
    if "fetch_stopped" not in ingest:
        return UNKNOWN, (
            f"{run_date}: this summary has no `fetch_stopped` key, so it was written by a build "
            "older than PR #63. It cannot say why it fetched zero — that is the ERR-017 blind "
            "spot itself. Check a run from a newer build."
        )

    stopped = ingest.get("fetch_stopped")
    fetched = ingest.get("fetched", 0)
    failed = ingest.get("fetch_failed_queries", 0)
    legacy_cycle = str(run_date) < since.isoformat()

    if stopped == SKIP_NOT_A_FETCH_DAY:
        return EXPECTED, (
            f"{run_date}: not a fetch day — the sweep runs every {FETCH_EVERY_N_DAYS} days by "
            "design, so 2 days in 3 look like this. Scoring, the digest and the report still ran."
        )
    if stopped == STOP_RATE_LIMITED:
        if legacy_cycle:
            return EXPECTED, (
                f"{run_date}: rate-limited, in a quota cycle that began before {since} — i.e. "
                "one whose allowance was spent by the pre-fix daily sweep. Running out mid-cycle "
                "is what a quota IS. NOT a regression, and nothing here to 'fix'."
            )
        return FAIL, (
            f"{run_date}: rate-limited in a cycle that began on or after {since}, which the "
            "fixed cadence was sized to fit inside. The capacity arithmetic is therefore still "
            "wrong — RE-OPEN ERR-017. Check actual usage on the RapidAPI dashboard (do NOT probe "
            "the API), then re-do titles x countries x pages x runs-per-month against the plan."
        )
    if stopped == STOP_BUDGET_EXHAUSTED:
        return WARN, (
            f"{run_date}: stopped on our OWN request budget (`request_budget_per_run`), not the "
            "provider's. The matrix was not fully searched, so the counts are a floor. Raise the "
            "budget or shrink the matrix — they must agree."
        )
    if stopped == STOP_PARTIAL_ERRORS:
        return WARN, (
            f"{run_date}: the sweep ran its whole loop but {failed} query/queries died on "
            f"upstream errors, so part of the matrix went unsearched. `fetched: {fetched}` here "
            "is a FLOOR, not the day's supply — do not read it as 'the source had nothing'."
        )
    if stopped is not None:
        return UNKNOWN, f"{run_date}: unrecognised fetch_stopped={stopped!r} — newer than this script?"

    # fetch_stopped is None: the sweep completed its whole matrix, so `fetched` is the truth.
    if fetched > 0:
        return PASS, (
            f"{run_date}: swept the full matrix and landed {fetched} posting(s). Ingestion is "
            "working — this is the condition ERR-017 / INV-003 were waiting on."
        )
    return WARN, (
        f"{run_date}: the sweep completed its whole matrix and the source genuinely returned "
        "nothing. Honest, but worth a second look if it repeats — check the RapidAPI dashboard "
        "and whether `targeting` is too narrow."
    )


def latest_raw_date(keys: list[str]) -> str | None:
    """The newest `YYYY-MM-DD` appearing in the `raw/` keys, or None. Pure."""
    dates = {m.group(1) for k in keys if (m := re.search(r"(\d{4}-\d{2}-\d{2})", k))}
    return max(dates) if dates else None


def _s3(client: Any = None) -> tuple[Any, str]:
    bucket = os.environ.get(_BUCKET_ENV, "").strip()
    if not bucket:
        raise SystemExit(f"no data bucket — set ${_BUCKET_ENV}")
    if client is None:
        import boto3  # lazy: tests inject a fake/moto client (mirrors adapters/s3_raw.py)

        client = boto3.client("s3")
    return client, bucket


def _list_keys(client: Any, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = client.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")


def _read_json(client: Any, bucket: str, key: str) -> dict[str, Any] | None:
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except Exception:  # noqa: BLE001 — one unreadable summary must not kill the report
        return None


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5, help="how many recent run summaries to judge")
    ap.add_argument("--today", type=str, default=None, help="pin today's date (YYYY-MM-DD)")
    ap.add_argument("--first-clean-cycle", type=str, default=None,
                    help="start of the first quota cycle that tests the fix "
                         f"(default {FIRST_CLEAN_CYCLE})")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    reset = last_quota_reset(today)
    since = (date.fromisoformat(args.first_clean_cycle) if args.first_clean_cycle
             else FIRST_CLEAN_CYCLE)
    client, bucket = _s3(client)

    print(f"bucket={bucket}  today={today}  last quota reset={reset}")
    print(f"judging rate-limits against the first clean cycle: {since}")
    print(f"cadence: a sweep every {FETCH_EVERY_N_DAYS} days; plan allows "
          f"{SOURCE_MONTHLY_QUOTA} requests/month")
    print(f"today is {'a FETCH day' if is_fetch_day(today) else 'NOT a fetch day'}\n")

    raw = latest_raw_date(_list_keys(client, bucket, "raw/"))
    if raw:
        # Measured against `since`, NOT against the last reset. "Later than the last reset" was
        # the first version of this line and it read reassuringly while nothing had landed for
        # days — the question that matters is whether anything has landed in the cycle that
        # actually tests the fix.
        age = (today - date.fromisoformat(raw)).days
        if raw >= since.isoformat():
            state = "landed in the cycle under test"
        elif today < since:
            # `since` has not arrived yet, so there is nothing to conclude. Saying "nothing has
            # landed since <a future date>" would read as a fault when it is just the calendar.
            state = f"the cycle that tests the fix starts {since} — too early to judge"
        else:
            state = f"NOTHING has landed since {since}, the cycle that tests the fix"
        print(f"latest raw/ posting date: {raw}  ({age} days ago — {state})\n")
    else:
        print("latest raw/ posting date: none found\n")

    run_keys = sorted(_list_keys(client, bucket, "runs/"))[-args.runs:]
    if not run_keys:
        print("no run summaries found — cannot judge.")
        return 1

    worst = 0
    for key in reversed(run_keys):
        summary = _read_json(client, bucket, key)
        if summary is None:
            print(f"  [UNKNOWN] {key}: unreadable")
            continue
        level, msg = verdict(summary, since=since)
        print(f"  [{level}] {msg}")
        if level == FAIL:
            worst = 1

    print("\nFAIL" if worst else "\nOK (no failing condition found)")
    print("Actual request usage is on the RapidAPI dashboard — this script deliberately does "
          "NOT call JSearch to find out.")
    return worst


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
