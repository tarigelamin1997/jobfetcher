# ADR-0021 — M1: pipeline hardening (throughput · reliability · precision)

**Status:** Accepted · ✅ Validated live (v0.2.0, 2026-07-02)
**Date:** 2026-07-02
**Supersedes the M1 hypothesis:** the pre-drawn roadmap guessed **M1 = CV tailoring**. The P2 bottleneck protocol (run the tool, measure, rank by leverage) overruled that: the *first* real capability blocked was **the pipeline completing a full run at all**. So M1 became **pipeline hardening**. CV tailoring returns to the hypothesis list as a later migration.

## Context — three bottlenecks, measured on a live full sweep

The v0.1.0 pipeline works on the daily incremental (~10–30 jobs). Running the **full 18-query GCC sweep** (3 DE titles × 6 countries × 30-day window ≈ 155 postings) live on AWS surfaced three bottlenecks that block the tool's *real* capability — processing a backlog / a full market scan — plus a fourth operational hazard:

1. **Throughput.** Dissection ran as a **serial `for` loop** (`core/ingest.py`), ~40–50s per posting (a DeepSeek call + a Data-API write). 155 postings ≈ **~90 min** against the **15-min Lambda cap** → a run reached **silver ≈ 17 and timed out**; gold/score/notify never ran.
2. **Reliability.** `OpenAICompatLlmClient.complete()` made **one** HTTP attempt with **no retry**. A single DeepSeek `HTTP 503 "Service is too busy"` raised `LlmError` — and because `land_silver` caught only `DissectionError` (not `LlmError`), that error **propagated and killed the whole run** (`statusCode 500`). Isolation was asymmetric: `score_gold` already caught `LlmError`, `land_silver` did not. (→ [ERR-006](../ledgers/errors.md))
3. **Precision.** The gold `DeterministicFilterStrategy` title rule passed on **any single shared token**. Querying "Data Architect" passed *Azure Architect, Enterprise Architect, ArangoDB Architect, Alliances Manager, Computer Vision Engineer* straight to the expensive `deepseek-v4-pro` scorer (all scored ≤15). The scorer was the *only* real gate — paying an LLM call to reject each obvious mismatch. The already-built `LlmFilterStrategy` was not selectable (the handler hardcoded deterministic).
4. **Zombie retry (operational).** When the timed-out run failed, **AWS's default async invoke retried it**, re-fetching the whole sweep from scratch (JSearch quota + tokens burned on a run that would only time out again). (→ [ERR-007](../ledgers/errors.md))

## Decision

Ship **M1 = pipeline hardening** as three gate-trio build units (H-1 reliability, H-2 throughput, H-3 precision), release **v0.2.0**. Order deliberate: reliability first, because concurrency *amplifies* provider errors.

- **H-1 — retry + symmetric isolation.** `complete()` retries **only transient** failures (HTTP 429/500/502/503/504 + connection/timeout) with **exponential backoff + full jitter** (`LlmConfig.max_retries=3`, `backoff_base_s=1.0`, both config; 0 disables). Auth (401), model-not-found (404), and other 4xx **fail fast** — retrying them is waste. `land_silver` now catches `(DissectionError, LlmError)` → one bad posting or provider blip **skips one item, never the run** (symmetric with `score_gold`).
- **H-2 — bounded in-Lambda concurrency + deadline guard.** LLM calls (pure I/O) run on a **`ThreadPoolExecutor`** (default 8, `$PIPELINE_MAX_WORKERS`); **every DB write stays on the main thread** — the `sqlalchemy-aurora-data-api` dialect's thread-safety is never relied upon. A **`Deadline`** (from `context.get_remaining_time_in_millis()` − 60s) stops *starting* new LLM work before the timeout; the remainder is counted **`deferred`**, the summary carries **`partial: true`**, and **notify is skipped on a partial run** (the completing idempotent re-run sends the digest — protecting the send-once `run_log` guard from an incomplete shortlist). A run can no longer time out. Terraform: **`maximum_retry_attempts = 0`** (kills the zombie retry, ERR-007) + **memory 512→1024 MB** (Lambda CPU scales with memory; 8 threads + TLS).
- **H-3 — subset title match + selectable filter.** The deterministic rule now requires, for **some** target title, that **all** its meaningful tokens appear in the posting title (raw ∪ normalized): "Data Architect" needs `data` **and** `architect`. Semantic adjacency ("Analytics Architect") is deliberately *not* this filter's job — the **`LlmFilterStrategy` is now config-selectable** via `$GOLD_FILTER_STRATEGY = deterministic (default) | llm`.

