"""`core.credit` — the rule behind the digest's LLM credit alert (INV-005). Pure, so every rung
is proven here without a handler: each positive is paired with the silence it must keep."""
from __future__ import annotations

import pytest

from jobfetcher.adapters.llm_openai import LlmBalance
from jobfetcher.core.credit import LLM_LOW_CREDIT_USD, CreditAlert, credit_problem

_HEALTHY = LlmBalance(9.71, True, None)
_UNKNOWN = LlmBalance(None, None, "timeout")
_CLEAN = {"billing_blocked": 0, "deferred": 0}


# ------------------------------------------------ rule (a): this run hit HTTP 402
def test_a_blocked_scoring_run_is_a_blocking_alert_whatever_the_balance_says():
    alert = credit_problem(_CLEAN, {"billing_blocked": 3}, _HEALTHY)
    assert alert is not None and alert.blocking
    assert alert.what == (
        "The LLM account is out of credit: 3 postings could not be processed (HTTP 402)"
    )
    assert "Top up the DeepSeek account" in alert.where


def test_a_dissection_only_block_banners_too():
    # VG-b: a fetch day whose only blocked work was dissection.
    alert = credit_problem({"billing_blocked": 5}, {"billing_blocked": 0}, _HEALTHY)
    assert alert is not None and alert.blocking and "5 postings" in alert.what


def test_both_stages_are_summed_and_one_posting_is_singular():
    assert "4 postings" in credit_problem({"billing_blocked": 1}, {"billing_blocked": 3}, None).what
    assert "1 posting could" in credit_problem({"billing_blocked": 1}, _CLEAN, None).what


def test_ordinary_per_item_failures_are_not_an_empty_account():
    # VG-b negative: `skipped` / `failed` are per-item failures (a bad JD, a flaky call).
    counts_ingest = {"skipped": 5, "billing_blocked": 0}
    counts_score = {"failed": 7, "billing_blocked": 0}
    assert credit_problem(counts_ingest, counts_score, _HEALTHY) is None


@pytest.mark.parametrize("reading", [None, _UNKNOWN, LlmBalance(None, None, "http_401")])
def test_rule_a_never_depends_on_the_balance_read(reading):
    # VG-c: a failed, missing or rejected read must never hide a run that was blocked.
    alert = credit_problem(_CLEAN, {"billing_blocked": 2}, reading)
    assert alert is not None and alert.blocking


@pytest.mark.parametrize("junk", [True, "3", 2.0, None, -4])
def test_counts_are_read_defensively(junk):
    # A summary read back from S3 or a stub: anything but a positive int is "not blocked".
    assert credit_problem({"billing_blocked": junk}, "not-a-dict", _HEALTHY) is None


# ------------------------------------------------ rule (b): the account reads empty
@pytest.mark.parametrize(
    ("reading", "shown"),
    [
        (LlmBalance(0.0, True, None), "($0.00 left)"),
        (LlmBalance(-0.30, True, None), "(-$0.30 left)"),
        (LlmBalance(5.00, False, None), "(DeepSeek reports it unavailable)"),
    ],
    ids=["zero", "negative", "unavailable"],
)
def test_an_empty_or_unavailable_account_is_blocking_with_nothing_to_retry(reading, shown):
    # Evidence 5(a): a non-fetch day with no blocked work — the counts are 0, the account is not.
    alert = credit_problem(_CLEAN, _CLEAN, reading)
    assert alert is not None and alert.blocking
    assert alert.what.startswith("The LLM account is out of credit ") and alert.what.endswith(shown)


# ------------------------------------------------ rule (c): the forecast
def test_below_the_threshold_is_a_low_credit_forecast_in_dollars_and_postings():
    alert = credit_problem(_CLEAN, _CLEAN, LlmBalance(1.99, True, None))
    assert alert == CreditAlert(
        "LLM credit low: $1.99 left (≈128 postings)",
        "Top up the DeepSeek account before the next job search",
        False,
    )


def test_todays_live_balance_banners():
    # INV-005: $0.54 on 2026-09-23, about 34 postings at the settled $0.0155.
    alert = credit_problem(_CLEAN, _CLEAN, LlmBalance(0.54, True, None))
    assert alert is not None and not alert.blocking
    assert "$0.54 left (≈34 postings)" in alert.what


@pytest.mark.parametrize("usd", [LLM_LOW_CREDIT_USD, 2.00, 9.71, 1000.0])
def test_at_or_above_the_threshold_is_silent(usd):
    # VG-d negative: strictly below. A banner that sits on every healthy day is furniture.
    assert credit_problem(_CLEAN, _CLEAN, LlmBalance(usd, True, None)) is None


def test_the_threshold_is_two_dollars():
    # Sized in the dossier (worst cycle $1.41 + 16% settlement lag = $1.64). Pinned so a change
    # is deliberate, not a typo.
    assert LLM_LOW_CREDIT_USD == 2.00


# ------------------------------------------------ rule (d): no usable reading
@pytest.mark.parametrize(
    "reading",
    [
        None,
        _UNKNOWN,
        LlmBalance(None, False, "no_usd"),
        LlmBalance(float("nan"), True, None),
        object(),
    ],
    ids=["none", "timeout", "unknown-but-unavailable", "nan", "not-a-reading"],
)
def test_a_failed_read_is_never_a_false_alarm(reading):
    # VG-c negative: with nothing blocked, an unknown balance announces nothing at all.
    assert credit_problem(_CLEAN, _CLEAN, reading) is None


# ------------------------------------------------ how the amount is displayed
@pytest.mark.parametrize(
    ("usd", "shown"),
    [(1.999, "$1.99 left"), (0.29, "$0.29 left"), (0.54, "$0.54 left"), (1.0, "$1.00 left")],
)
def test_the_amount_is_truncated_to_cents_never_rounded_up_to_the_threshold(usd, shown):
    # $1.999 is below $2.00 and must never display as "$2.00 left" under a "below $2" rule.
    alert = credit_problem(_CLEAN, _CLEAN, LlmBalance(usd, True, None))
    assert alert is not None and shown in alert.what


def test_a_negative_zero_balance_renders_as_zero():
    alert = credit_problem(_CLEAN, _CLEAN, LlmBalance(-0.0, True, None))
    assert alert is not None and alert.blocking and alert.what.endswith("($0.00 left)")
