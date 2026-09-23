---
id: INV-005
title: When the LLM account runs out of credit, the daily run reports success and the digest says "no new matches"
status: in-progress     # open | verifying | verified | handoff-ready | in-progress | fixed | killed
severity: crucial       # rung 2 adds a new external call (DeepSeek GET /user/balance) to the unattended daily path. Rung 1 alone would be non-crucial.
logged: 2026-09-23
updated: 2026-09-23
source: orchestrator triage 2026-09-23 (live balance $0.54, with Tarig deciding not to top up so that 2026-09-25 becomes the live repro). Checked here against code, run summaries and CloudWatch
---

<!-- Case folder: docs/investigations/INV-005-silent-llm-credit-exhaustion/README.md · raw artifacts in ./evidence/ -->

# INV-005 · Silent LLM credit exhaustion

**Status:** `in-progress` (built + Examiner clean pass 2026-09-23; **merged (#80, `2f49894`) and deployed 2026-09-23 18:09 UTC**; awaiting live proof, a delivered digest showing the banner) · **Severity:** `crucial` (rung 2: a new external call) · **Owner of the fix:** a Surgeon, from the brief Tarig approved 2026-09-23 (see Resolution below)

## The problem

Every LLM call goes to DeepSeek: dissection on `deepseek-v4-flash` and scoring on `deepseek-v4-pro`. When the prepaid account runs dry, DeepSeek answers each call with **HTTP 402 "Insufficient Balance"**. The pipeline handles that well internally. The 402 gets its own error type, it is never retried, it is counted once as `billing_blocked`, and it is logged once at ERROR with the words "Top up the provider account". **Nothing outside the logs and `runs/*.json` ever reads that count.** The run returns `statusCode: 200`. No alarm is keyed on it. The digest still goes out, and because nothing new could be scored it reads *"No new matches since …"*, which is the same email a quiet market week produces.

So the product stops working while telling its one user that the market is quiet. It is the ERR-017 shape again: intake failed and looked like a quiet week, and here it is scoring. It has already happened once. The account ran dry on 2026-08-25 and stayed dry for 9 days, and nobody noticed that either, because ERR-010's 500s were hiding it (Evidence 4).

The live balance today is **$0.54, about 34 postings**. The next fetch day is 2026-09-25, and Tarig has decided not to top up.

## Does it exist? — verification

All live reads were made read-only on 2026-09-23 as `jobfetcher-dev` (profile `jobfetcher`, us-east-1, account `198592435375`). No Lambda was invoked and nothing was written to AWS. The API key was piped between processes and never printed. Raw artifacts are in [`evidence/`](evidence/).

- **Evidence 1: a 402 becomes a counter and nothing else (code).**
  - [`adapters/llm_openai.py:161-162`](../../../src/jobfetcher/adapters/llm_openai.py) maps `e.code == 402` to `LlmBillingError`. It is checked before the model-not-found heuristic and is **never retried**, because 402 is not in `_RETRYABLE_STATUSES` (line 35).
  - [`core/ports.py:70-78`](../../../src/jobfetcher/core/ports.py) defines `LlmBillingError(LlmError)`. `Dissector` and `Scorer` catch only `DissectionError`/`JSONDecodeError`/`ValidationError` (`core/dissector.py:140`, `core/scorer.py:193`), so the 402 passes through them unwrapped.
  - Dissection: [`core/ingest.py:362-365`](../../../src/jobfetcher/core/ingest.py) returns the `_BILLING_BLOCKED` sentinel (line 223). The loop at 480-481 counts it, 502-509 logs one ERROR line, and 597 puts it in the summary as `ingest.billing_blocked`.
  - Scoring: `core/ingest.py:851-856` returns the sentinel, 869-871 counts it, 919-926 logs one ERROR line, and 933 puts it in the summary as `score.billing_blocked`.
  - **Nothing reads it.** Reproduce: `grep -rn "billing_blocked" --include=*.py src/ scripts/ | grep -v core/ingest.py`, which should print **nothing**. `handlers/pipeline.py`, `core/intake.py`, `core/notifier.py` and `scripts/check_ingestion.py` never look at the key.
  - **No alarm matches it.** The only log-metric filter is `pattern = "\"PIPELINE_ALARM\""` ([`terraform/alarms.tf:103`](../../../terraform/alarms.tf)), which is emitted only on the returned-500 path (`handlers/pipeline.py:738-739`). A billing-blocked run returns 200.

- **Evidence 2 (the key question): a billing-blocked run still SENDS the digest.** `partial = bool(ingest_counts.get("deferred") or score_counts.get("deferred"))` at [`handlers/pipeline.py:641`](../../../src/jobfetcher/handlers/pipeline.py). **`billing_blocked` is not part of `partial`, and a blocked item is never counted as `deferred`.** The three sentinels are distinct: `_DEFERRED` (217), `_BILLING_BLOCKED` (223), and `None`, which means failed. So a run that is blocked and not deferred takes the `else` branch at 692-728: `notify(...)` then `mark_digest_sent`. `notify` itself never looks at stage counts (`core/ingest.py:1159-1291`). With 0 new items the renderer takes the zero-match path at `core/notifier.py:374-402`, and its subject is `JobFetcher — no new matches since {since_day} ({day})` (line 386).
  - **Consequence for the fix:** a banner in the digest **will be delivered**. No change to the send path is needed.
  - **The one exception:** if the same run also hit the H-2 deadline (`deferred > 0`), `partial` is true and notify is skipped (681-688). The next day's run sends. See Evidence 3 for why a blocked run is fast and rarely deferred.

- **Evidence 3: a blocked run is fast, with no short-circuit and no deadline pressure.** Nothing stops the loop at the first 402. Every remaining item still makes one HTTP round-trip that fails immediately (`_score_task` at `core/ingest.py:837-859` has no shared "stop" flag). Measured live: the `drain002` run on 2026-09-02 took **618 scoring 402s in 23 seconds** (08:58:32Z → 08:58:55Z) and returned `200, partial: false` ([`evidence/run-2026-09-02-drain002.json`](evidence/run-2026-09-02-drain002.json), [`evidence/insufficient-balance-log-events.txt`](evidence/insufficient-balance-log-events.txt)). 402s cost no inference, so this wastes nothing. It also means **a blocked run reaches notify well inside the 700 s deadline**, unless credit runs out *late* in a long, slow scoring stage.
  Reproduce:
  ```bash
  export MSYS_NO_PATHCONV=1
  aws logs filter-log-events --log-group-name /aws/lambda/jobfetcher-dev-pipeline \
    --start-time $(python -c "import time;print(int((time.time()-60*86400)*1000))") \
    --filter-pattern '"Insufficient Balance"' --query 'events[].timestamp' --output json \
    | python -c "import sys,json,collections,datetime as d;print(sorted(collections.Counter(d.datetime.utcfromtimestamp(t/1000).date().isoformat() for t in json.load(sys.stdin)).items()))"
  ```
  Expected: `[('2026-08-25', 124), …, ('2026-09-01', 120), ('2026-09-02', 618)]`, **1,672 events** in total. The window slides, so after ~2026-10-24 the August days fall out; the saved evidence file is the record.

- **Evidence 4: it has happened before, and nobody noticed for 9 days.** The first 402 was at **2026-08-25T06:02:27Z** (`run_id=d1f89821`) and the last at 2026-09-02T08:58:55Z. The verbatim body is `HTTP 402: {"error":{"message":"Insufficient Balance","type":"unknown_error","param":null,"code":"invalid_request_error"}}` (same file). Those runs were also dying of ERR-010 (500s), which is why nobody saw it. [ERR-010](../../ledgers/errors.md) records the second fault in its own words: *"Either fault alone was enough to break the tool."* Only the first fault paged, and the alarm fired 38 times without anyone acting on it. **Take ERR-010 away and the credit failure alone would have looked like a green run and a "no new matches" email every day.**

- **Evidence 5: blocked work is retried by every run, so the condition persists daily until a top-up.**
  - *Scoring:* `get_gold_candidates` reads `status == "gold_candidate"` ([`adapters/repository_postgres.py:406-408`](../../../src/jobfetcher/adapters/repository_postgres.py)), and `mark_scored` (679) runs **only on success** (`core/ingest.py:899`). Every run, fetch day or not, therefore re-attempts every blocked candidate. Live proof of the retry mechanism: 2026-09-22 left `score.deferred: 35`, and 2026-09-23, a non-fetch day, scored exactly those 35 (`score.gold: 35, scored: 35`) ([`evidence/run-2026-09-22-19c08edf.json`](evidence/run-2026-09-22-19c08edf.json), [`evidence/run-2026-09-23-4e18a4b1.json`](evidence/run-2026-09-23-4e18a4b1.json)).
  - *Dissection is **not** retried.* `ingest()` dissects only what **this run** fetched (`core/ingest.py:444-452`). On a non-fetch day `landed` is empty (426-427). A posting whose dissection was 402'd stays bronze-only, and it is recovered only if a later sweep happens to fetch it again (`get_posting` is still `None`, so it is re-dissected). No bronze→silver backfill exists: `grep -rn "unsilvered\|backfill" src/` returns nothing relevant. **Two consequences follow.** (a) A day whose only blocked work was dissection shows `billing_blocked > 0` on the fetch day and **0 on the two days after**, so a banner driven only by the counter would flicker. Rung 2 closes that. (b) Postings that were never re-fetched are lost to silver for good. That is a real data gap, but it is **out of scope** here (see Out of scope).

- **Evidence 6: the live balance, and the shape of the endpoint.**
  ```
  GET https://api.deepseek.com/user/balance   (2026-09-23T14:34Z, HTTP 200, 0.57–0.81 s)
  {"is_available":true,"balance_infos":[{"currency":"USD","total_balance":"0.54","granted_balance":"0.00","topped_up_balance":"0.54"}]}
  ```
  Saved as [`evidence/deepseek-balance-2026-09-23T1434Z.json`](evidence/deepseek-balance-2026-09-23T1434Z.json). Amounts are **strings**, `balance_infos` is a **list keyed by currency**, and the response carries an `is_available` flag. A **bad key returns 401**, and its body **echoes the key's last four characters**: `"Authentication Fails, Your api key: ****d000 is invalid"` (measured with a dummy key). This bears on secret hygiene (Mechanism, last paragraph).
  Reproduce (the key goes into a shell variable and is never echoed):
  ```bash
  K=$(aws secretsmanager get-secret-value --secret-id jobfetcher/deepseek --query SecretString --output text \
      | python -c "import sys,json
  r=sys.stdin.read().strip()
  try: d=json.loads(r); print(d.get('api_key') or d.get('apiKey') or r)
  except Exception: print(r)")
  curl -s -H "Authorization: Bearer $K" https://api.deepseek.com/user/balance; unset K
  ```

- **Evidence 7: the deployed build is the one analysed.** `aws lambda get-function-configuration --function-name jobfetcher-dev-pipeline` gives `LastModified 2026-09-23T10:23:25Z`, `CodeSha256 9JcEtlkm…` (the B-12 deploy), `Timeout 900`, `PIPELINE_MAX_WORKERS 8`, `JOBFETCHER_FETCH_EVERY_N_DAYS 3`. The schedule is `cron(0 6 * * ? *)`. `is_fetch_day` is true for 2026-09-22, **09-25** and 09-28 (computed with `core.ingest.is_fetch_day`).

- **Magnitude.**
  - **Frequency:** one live occurrence already (9 days, 1,672 blocked calls), and the next is imminent. $0.54 covers about 34 postings at the settled **$0.0155/posting** ([B-10](../../ledgers/backlog.md)). How many new postings 2026-09-25 brings is **estimated, not measured**. In the daily-sweep era, runs one day apart re-found 66–71% of what they fetched (07-23: 100 of 151 `already`; 07-24: 104 of 146), and 64–77% of new postings went gold. A 3-day gap means less overlap, so roughly 40–60 new gold, which would cost **$0.62–0.93 against $0.54**. **Exhaustion on 09-25 is likely but not certain.** If it does not happen, 09-25 ends well under the threshold proposed below and exhaustion moves to 09-28.
  - **Effect:** no new job can be scored until a human tops up the account. Every day of that is invisible from the inbox.
  - **Duration:** unbounded. Nothing ends it except someone reading `runs/*.json` or CloudWatch.

## Mechanism (root cause)

**The symptom** is a green run and an ordinary-looking "no new matches" digest while no job can be scored.

**The cause** is that the billing failure was made *legible* by ERR-010/ERR-011, which added the type, the sentinel, the counter and the one ERROR line, but it was never made *announced*. The counter stops at `core/ingest.py:597` / `:933`. The handler reads `ingest_counts` and `score_counts` only to compute `partial` (`handlers/pipeline.py:641`) and to write the summary (757-766). The one surface the user reads, the digest, gets `stale_days` and `intake_alert` (`core/ingest.py:1259-1263`) but nothing about the LLM account. This is the same gap INV-003 found for intake ("diagnosable, not announced"). There, B-12 closed it with the digest banner. Here nothing has closed it yet.

**Why no existing signal catches it:**

| Signal | Why it misses a credit-blocked run |
|---|---|
| Dead-man alarm | The cron still fires |
| Lambda `Errors` alarm | No exception escapes: every 402 is caught per item |
| `PIPELINE_ALARM` / returned-500 (INV-002) | The run returns 200 |
| B-12 intake banner (`core/intake.py`) | It keys only on `ingest.fetch_stopped`. The sweep itself is healthy, so it stays silent, correctly |
| INV-004 staleness banner (`core/notifier.py:227-262`) | A digest **is** delivered every day, so `days_since_last_digest` stays at 1 and never reaches the threshold of 3 |
| The ERROR log line | Correct and precise, but it is a log line. ERR-010 showed that even a paging alarm went ignored for 38 days |

**Q2: 401 (`LlmAuthError`) and 404 (`LlmModelNotFoundError`) are quiet too, but the fix is not the same.** Neither gets a sentinel. `_prepare_silver` catches them as `LlmError`, logs a WARNING **per item**, and counts them as `skipped` (`core/ingest.py:366-368`). `_score_task` does the same and counts them as `failed` (857-859). So a revoked key also produces a 200 and an unbannered digest. The rung-1 fix, however, reads an **existing** counter (`billing_blocked`). Covering 401/404 would need a new run-wide classification, which means new sentinels, new summary keys, and a change to the per-item isolation semantics. **They are out of scope under the rule "include only if the fix is identical".** One cheap seam is built in anyway: rung 2's balance read uses the same key, so a revoked key shows up as `llm_balance_error: "http_401"` in the run summary. That is forensic evidence only, with no banner. See Out of scope.

**Where a balance read may live without polluting the port (ADR-0012/0017).** `LlmClient` (`core/ports.py:81-95`) is a provider-neutral `complete()` Protocol. `/user/balance` is **DeepSeek-specific**: OpenAI has no such endpoint and OpenRouter uses a different path and shape. Adding `balance()` to the Protocol would oblige every future provider to fake one. The concrete adapter `OpenAICompatLlmClient` already holds everything needed:
- `config.base_url` (`https://api.deepseek.com`, `config.py:21`);
- lazy, lock-guarded key resolution through `_key()` (`llm_openai.py:67-76`), which reads `$DEEPSEEK_API_KEY` or Secrets Manager `jobfetcher/deepseek` through `_resolve_api_key` (38-56), with no second secret fetch;
- stdlib `urllib` (no new dependency).

So the balance read belongs **on the concrete adapter as an optional capability**, reached by the handler (the composition root) through `getattr(client, "read_balance", None)`. That is the precedent INV-003 set for `last_stop_reason` (`core/ingest.py:527`): the port stays unchanged, and an adapter without the method simply reports nothing. A useful side effect is that the existing handler unit tests stub `OpenAICompatLlmClient` as a bare `object()` (`tests/test_handler_cadence.py:90-92`), so they automatically get "no balance, no network call" and need no edits to stay offline.

**B-10's settlement-lag caveat decides *when* to read.** DeepSeek's balance settles late: a read taken straight after the 2026-09-02 drain was **16% high** ($1.48 against a settled $0.14). The lag only ever makes the reading **too high**, never too low. The read should therefore come **after the score stage and before notify**. There it already reflects most of today's spend, and the lag can only cause a slight *under*-warning, never a false alarm. A read at the start of the run would miss all of today's spend. That is a full cycle late on a fetch day.

**Secret hygiene: what exists today.** The key is resolved in `_resolve_api_key`/`_key()` and is never logged (module docstring, `llm_openai.py:5-6`). It travels only in the `Authorization` header (line 102). Logging goes through the package logger and the handler's `LoggerAdapter` (`handlers/pipeline.py:419`). One pre-existing exposure applies to any new call: **DeepSeek's 401 body echoes the key's last four characters** (Evidence 6), and the current `complete()` path puts that body into `LlmAuthError` (line 158), which `_prepare_silver` logs per item. The new balance read must **never log or record a response body or a request header**, only a status code or an exception class name.

## Blast radius

- **Changes (rungs 1 + 2):**
  - `src/jobfetcher/adapters/llm_openai.py`: **add** one method, `read_balance()` (sketched below). `complete()`, `_request_with_retries()` and the error mapping are untouched.
  - `src/jobfetcher/core/credit.py`: **new**, pure, no I/O. It turns `(billing_blocked total, balance reading)` into a credit alert or `None`, the same way `core/intake.py` does for the sweep.
  - `src/jobfetcher/core/notifier.py`: `render_digest(..., credit_alert=None)` and `_with_banners` order the alerts and choose the one `⚠` subject lead.
  - `src/jobfetcher/core/ingest.py`: `notify(..., credit_alert=None)` threads the value through to `render_digest`, exactly as `intake_alert` is threaded (1170, 1262). **Nothing else in this file changes.**
  - `src/jobfetcher/handlers/pipeline.py`: keep a reference to the scorer's client (currently built inline at 523-525), read the balance after `stage=score`, build the alert, pass it to `notify`, and record `llm_balance_usd` (+ `llm_balance_error`) in the run summary (757-766).
  - Tests: `tests/test_llm_openai.py`, a new `tests/test_credit.py`, `tests/test_notifier.py`, and a handler-level test beside `tests/test_handler_cadence.py`.
- **Must NOT change:**
  - The `LlmClient` Protocol (`core/ports.py:81-95`). **No `balance()` on the port.**
  - `complete()`'s request, retry and error-mapping behaviour, including 402 → `LlmBillingError`, fail-fast, checked before the 404 heuristic (`test_402_*` in `tests/test_llm_openai.py` must pass **unmodified**).
  - The `_BILLING_BLOCKED` sentinel, its once-only ERROR line, and the `billing_blocked` summary keys and their meaning (`test_ingest_billing_*` unmodified).
  - The **`partial` definition** (`handlers/pipeline.py:641`) and the **send-once guard** (`was_digest_sent` / `mark_digest_sent`). A blocked-and-deferred run still skips notify, and the next run sends.
  - **Scoring semantics:** thresholds, bands, the boundary resample (ADR-0031), `profile_hash`, and what counts as scored or failed.
  - **B-12:** `core/intake.py` logic and its banner wording. **INV-004:** the staleness banner and its threshold. Every existing intake and staleness test passes unmodified.
  - Schema and migrations (no DB touch), all of `terraform/` (same secret, same egress, no new env var or IAM), the capture endpoint (`handlers/capture.py`), `{"mode":"smoke"}` (keeps **zero side effects**, so no balance read there), and `{"mode":"reassess"}` (no read, no digest).
- **Unaffected:** JSearch fetch and cadence, the gold filter, the S3 audit and full-list report, `scripts/check_ingestion.py` (it reads `ingest` blocks, and a new top-level summary key is additive).

## Fix plan (the handoff guideline)

**Approved scope: rung 1 (announce) + rung 2 (forecast).** Tarig decided this on 2026-09-23. Everything else is out of scope.

**Rung 1 — announce a blocked run in the digest it already sends (`src/` only).**
1. `core/credit.py` gets `credit_problem(ingest_counts, score_counts, balance, *, low_usd=LLM_LOW_CREDIT_USD) -> CreditAlert | None`. `CreditAlert` is a `NamedTuple(what, where, blocking: bool)`, the same *what + where* shape as `IntakeAlert` (`core/intake.py:48-53`) plus one flag that sets ordering. Rule (a): if `ingest.billing_blocked + score.billing_blocked > 0`, return a **blocking** alert, e.g. *what* = "The LLM account is out of credit: N postings could not be processed (HTTP 402)", *where* = "Top up the DeepSeek account. Nothing else clears this; the next run resumes scoring". **Rule (a) must not depend on rung 2:** a failed balance read must never hide it. Read the counts defensively (`.get(..., 0)`, ints only), like `ingest`'s `getattr` reads.
2. `core/notifier.py`: add `credit_alert` to `render_digest`, and have `_with_banners` render it with the existing `_intake_banner` (text + HTML, escaped). No new banner markup is needed.
   - **Order:** blocking credit, then intake, then non-blocking (low) credit, then staleness. A present failure outranks a forecast, and a cause outranks a symptom (INV-004).
   - **Subject:** `⚠` appears exactly once and leads with the *what* of the first alert in that order.
   - **Scope:** both return paths (zero-match 398-402 and main 465-468), because the one-helper rule exists to stop the two paths diverging (Examiner B3).
3. `core/ingest.py::notify`: add `credit_alert=None` and pass it through. Reuse point: the `intake_alert` threading at 1170/1262.
4. `handlers/pipeline.py`: after `stage=score done`, compute the alert (a small guarded `_credit_alert_for_digest(...)`, modelled on `_intake_alert_for_digest` at 337-398: best-effort, logs a WARNING carrying the user-facing words, and never raises). Pass it to `notify` in the send branch (707-724).

**Rung 2 — forecast from a best-effort balance read, once per run.**
5. `adapters/llm_openai.py`: add `OpenAICompatLlmClient.read_balance(*, timeout_s: float = 5.0) -> LlmBalance`, where `LlmBalance` is `NamedTuple(usd: float | None, available: bool | None, error: str | None)`.
   - **Request:** a single `GET {base_url}/user/balance` with the `_key()` bearer. **No retry** (it is best-effort, and retries only add latency). 5 s timeout (measured at 0.57–0.81 s).
   - **Parsing:** find the `balance_infos` entry whose `currency == "USD"`, turn `total_balance` from a string into a float, and read `is_available`.
   - **Never raises:** every failure returns `usd=None` with a short `error` class: `timeout`, `http_<code>`, `bad_json`, `no_usd`, `no_key`, `error:<ExceptionClass>`. **It never includes a response body or a header.**
   - **Non-DeepSeek hosts:** they 404, which is recorded as `http_404` with no banner. That keeps it portable.
6. `handlers/pipeline.py`: keep the scorer's client (`score_llm = OpenAICompatLlmClient(_score_llm_config())`) and call `getattr(score_llm, "read_balance", None)` **once**, after the score stage. Wrap it in `try/except Exception`, so the read can never produce a 500. Record top-level `"llm_balance_usd"` and `"llm_balance_error"` in the summary on **every** normal-mode run that reaches that point, including partial and already-sent runs. The INV-003 lesson applies: a `null` must say why.
7. `core/credit.py`, same function, rules after (a):
   - (b) If `balance.available is False` or `balance.usd <= 0`, return a **blocking** alert ("…out of credit ($0.00 left)"). This covers the days with nothing to retry that Evidence 5(a) describes.
   - (c) If `0 < balance.usd < LLM_LOW_CREDIT_USD`, return a **non-blocking** alert: *what* = "LLM credit low: $0.54 left (≈34 postings)", *where* = "Top up the DeepSeek account before the next job search".
   - (d) Otherwise, `None`. **If `balance` is `None` or `usd` is `None`, only rule (a) can fire, and a failed read must never produce a low-credit banner.**
8. **The threshold: `LLM_LOW_CREDIT_USD = 2.00`, compared in dollars and displayed as dollars plus ≈postings.**

   | Input | Value | Source |
   |---|---|---|
   | Cost per scored posting (settled, includes the 1-in-5 3× boundary resample) | **$0.0155** | [B-10](../../ledgers/backlog.md) (618 postings, $9.57) |
   | Worst observed fetch-cycle gold | **91** | `runs/2026-09-22` (after a 3-week gap, so an upper bound) |
   | Worst cycle spend | 91 × 0.0155 = **$1.41** | |
   | + settlement lag (reads up to 16% high) | **$1.64** | B-10 |
   | Typical cycle (~50 gold) | **~$0.78**, ~10 cycles/month ≈ **$7.8/month** | consistent with B-10's $5–14/month |

   **$2.00 is above the worst observed cycle plus lag**, so the warning appears at least one full fetch cycle before exhaustion. At typical volume it appears about two cycles (~6 days) ahead. **$1** would cover only ~64 postings, less than a bad fetch day, so the warning could arrive on the day of exhaustion itself. **$5** would light up for the last half of every $10 top-up, which turns the banner into furniture (INV-004's rule). Compare in **dollars**, because dollars are what the provider reports and what a top-up is paid in, and a per-posting cost drifts with `reasoning_effort` (B-10(d), ADR-0037). Display **≈postings** as `floor(usd / 0.0155)`, from a named constant that cites B-10, so "$0.54" reads as capacity. Label it "≈", because it is an estimate and not a guarantee. Today's $0.54 would banner **correctly**.

> **Sequence:** 5 → 7 → 1 → 2 → 3 → 4/6. Build the pure pieces first (the adapter method and `credit.py`, both unit-testable offline), then the render, then the wiring. **Unit tests must never reach the network.** Every test either stubs the client or monkeypatches `urllib.request.urlopen`, as `tests/test_llm_openai.py` already does. **Do not run the integration suite without the project's DB URL** (see the memory note on `JOBFETCHER_DB_URL`). Also check whether `tests/test_integration_handler.py` builds a real `OpenAICompatLlmClient`. If it does, stub `read_balance`, or the suite will make a live DeepSeek call.

## Validation gate

Every row is behavioural (driven through the real function or handler interface) and carries a negative case. **Prove each gate by making it fail:** apply the mutation named in VG-j, watch the suite go red, and restore it ([ERR-015](../../ledgers/errors.md)/[ERR-016](../../ledgers/errors.md) standard).

| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | **Blocked run → digest SENT and it leads with the credit banner.** Handler test: `score_gold` returns `billing_blocked: 3, deferred: 0`, and the fake notifier captures the send. Assert statusCode 200, exactly one send, a subject that starts `⚠` and contains the credit *what*, a text body whose **first** line is the credit banner (also in the HTML), and `mark_digest_sent` called. | **Clean run** (`billing_blocked: 0`, balance $9.71) → no credit banner, subject unchanged. Every existing digest, intake and staleness test passes **unmodified**. |
| VG-b | **Dissection-only block banners too:** `ingest.billing_blocked: 5`, `score.billing_blocked: 0` → blocking banner. | `ingest.skipped: 5` (ordinary dissection failures, not 402) → **no** credit banner. A per-item failure is not an empty account. |
| VG-c | **Balance-read failure is never fatal:** `read_balance` raises, times out, returns non-JSON, returns JSON with no USD entry, returns HTTP 401 or 404, **or the client lacks the method** (`object()`). In every case the run returns 200, the summary has `llm_balance_usd: null` and a non-empty `llm_balance_error`, and the digest sends. | In all of those cases **with `billing_blocked: 0`**, there is **no** credit banner, so a read error never becomes a false alarm. **And** `billing_blocked > 0` with a failed read **still** banners, so rule (a) never depends on rung 2. |
| VG-d | **Threshold, both sides:** $1.99 → non-blocking "low" banner showing "$1.99" and "≈128". $0.00, $-0.30, or `is_available: false` → **blocking** wording. | **$2.00 → no banner** (strictly below). $9.71 → no banner. |
| VG-e | **Co-existence and order:** blocking credit + intake + staleness ≥ 3 → the subject leads with the credit *what*, `⚠` appears exactly once, and the body order is credit, intake, staleness. **Low** credit + intake → the subject leads with **intake**, and low credit renders after it. | No alerts → no `⚠`, no banner block. An intake-only render is byte-identical to today's. |
| VG-f | **Persists while blocked, clears after top-up.** Three consecutive handler runs with a fake LLM that 402s, candidates left `gold_candidate`, balance $0 → the banner is in all three digests. Day 2 is a non-fetch day with **nothing to retry**, and `balance.usd == 0` still banners through rule (b). | "Top-up": the fake LLM succeeds and the balance reads $10 → the next digest has **no** credit banner. A $1.50 top-up → **low** banner, as intended. |
| VG-g | **The secret never leaks.** Set the key to a sentinel `sk-SENTINEL-abcd1234` and make the balance endpoint return 401 with DeepSeek's real echo shape (`"Your api key: ****1234 is invalid"`), then separately a timeout. | Assert neither `SENTINEL` nor `1234` appears in any `caplog` record, in the JSON-serialised run summary, or in the rendered subject, HTML or text. |
| VG-h | **The send-once and partial rules are untouched:** `deferred: 2` + `billing_blocked: 4` → notify **skipped** (unchanged), with `llm_balance_usd` still recorded. The next run (blocked, not deferred) sends **with** the banner. | A second run on the same `run_date` does **not** send twice. |
| VG-i | **Smoke and reassess make no balance call:** `{"mode":"smoke"}` and `{"mode":"reassess"}` never call `read_balance` (spy), and the smoke summary shape is unchanged. | A normal-mode run calls it **exactly once**. |
| VG-j | **Mutation proof:** delete `credit_alert=credit_alert` from the `notify(...)` call, and separately from `notify`'s `render_digest(...)` call. VG-a must go red each time. | Swap the order in `_with_banners` → VG-e goes red. |

**Live proof (a human runs the deploy; the Investigator and Surgeon never invoke the Lambda):**

*(Rewritten 2026-09-23: this timeline assumed a 2026-09-24 deploy, with 09-24 as an undeployed baseline and 09-25 as the BEFORE repro. The fix was deployed on 2026-09-23 at 18:09 UTC, before the 09-24 run, so there is no live BEFORE repro. The 9-day 2026-08-25 episode ([ERR-019](../../ledgers/errors.md), Evidence 4) is the before.)*

1. **2026-09-24 (the first run on the fix; the forecast proof).** Not a fetch day, with nothing pending (09-23 scored all 35), so the balance should still read about $0.54. Expect `llm_balance_usd` ≈ 0.54, `llm_balance_error: null`, `billing_blocked: 0`, and a delivered digest carrying the **low-credit** banner, `⚠ LLM credit low: $0.54 left (≈34 postings)…`. The [B-12](../../ledgers/backlog.md) `partial_errors` banner may fire the same day (B-12's own proof). If it does, it renders first and the subject leads with the intake words, because a low-credit warning sits after intake (VG-e).
2. **2026-09-25 (the fetch day; expected to run out).** About 34 postings of credit against a full sweep's gold, so the account should run out partway through scoring. That makes this day step 3 below. Save `runs/2026-09-25/*.json`, the `LLM ACCOUNT OUT OF CREDIT` ERROR line, and **a balance read ~30 min after the run**, which settles what `is_available` and `total_balance` read at exhaustion (rule (b) handles either). If the credit is *not* exhausted, expect the low-credit banner again, record the balance, and the repro moves to 09-28.
3. **The first blocked run after deploy, still unfunded (the AFTER proof; expected 2026-09-25, else 09-28).** If the fetch day does not exhaust it, any later day works, because unscored gold is retried every run (Evidence 5). Expect `llm_balance_usd` ≈ 0 (or negative), `billing_blocked > 0`, and a **delivered** digest whose subject begins `⚠ The LLM account is out of credit…` with the banner on top. **The proof is the email in the inbox, not the summary.** The post-deploy `{"mode":"smoke"}` returned `200` on 2026-09-23; that it made no balance call is covered by VG-i, not yet read from its log.
4. **Persistence:** the next day's digest carries it again, including a non-fetch day.
5. **Post-top-up negative:** after a top-up of **≥ $10**, the next run shows `billing_blocked: 0`, `llm_balance_usd ≥ 2`, and **no** credit banner. The expected `llm_balance_usd` reads high by the settlement lag, which is fine.

## Out of scope / rejected

- **A `balance()` method on the `LlmClient` Protocol.** Rejected because it breaks provider neutrality (ADR-0012/0017). The capability stays on the concrete adapter, reached through `getattr`.
- **Banners for 401/404 (`LlmAuthError` / `LlmModelNotFoundError`).** They are equally quiet, but the fix differs: new sentinels and a change to per-item semantics. Rung 2 records `llm_balance_error: "http_401"` as forensic evidence. **Follow-up candidate** for the backlog: a run-wide "key rejected" count and banner, which would reuse `credit.py`.
- **Short-circuiting after the first 402.** An optimisation, not a fix. 618 blocked calls took 23 s and cost nothing (Evidence 3).
- **Re-dissecting bronze-only postings that were 402'd.** A real data-recovery gap (Evidence 5): postings that are never re-fetched never reach silver. It is a separate bottleneck, and the Scribe should log it to the backlog. A fix would touch ingest semantics and repository reads.
- **A CloudWatch alarm or SNS on a published balance metric** (B-10(c)). Rejected per INV-004: a fourth alarm in an inbox that ignored 29 makes things worse, and the digest is the surface that gets read. It would also be live infra.
- **A balance check in `{"mode":"smoke"}`** (B-10(b)). Smoke's contract is zero side effects, and it runs only at deploy, not daily.
- **Per-run token and cost accounting** (B-10(a)) and **changing `reasoning_effort`** (B-10(d)). Separate decisions.
- **A B-12-style look-back over earlier summaries.** Rule (b) (balance ≤ 0) already covers the days with nothing to retry, so a look-back adds code for a case that is covered.
- **Changing `partial`, the send-once guard, or making a blocked run fail (500).** A 500 would stop the digest, which is the one surface that works, and would page an alarm that INV-004 showed goes ignored.
- **Anything in `docs/` beyond this dossier and its index row.** The Investigator is read-only by [ADR-0034](../../adr/0034-investigation-dossier-system.md).

## Connections (typed — the graph seam)

- `causes` → daily `statusCode: 200` runs and a "no new matches" digest while no job can be scored (live 2026-08-25 → 09-02, masked by ERR-010)
- `caused-by` → `file:src/jobfetcher/core/ingest.py` (`billing_blocked` counted at :481/:870 and logged at :502-509/:919-926, but never passed to the digest)
- `caused-by` → `file:src/jobfetcher/handlers/pipeline.py` (the notify wiring :692-728 carries intake and staleness, but no credit signal)
- `caused-by` → an external DeepSeek HTTP 402 "Insufficient Balance" *(the operator action is a top-up)*
- `touches` → `file:src/jobfetcher/adapters/llm_openai.py` (add `read_balance`)
- `touches` → `file:src/jobfetcher/core/credit.py` (new, pure)
- `touches` → `file:src/jobfetcher/core/notifier.py` (`_with_banners`, `render_digest`)
- `touches` → `file:src/jobfetcher/core/ingest.py` (`notify` threads `credit_alert` only)
- `touches` → `file:src/jobfetcher/handlers/pipeline.py`
- `depends-on` → external `GET https://api.deepseek.com/user/balance` (read-only, same key)
- `blocks` → the daily shortlist, the tool's entire product
- `relates-to` → [INV-003](../INV-003-silent-fetch-stop/README.md) (same shape for intake: legible but not announced; the `getattr` optional-capability precedent)
- `relates-to` → [INV-004](../INV-004-alarm-escalation/README.md) (digest, not alarm; the staleness banner this must co-exist with)
- `relates-to` → [B-12](../../ledgers/backlog.md) (the intake banner and `_with_banners` path reused here)
- `relates-to` → [B-10](../../ledgers/backlog.md) (the $0.0155/posting cost model and the settlement lag that decides when to read)
- `relates-to` → [ERR-010](../../ledgers/errors.md) (the first live 402 episode; introduced `LlmBillingError` + `billing_blocked`)
- `relates-to` → [ERR-011](../../ledgers/errors.md) (extended the 402 treatment to scoring: `failed: 618` → `billing_blocked`)
- `relates-to` → [ERR-014](../../ledgers/errors.md) (balance also scales concurrency: a low balance throttles before it blocks)
- `relates-to` → [ERR-017](../../ledgers/errors.md) (the same "green but empty" failure class)
- `relates-to` → [ADR-0012](../../adr/0012-model-agnostic-llm.md) / [ADR-0017](../../adr/0017-llm-transport-openai-compatible-deepseek.md) (why the balance read stays off the port)
- `relates-to` → [ADR-0037](../../adr/0037-per-task-reasoning-budgets.md) / [ADR-0031](../../adr/0031-boundary-self-consistency-honest-graduations.md) (the cost drivers behind the threshold)

## Handoff

- **Severity tier: `crucial`.** Rung 2 adds a **new external call** to the unattended daily path: a new endpoint, carrying the API key, on every run. The severity gate classes that as crucial, and doubt rounds up. Rung 1 alone (reading an existing counter, `src/`-only) would be non-crucial, but the approved scope includes both. **Both human checkpoints apply:** Tarig approves this brief before any code, and approves the PR before merge. The live deploy is always a checkpoint. No schema, terraform, IAM, new library or PII is involved.
- **Ready-for-Surgeon checklist:** verified ✅ (code + 1,672 live 402 events + run summaries) · root-caused ✅ (counted, never announced) · fix plan ✅ (rungs 1 + 2, threshold justified) · validation gate (behavioural + negative, mutation-proven) ✅ · out-of-scope ✅.
- **⚠️ Before coding, the Surgeon must:**
  1. Re-read the balance (Evidence 6). If it is already ≤ 0, the live AFTER proof can run on the first post-deploy day.
  2. Check `tests/test_integration_handler.py` for a real `OpenAICompatLlmClient`, so the new call never hits the network from the suite.
  3. Keep `CreditAlert` wording short (*what* + *where*), like `IntakeAlert`. The run summary and logs hold the detail.
- **On fix:** fill in **Resolution — as-built** below and set `status: fixed` after the live AFTER proof (step 3) is in the inbox.

## Resolution — as-built _(2026-09-23)_

> 🚧 **Merged and deployed, not yet live-proven.** Examiner clean pass after one fix round. Tarig approved the merge (crucial tier; #80, squashed as `2f49894`) and the deploy, which went live **2026-09-23 18:09 UTC** (build `UuR0jq10…`, terraform 0/2/0, smoke `200`; see the [CHANGELOG](../../../CHANGELOG.md)). CodeRabbit was skipped on #80 at Tarig's request (his plan's review limit), so the Examiner was the only independent review. **Awaiting:** the live AFTER proof (Live proof step 3: a delivered digest that leads with the banner, expected 2026-09-25). The status moves to `fixed` only on that email, not on the merge or the deploy.

- **What shipped.** Both approved rungs, as one unit.
  - *Rung 1 (announce).* New pure [`core/credit.py`](../../../src/jobfetcher/core/credit.py): `credit_problem(ingest_counts, score_counts, balance)` returns a `CreditAlert(what, where, blocking)` or `None`. A run with `billing_blocked > 0` in either stage gets a **blocking** alert from its own counts, whatever the balance read did. [`core/notifier.py`](../../../src/jobfetcher/core/notifier.py) renders it through the B-12 banner helper (renamed `_alert_banner`, since it now serves both alerts), in both render paths; [`core/ingest.py`](../../../src/jobfetcher/core/ingest.py) `notify(credit_alert=)` threads it through and changes nothing else.
  - *Rung 2 (forecast).* [`adapters/llm_openai.py`](../../../src/jobfetcher/adapters/llm_openai.py) `OpenAICompatLlmClient.read_balance() -> LlmBalance(usd, available, error)`: one `GET {base_url}/user/balance`, 5 s, no retry, never raises, never touches a response body or header. [`handlers/pipeline.py`](../../../src/jobfetcher/handlers/pipeline.py) keeps the scorer's client as `score_llm`, reads the balance once **after `stage=score` and before the notify branch**, and records `llm_balance_usd` / `llm_balance_error` in the summary of every normal run that reaches that point (send, partial, already-sent). Balance ≤ 0 or `is_available: false` → blocking; `0 < usd < $2.00` → a non-blocking "LLM credit low: $X left (≈N postings)".
  - *Unchanged, as the blast radius required:* the `LlmClient` port, `complete()` and its 402 mapping, the `_BILLING_BLOCKED` sentinel and its log line, `partial`, the send-once guard, scoring semantics, B-12 and INV-004 logic and wording, schema, Terraform, IAM, the capture endpoint, smoke and reassess.
- **Rung taken · divergence from the Fix plan.** Rungs 1 + 2, as approved. Divergences, all small:
  - **`is_available: false` with a positive balance** reads *"The LLM account is out of credit (DeepSeek reports it unavailable)"* rather than the plan's `($0.00 left)`, because printing a positive amount next to "out of credit" would contradict itself. A zero or negative balance prints the amount (`$0.00 left`, `-$0.30 left`), truncated to cents. Its *where* reads *"Nothing can be scored until then"*, since on such a day there may be nothing blocked to resume.
  - **Error vocabulary gained `unsupported`**, recorded by the handler when the client has no `read_balance`, and the handler treats a reading that is not an `LlmBalance` as `error:TypeError`. The plan's vocabulary is otherwise as written.
  - **The unit command is `pytest -m "not integration"`.** There is no `unit` marker; that selection is what the guarded `tests_unit` count measures.
  - **The `statusCode: 500` summary does not carry `llm_balance_*`.** A crash happens before or instead of the read, and the 500 summary carries only what had finished (the B-12 idiom: `mode`, `ingest`). VG-c's "every normal-mode run that reaches that point" holds; a 500 does not reach it.
  - **A known edge, left alone:** `_dollars` raises `decimal.InvalidOperation` for a balance of magnitude ≥ ~1e26. `_credit_alert_for_digest`'s guard catches it (digest sends, no banner, run stays `200`). Not a realistic input. Recorded in [B-10](../../ledgers/backlog.md).
- **The Examiner round.** A fresh Examiner's first pass found **three mutations that survived** (Examiner S1, S2, N3), all in the handler's own safety net: the guards around the balance read and around the credit rule could be removed without any test failing. They are now pinned in `tests/test_handler_credit.py` under *the handler's own safety net*: a read that raises despite its contract (`error:RuntimeError`), a read that returns something other than an `LlmBalance` (`error:TypeError`), a credit rule that throws (the digest still sends, with no banner and no `PIPELINE_ALARM`), and the read happening **after** scoring. Re-verified: every mutation killed, **clean pass**. The Examiner's NIT 8 (urllib forwards `Authorization` on a cross-host redirect) is pre-existing in `complete()` and was logged as [B-18](../../ledgers/backlog.md) rather than widened into this unit.
- **Key files + decisions.** `core/credit.py` (the rule, `LLM_LOW_CREDIT_USD = 2.00`, `LLM_COST_PER_POSTING_USD = 0.0155`) · `adapters/llm_openai.py` (`read_balance`, `LlmBalance`, `_BALANCE_TIMEOUT_S`) · `core/notifier.py` (`_with_banners` order) · `handlers/pipeline.py` (`_read_llm_balance`, `_credit_alert_for_digest`). Tests: `tests/test_credit.py`, `tests/test_handler_credit.py`, additions to `tests/test_llm_openai.py` and `tests/test_notifier.py`; unit suite 686 → 787. Load-bearing choices: **rule (a) never waits on rung 2**; the balance is read **after** scoring (B-10's lag only reads high, so the warning can be slightly late and never falsely early); the read stays **off the port**.
- **Links:** PR #80 (branch `fix/llm-credit-alert`, squash-merged as `2f49894`) · [CHANGELOG `[Unreleased]`](../../../CHANGELOG.md) · [ERR-019](../../ledgers/errors.md) · [interface-contracts](../../ledgers/interface-contracts.md) (INV-005 row) · follow-ups [B-16](../../ledgers/backlog.md) (401/404 still silent) · [B-17](../../ledgers/backlog.md) (402'd dissections never retried) · [B-18](../../ledgers/backlog.md) (cross-host redirect).
- **Extending / editing later.**
  - *Another provider:* give its adapter a `read_balance()` returning `LlmBalance`, or nothing; without one the run records `unsupported` and only rule (a) can banner. Do not add `balance()` to the port.
  - *A "key rejected" banner (B-16):* add a run-wide count and a rule to `credit_problem`; it will render through the same banner order.
  - *Changing the threshold:* re-do the threshold table (Fix plan, step 8) against current cycle sizes and `reasoning_effort` (ADR-0037) first. `$2.00` is sized to warn one full fetch cycle ahead, not chosen as a round number.
  - *Gotcha:* the reading after a run is high by the settlement lag (up to 16% on 2026-09-02), so the post-top-up check (Live proof step 5) will look better than the settled balance.
