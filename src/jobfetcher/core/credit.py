"""Can the LLM account still pay for today's work? The rule behind the digest's credit alert.

**What / Why / So-what.** *What:* one pure function that turns this run's `billing_blocked`
counts and a best-effort balance reading into a short `(what, where, blocking)` alert, or
`None`. *Why:* an empty DeepSeek account answers every call with HTTP 402; the pipeline counts
that (`billing_blocked`, ERR-010/ERR-011) and returns 200, and the digest then reads "no new
matches" — the same email a quiet market week produces (INV-005). The count made it
diagnosable; this makes it *announced*, in the digest the user already reads, the way
`core.intake` does for the JSearch sweep (B-12). *So-what:* the user learns scoring stopped on
the day it stops, and is warned about a cycle before it does.

**Rule (a) never depends on the balance read.** A run that was actually blocked banners from
its own counts, so a failed, slow or unsupported read can never hide it. The reading only adds
what the counts cannot see: a day with nothing to retry while the account is still empty, and
the forecast. A reading that failed (`usd is None`) adds nothing — it must never become a
false "low credit" alarm.

Pure: no I/O. The handler reads the balance (`OpenAICompatLlmClient.read_balance`) once, after
scoring, so the reading already reflects most of today's spend (B-10's settlement lag only ever
reads HIGH, which can under-warn slightly but never false-alarm).
"""
from __future__ import annotations

import math
from decimal import ROUND_DOWN, Decimal
from typing import Any, NamedTuple

# Warn below this many dollars. Sized from B-10's settled cost model (INV-005 Fix plan §8): the
# worst observed fetch cycle (91 gold x $0.0155 = $1.41) plus the settlement lag (reads up to
# 16% high) is $1.64, so $2.00 warns at least one full fetch cycle before exhaustion — about
# two cycles (~6 days) ahead at typical volume. $1 could warn on the day of exhaustion itself;
# $5 would sit on half of every $10 top-up and become furniture (INV-004). Compared in DOLLARS,
# because that is what the provider reports and what a top-up is paid in.
LLM_LOW_CREDIT_USD = 2.00

# Settled cost per scored posting, including the 1-in-5 3x boundary resample (B-10: 618
# postings, $9.57). Used ONLY to display "≈N postings" — a per-posting cost drifts with
# `reasoning_effort` (ADR-0037), which is why the threshold itself is in dollars.
LLM_COST_PER_POSTING_USD = 0.0155


class CreditAlert(NamedTuple):
    """WHAT is wrong and WHERE to act — the same short shape as `IntakeAlert` — plus whether it
    is a present failure (`blocking`) or a forecast, which decides the banner order."""

    what: str
    where: str
    blocking: bool


def _blocked(counts: Any) -> int:
    """A stage's `billing_blocked`, read defensively: anything but a positive int counts 0."""
    value = counts.get("billing_blocked", 0) if isinstance(counts, dict) else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _dollars(usd: float) -> str:
    """Cents TRUNCATED, never rounded: under a "below $2.00" rule, $1.999 must not display as
    "$2.00 left". Via `str` so a float like 0.29 (28.999... cents) keeps its cent; a zero of
    either sign renders "$0.00"."""
    cents = Decimal(str(usd)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return f"-${-cents}" if cents < 0 else f"${abs(cents)}"


def credit_problem(
    ingest_counts: Any, score_counts: Any, balance: Any, *, low_usd: float = LLM_LOW_CREDIT_USD
) -> CreditAlert | None:
    """The credit alert for today's digest, or `None` when the account is healthy or unknown.

    In order: (a) this run hit HTTP 402 → blocking, whatever the balance says; (b) the account
    reads empty (`usd <= 0`) or unavailable → blocking; (c) `0 < usd < low_usd` → a non-blocking
    low-credit warning; (d) otherwise `None`. With no usable reading only (a) can fire."""
    blocked = _blocked(ingest_counts) + _blocked(score_counts)
    if blocked:
        return CreditAlert(
            f"The LLM account is out of credit: {blocked} posting{'s' if blocked != 1 else ''} "
            "could not be processed (HTTP 402)",
            "Top up the DeepSeek account. Nothing else clears this; the next run resumes scoring",
            True,
        )
    usd = getattr(balance, "usd", None)
    if not isinstance(usd, (int, float)) or isinstance(usd, bool) or not math.isfinite(usd):
        return None  # no usable reading: a failed read is never a warning
    if getattr(balance, "available", None) is False or usd <= 0:
        return CreditAlert(
            "The LLM account is out of credit "
            + (f"({_dollars(usd)} left)" if usd <= 0 else "(DeepSeek reports it unavailable)"),
            "Top up the DeepSeek account. Nothing can be scored until then",
            True,
        )
    if usd < low_usd:
        return CreditAlert(
            f"LLM credit low: {_dollars(usd)} left "
            f"(≈{math.floor(usd / LLM_COST_PER_POSTING_USD)} postings)",
            "Top up the DeepSeek account before the next job search",
            False,
        )
    return None
