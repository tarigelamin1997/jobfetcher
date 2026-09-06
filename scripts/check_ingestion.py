#!/usr/bin/env python3
"""check_ingestion.py — did the JSearch sweep actually resume? A read-only verdict.

    python scripts/check_ingestion.py                      # judge the last 5 days of runs
    python scripts/check_ingestion.py --days 10            # look further back
    python scripts/check_ingestion.py --today 2026-09-22   # pin "now" for a dry check

**Exit codes** — three, not two, because "broken" and "cannot tell" are different answers and
a wrapper checking `$?` must be able to distinguish them:

    0  OK             nothing failing
    1  FAIL           something is genuinely wrong (see the FAIL conditions below)
    2  CANNOT JUDGE   no summaries, unreadable summaries, or a bad argument

**Why this exists as a command and not as a note.** [ERR-017] ran for two months because a run
that fetched nothing was indistinguishable from a run with nothing to fetch, and the follow-up
check — *"on or after the 22nd, confirm postings actually landed"* — lived as prose in a handoff
document and was never run. A standard not wired into a command is a suggestion
(`docs/00-design-philosophy.md`), so this is the command.

**It is READ-ONLY and must stay that way.** It lists S3 and reads run summaries. It does **not**
call JSearch — the quota is the thing under test, and spending a request to ask whether requests
remain is the ERR-013 mistake (measuring with the wrong instrument). Usage lives on the RapidAPI
dashboard.

**Two results that look alarming and are NOT**, both reported `EXPECTED`:

  1. **Running out of quota mid-cycle is what a quota IS.** The free tier is 200 requests/month
     and rolls over on the 22nd; between exhaustion and rollover the tool legitimately fetches
     nothing. Reporting that as a failure is how a check becomes furniture.
  2. **`not_a_fetch_day` is the DESIGN WORKING** (`FETCH_EVERY_N_DAYS`) — the Lambda runs daily,
     the sweep does not, so 2 days in 3 look like this.

**And the mirror of that, which the first version of this script got wrong.** Having refused to
cry wolf it could not bark at all: a pipeline returning `statusCode: 500` every day reported
`OK`. A crashed run writes `{"statusCode": 500, "run_id", "run_date", "error"}` to the same
`runs/` prefix with **no `ingest` key**, which the pre-#63 branch happily swallowed. That is the
ERR-010 shape (38 days of returned 500s) reproduced inside the tool built to catch it. So the
FAIL conditions are now:

  - a run that returned **`statusCode: 500`**;
  - `rate_limited` inside a cycle beginning on or after `FIRST_CLEAN_CYCLE`;
  - **more consecutive `not_a_fetch_day` runs than the cadence can produce** — arithmetically
    impossible unless the cadence is misconfigured (the `$JOBFETCHER_FETCH_EVERY_N_DAYS` knob).
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
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The day of the month the JSearch/RapidAPI allowance rolls over. From evidence, not marketing:
# the S3 audit trail showed a two-month cycle resetting on the 22nd and the dashboard agreed.
QUOTA_RESET_DAY = 22

# The first quota cycle running ENTIRELY on the fixed cadence — the first whose outcome actually
# tests the ERR-017 capacity fix.
#
# WHY NOT SIMPLY "THE LAST RESET". Being rate-limited *mid-cycle* is ordinary once the month's
# allowance is spent; that is what a quota is. It only proves something is wrong when it happens
# in a cycle the fixed arithmetic was sized to fit. September 2026's allowance was burned by the
# OLD daily sweep before the fix reached the live Lambda, so a 429 anywhere in that cycle says
# nothing about the new behaviour. Judging against the last reset instead would FAIL on every
# ordinary day between exhaustion and rollover — the false alarm that makes a check unreadable.
# Override with --first-clean-cycle when the baseline moves (a plan or cadence change).
FIRST_CLEAN_CYCLE = date(2026, 9, 22)

PASS, EXPECTED, WARN, FAIL, UNKNOWN = "PASS", "EXPECTED", "WARN", "FAIL", "UNKNOWN"

OK_EXIT, FAIL_EXIT, CANNOT_JUDGE_EXIT = 0, 1, 2


def last_quota_reset(today: date, *, reset_day: int = QUOTA_RESET_DAY) -> date:
    """The most recent monthly quota reset on or before `today`. Pure. (Header context only —
    the verdicts key off `FIRST_CLEAN_CYCLE`, deliberately; see its comment.)"""
    if today.day >= reset_day:
        return today.replace(day=reset_day)
    prev = today.replace(day=1)
    prev = prev.replace(year=prev.year - 1, month=12) if prev.month == 1 else prev.replace(
        month=prev.month - 1
    )
    return prev.replace(day=reset_day)


def verdict(summary: Any, *, since: date = FIRST_CLEAN_CYCLE) -> tuple[str, str]:
    """`(level, message)` for one run summary. **Pure — this is the whole judgment**, so every
    trap lives here rather than in the plumbing and is unit-testable without S3."""
    if not isinstance(summary, dict):
        # A JSON body that parses but is not an object. Exactly the class of bug PR #70 fixed in
        # the JSearch adapter, so guarding it here rather than re-learning it.
        return UNKNOWN, f"a run summary is {type(summary).__name__}, not an object — skipped."

    # ONE date validator, shared with the streak detectors via `_run_day`. Two of them disagreed:
    # this one only regex-checked the shape, so `2026-13-45` was judged here and invisible there.
    day = _run_day(summary)
    if day is None:
        # Cannot place it in a cycle, so cannot judge it. Deliberately NOT a FAIL: an
        # unparseable date is missing information, and guessing toward alarm is crying wolf.
        raw_date = summary.get("run_date")
        return UNKNOWN, f"a run summary has an unusable run_date ({raw_date!r}) — cannot judge it."
    run_date = day.isoformat()

    status = summary.get("statusCode")
    if status == 500:
        err = summary.get("error", "(no error field)")
        return FAIL, (
            f"{run_date}: the run FAILED — statusCode 500: {err}. Nothing fetched, nothing "
            "scored, no digest for this run. Not a quota question — check the CloudWatch logs "
            "for this run_id. (One failure is a fact; the streak note below says whether it is "
            "a pattern.)"
        )

    ingest = summary.get("ingest")
    if not isinstance(ingest, dict):
        mode = summary.get("mode")
        if mode:
            return EXPECTED, f"{run_date}: mode={mode} run — no ingest stage, nothing to judge."
        return UNKNOWN, (
            f"{run_date}: statusCode {status} but no `ingest` block — an unrecognised summary "
            "shape. Look at the object itself."
        )

    if "fetch_stopped" not in ingest:
        return UNKNOWN, (
            f"{run_date}: the ingest block has no `fetch_stopped` key, so this run predates "
            "PR #63 and cannot say why it fetched zero — that IS the ERR-017 blind spot. Judge a "
            "run from a newer build."
        )

    stopped = ingest.get("fetch_stopped")
    fetched = ingest.get("fetched", 0)
    failed = ingest.get("fetch_failed_queries", 0)
    legacy_cycle = run_date < since.isoformat()

    if stopped == SKIP_NOT_A_FETCH_DAY:
        return EXPECTED, (
            f"{run_date}: not a fetch day — the sweep runs every {FETCH_EVERY_N_DAYS} days by "
            "design, so 2 days in 3 look like this. Scoring, the digest and the report still ran."
        )
    if stopped == STOP_RATE_LIMITED:
        if legacy_cycle:
            return EXPECTED, (
                f"{run_date}: rate-limited, in a quota cycle that began before {since} — one "
                "whose allowance was spent by the pre-fix daily sweep. Running out mid-cycle is "
                "what a quota IS. NOT a regression, and nothing here to 'fix'."
            )
        return FAIL, (
            f"{run_date}: rate-limited in a cycle beginning on or after {since}, which the fixed "
            "cadence was sized to fit. The capacity arithmetic is therefore still wrong — RE-OPEN "
            "ERR-017. Check usage on the RapidAPI dashboard (do NOT probe the API), then re-do "
            "titles x countries x pages x runs-per-month against the plan."
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
            f"upstream errors, so part of the matrix went unsearched. `fetched: {fetched}` is a "
            "FLOOR, not the day's supply — do not read it as 'the source had nothing'."
        )
    if stopped is not None:
        return UNKNOWN, f"{run_date}: unrecognised fetch_stopped={stopped!r} — newer than this script?"

    if fetched > 0:
        return PASS, (
            f"{run_date}: swept the full matrix and landed {fetched} posting(s). Ingestion is "
            "working — the condition ERR-017 / INV-003 were waiting on."
        )
    return WARN, (
        f"{run_date}: the sweep completed its whole matrix and the source genuinely returned "
        "nothing. Honest, but worth a second look if it repeats — check the RapidAPI dashboard "
        "and whether `targeting` is too narrow."
    )


def _run_day(summary: Any) -> "date | None":
    """The summary's `run_date` as a real `date`, or None if it has none we can parse. Pure."""
    if not isinstance(summary, dict):
        return None
    raw = summary.get("run_date")
    if not isinstance(raw, str) or not _ISO.match(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _longest_run(by_day: "dict[date, bool]") -> int:
    """The longest run of CALENDAR-ADJACENT days whose value is True. Pure.

    Adjacency is the whole point. Both callers previously walked sorted dates and incremented a
    streak on each True without checking that the days actually touch — so summaries for
    09-01, 09-10 and 09-20 counted as "3 consecutive", and a **missing** run date (an S3 object
    that never landed, a `--days` window with a hole in it) manufactured a FAIL out of nothing.
    That is the crying-wolf direction this file spends its docstring arguing against, so a gap
    now RESETS the streak: absence of evidence is not evidence of a streak.

    **The cost of that choice, stated rather than left to be discovered.** A genuinely dead
    sweep escapes these detectors if its summaries are *periodically missing* — 30 straight
    skip-days with every third summary absent yields a longest adjacent run of 2, under the
    threshold. The trade-off is still right (a sparse window is far likelier than "dead AND
    periodically missing", and a false alarm is the failure mode that gets a check ignored), but
    it is a real blind spot, so `main()` reports how many days in the window have no summary at
    all. A detector that can be defused silently is one nobody should trust twice.
    """
    streak = worst = 0
    prev: date | None = None
    for day in sorted(by_day):
        contiguous = prev is not None and (day - prev).days == 1
        streak = (streak + 1) if (by_day[day] and contiguous) else (1 if by_day[day] else 0)
        worst = max(worst, streak)
        prev = day
    return worst


def cadence_anomaly(
    summaries: list[dict[str, Any]], *, every_n_days: int = FETCH_EVERY_N_DAYS
) -> str | None:
    """A message if MORE consecutive `not_a_fetch_day` runs appear than the cadence can produce,
    else None. Pure.

    With a cadence of N, at most N-1 skips can fall between two sweeps — so a longer run of them
    is arithmetically impossible unless the cadence is misconfigured (the
    `$JOBFETCHER_FETCH_EVERY_N_DAYS` knob, or a `run_date` that never lands on a fetch day).
    Without this, a permanently-dead sweep **whose summaries are contiguous** prints an unbroken
    column of `EXPECTED` and exits 0 — every line individually correct, the whole picture wrong.
    (The contiguity qualifier is load-bearing: see `_longest_run` for the blind spot it leaves
    and how `main()` surfaces it.)

    The per-day fold is **AND** — a day counts as skipped only if EVERY run that day skipped —
    because this is the one detector that can turn a column of `EXPECTED` into exit 1 on its
    own, so it gets the conservative operator. `failure_streak` folds with OR for the opposite
    reason; see its docstring."""
    if every_n_days <= 1:
        return None  # cadence off: every day is a fetch day, so skips are the anomaly elsewhere
    by_day: dict[date, bool] = {}
    for s in summaries:
        day = _run_day(s)
        if day is None:
            continue
        ingest = s.get("ingest")
        skipped = isinstance(ingest, dict) and ingest.get("fetch_stopped") == SKIP_NOT_A_FETCH_DAY
        by_day[day] = by_day.get(day, True) and skipped
    worst = _longest_run(by_day)
    if worst < every_n_days:
        return None
    return (
        f"{worst} CONSECUTIVE non-fetch days, but a cadence of {every_n_days} can only ever "
        f"produce {every_n_days - 1} in a row. The sweep is not merely paused — it is not "
        "running at all. Check $JOBFETCHER_FETCH_EVERY_N_DAYS on the Lambda (a value <= 1 "
        "disables the cadence; a large one disables the sweep) and terraform/lambda.tf."
    )


def failure_streak(summaries: list[dict[str, Any]]) -> str | None:
    """A message if consecutive DATES each contain a failed run, else None. Pure.

    One `statusCode: 500` is a fact — an Aurora resume, a transient. **Consecutive days of them
    is ERR-010**, which ran 38 days precisely because each morning's failure looked like the
    last and nobody read the pattern. So the per-run verdict stays proportionate and the
    escalation lives here, where more than one day can be seen at once.

    The per-day fold is **OR** — any crash that day makes it a failure day, even if a manual
    retry later succeeded — because the *scheduled* run did fail. That is safe to be aggressive
    about: every summary this can count already made `verdict()` return `FAIL`, so this function
    has **no authority over the exit code** and can only escalate the message on a run that
    already set it. `cadence_anomaly`, which CAN set the exit code alone, folds with AND."""
    by_day: dict[date, bool] = {}
    for s in summaries:
        day = _run_day(s)
        if day is not None:
            by_day[day] = by_day.get(day, False) or s.get("statusCode") == 500
    worst = _longest_run(by_day)
    if worst < 2:
        return None
    return (
        f"{worst} CONSECUTIVE DAYS with a failed run. That is the ERR-010 shape — 38 days of "
        "returned-500s went unnoticed because each morning looked like the last. A pattern, not "
        "a blip: read the logs and the alarm history before anything else."
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
        kw: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = client.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])]
        token = page.get("NextContinuationToken")
        # `IsTruncated` without a token would re-request page 1 forever — an unbounded BILLED
        # loop, not merely a hang. Real S3 always sends the token; this costs one condition.
        if not page.get("IsTruncated") or not token:
            return keys