## Measured before / after (live, revalidate01, 2026-07-02)

Re-ran the **exact ~132-posting backlog** the pre-fix code died on (same infra, same instrument = a 30s DB-count poll) → a clean A/B.

| # | Bottleneck | Pre-fix (measured) | Post-fix (measured) | Verdict |
|---|---|---|---|---|
| M1 | Throughput | silver ~17 in 15 min (**~1.1/min**), **timed out**, gold/score/notify = 0 | ~100 dissected in ~7 min (**~14–15/min**); full backlog drained; gold→score→notify completed | **~13× faster, no timeout** |
| M2 | Reliability | one 503 → `500`, whole run dead | **15 dissect + 0 score failures isolated** (skipped), `200`; a concurrent 2nd run overlapped — idempotency held, exactly **one** email | **proven** |
| M3 | Precision | 6/6 no-data-token junk passed gold | old junk **gone**; filter **dropped 18/32** silver; passes now genuinely token-match; semantic edge-cases scored ≤45 → **none surfaced** | **junk eliminated** |
| M4 | Zombie retry | AWS re-fetched a timed-out run | `MaximumRetryAttempts=0`, Terraform-managed (verified) | **fixed** |
| — | Product | "no matches" (thin Oman slice) | **21-job digest SENT** — real GCC DE roles scored 60–95 | **delivered** |

**Honest M3 caveat.** The live signal is partly muddied: pre-fix junk still sits in the DB as `status='scored'`, and the deterministic filter only re-evaluates `status='silver'` — so those old rows persist and can't be re-judged. The clean evidence M3 works is fourfold: (a) the six original junk titles are **absent** from the new-code batch; (b) the filter **dropped 18 of 32** silver in the new run; (c) a raw-title audit confirms every passing title genuinely contains a target's tokens; (d) the H-3 unit tests reject the exact six live junk titles as fixtures. The residual limitation — token-matching titles that are *semantically* off (e.g. raw "Sovereign Cloud & Data Center Engineer") — is by design handled by the **scorer backstop** (all scored ≤45, none surfaced) and, if a tighter gate is ever wanted, `GOLD_FILTER_STRATEGY=llm`.

## Rejected alternatives

- **Step Functions Map fan-out (one Lambda per posting) — now.** Premature at this volume (P1): in-Lambda threads clear the 155-job backlog in ≤2 runs and the daily 10–30 in ~2–3 min. Fan-out is the documented **M3-scale** path when volume/sources outgrow one Lambda; the account's ~10 concurrency ceiling would also cap it today.
- **SQS + worker fleet for the LLM calls.** More infra, more IAM, a queue to operate — unjustified for a single daily batch that a thread pool handles.
- **An external retry queue / DLQ for provider errors.** Retrying in-process with jitter is simpler and keeps the run self-contained; the deadline guard + idempotent resume already cover the "didn't finish" case.
- **LLM filter as the default gold gate.** Costs an LLM call per posting — the exact thing the deterministic pre-filter exists to avoid (P1). Kept as an opt-in for when semantic precision is worth the spend.
- **Unbounded concurrency.** DeepSeek tolerates it, but 8 is a safe, tunable default that fits the 1024 MB Lambda; the real resilience is H-1's retry, not the worker count.

## Consequences

- The pipeline **completes a full market sweep** and is safe to schedule on the daily backlog; partial runs resume idempotently instead of timing out or being blind-retried by AWS.
- A provider blip degrades gracefully (retry, then skip one item) instead of failing the run.
- Gold spends the pro-model only on genuinely title-matching postings; the scorer remains the semantic backstop.
- New config knobs: `PIPELINE_MAX_WORKERS`, `GOLD_FILTER_STRATEGY`, `LlmConfig.max_retries` / `backoff_base_s`. New Terraform: `aws_lambda_function_event_invoke_config` (retry=0), memory 1024.
- **Follow-on (next migration candidate):** the digest **email UX** — Tarig flagged the format as poor and the job links as not visible enough. Queued, not in v0.2.0.

Full reasoning trail: [01-session-decision-journal](../01-session-decision-journal.md) · plan §35–§36. Errors: [ERR-006, ERR-007](../ledgers/errors.md).