def _read_json(client: Any, bucket: str, key: str) -> tuple[Any, str | None]:
    """`(payload, error)`. An unreadable summary is reported, never swallowed: "I could not read
    anything" is a louder failure than "there was nothing to read", and the first version of this
    exited 0 for it while exiting non-zero for the second."""
    try:
        return json.loads(client.get_object(Bucket=bucket, Key=key)["Body"].read()), None
    except Exception as exc:  # noqa: BLE001 — one bad object must not kill the whole report
        return None, f"{type(exc).__name__}: {exc}"


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=int, default=5,
                    help="how many recent run DATES to judge (all runs within them)")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="pin today's date (YYYY-MM-DD)")
    ap.add_argument("--first-clean-cycle", type=date.fromisoformat, default=FIRST_CLEAN_CYCLE,
                    help=f"start of the first quota cycle that tests the fix (default {FIRST_CLEAN_CYCLE})")
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days must be >= 1")  # `[-0:]` is the whole list, which is not "none"

    today = args.today or date.today()
    since = args.first_clean_cycle
    reset = last_quota_reset(today)
    client, bucket = _s3(client)

    print(f"bucket={bucket}  today={today}  current quota cycle began {reset}")
    print(f"judging rate-limits against the first clean cycle: {since}")
    print(f"cadence: a sweep every {FETCH_EVERY_N_DAYS} days; plan allows "
          f"{SOURCE_MONTHLY_QUOTA} requests/month")
    print(f"today is {'a FETCH day' if is_fetch_day(today) else 'NOT a fetch day'}\n")

    failed = 0

    raw = latest_raw_date(_list_keys(client, bucket, "raw/"))
    # Measured against `since`, not the last reset: "later than the last reset" read
    # reassuringly while nothing had landed for days. `raw/` keys carry the LANDING date
    # (raw/{source}/{run_date}/…), not the posting's own date — so name it that.
    if raw and raw >= since.isoformat():
        state = "landed in the cycle under test"
    elif today <= since:
        state = f"the cycle that tests the fix starts {since} — too early to judge"
    else:
        # THE HEADLINE QUESTION, and it must reach the exit code. This used to be prose only:
        # the report could print "42 days ago — NOTHING has landed" and still exit 0, because
        # nothing about `raw/` staleness fed the verdict. A green $? beside an alarming
        # paragraph is the ERR-017 shape — a run that reports success while doing nothing.
        state = f"NOTHING has landed since {since}, the cycle that tests the fix — THIS IS THE FAILURE"
        failed += 1
    age = f"{(today - date.fromisoformat(raw)).days} days ago — " if raw else ""
    print(f"latest raw/ landing date: {raw or 'none found'}  ({age}{state})\n")

    # Select the last N run DATES and judge every run inside them. Taking the last N *keys*
    # ordered them by `run_id` within a day — a random uuid hex — so a retry could hide the
    # failing run behind a passing one from the same morning.
    # `.json` only: any other object under a date prefix (an S3-console folder placeholder, a
    # stray `.keep`, a truncated write) is unreadable and would pin the exit code at 2 forever.
    all_keys = [k for k in _list_keys(client, bucket, "runs/") if k.endswith(".json")]
    days = sorted({m.group(1) for k in all_keys if (m := re.search(r"runs/(\d{4}-\d{2}-\d{2})/", k))})
    wanted = set(days[-args.days:])
    run_keys = sorted(k for k in all_keys if any(f"runs/{d}/" in k for d in wanted))
    if not run_keys:
        print("no run summaries found — CANNOT JUDGE (wrong bucket, or nothing has run).")
        return CANNOT_JUDGE_EXIT

    unreadable = 0
    summaries: list[dict[str, Any]] = []
    for key in reversed(run_keys):
        payload, err = _read_json(client, bucket, key)
        if err is not None:
            print(f"  [UNKNOWN] {key}: unreadable — {err}")
            unreadable += 1
            continue
        if isinstance(payload, dict):
            summaries.append(payload)
        level, msg = verdict(payload, since=since)
        print(f"  [{level}] {msg}")
        if level == FAIL:
            failed += 1

    # The streak detectors reset on a calendar gap (see `_longest_run`), so a window with holes
    # can silently defuse them. Say how many, rather than leaving that to be discovered.
    if wanted:
        span = (date.fromisoformat(max(wanted)) - date.fromisoformat(min(wanted))).days + 1
        holes = span - len(wanted)
        if holes > 0:
            print(f"\n  [WARN] {holes} of the last {span} calendar days have NO run summary. "
                  "The streak detectors below reset across a gap, so they are weakened over "
                  "this window — read the per-run lines rather than trusting their silence.")

    # Whole-report checks: a single line can be individually correct while the pattern across
    # days is the actual defect. That is how both ERR-010 and ERR-017 survived.
    for extra in (cadence_anomaly(summaries), failure_streak(summaries)):
        if extra:
            print(f"\n  [FAIL] {extra}")
            failed += 1

    print()
    if failed:
        print(f"FAIL — {failed} failing condition(s) above.")
        return FAIL_EXIT
    if unreadable:
        # ANY unreadable object, not only "all of them". A confirmed FAIL still wins above, but
        # otherwise an unjudged run cannot be reported as OK: the failing run may be precisely
        # the object we could not open, and "I did not look" must never render as "nothing
        # wrong". The earlier `and not summaries` meant one readable sibling was enough to
        # print OK and exit 0 over an inaccessible summary.
        print(f"CANNOT JUDGE — {unreadable} summary/summaries could not be read, so this window "
              "is incomplete. Fix the access or the object, then re-run.")
        return CANNOT_JUDGE_EXIT
    print("OK (no failing condition found)")
    print("Actual request usage is on the RapidAPI dashboard — this script deliberately does "
          "NOT call JSearch to find out.")
    return OK_EXIT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
