---
id: INV-003
title: A run that fetches nothing is indistinguishable from a run with nothing to fetch
status: fixed           # open | verifying | verified | handoff-ready | in-progress | fixed | killed
severity: non-crucial   # rungs 1–2 (src-only observability). Rung 3 adds live infra → crucial.
logged: 2026-09-04
updated: 2026-09-04
source: live-stack review during the ERR-016 documentation audit, 2026-09-04; not reported by any alarm
---

<!-- Case folder: docs/investigations/INV-003-silent-fetch-stop/README.md -->

# INV-003 · A silent fetch stop reports success

**Status:** `fixed` (rungs 1–2 + the capacity fix; rung 3 open as [B-12](../../ledgers/backlog.md)) · **Severity:** `non-crucial` (rungs 1–2) · **Owner of the fix:** _(a Surgeon, once handed off)_

> The pipeline has ingested **zero** postings for three consecutive days while returning `statusCode: 200`, emailing a digest every morning, and holding all three CloudWatch alarms in `OK`.

## The problem

Since **2026-09-02** every daily run reports success and fetches nothing. No raw posting has landed since **2026-09-01**. The daily digest still goes out, carrying 257 previously-scored jobs, so from the inbox the tool looks alive and useful.

Two separate faults are tangled here, and **collapsing them is how this recurs**:

1. **The trigger** — whatever is making JSearch return HTTP 429 (quota exhausted, subscription lapsed, or rate-limit). Resolved outside the codebase.
2. **The blindness** — a run cut off at the first request is **byte-for-byte identical**, in the logs, the counters, the run summary and every alarm, to a run where the source genuinely had nothing new.

**Only fault 2 is engineering work, and only fault 2 prevents the next occurrence.** Fixing the quota alone leaves the trap armed and re-arms the clock.

This is [ERR-010](../../ledgers/errors.md)'s lesson one layer up, and strictly worse: that outage at least returned a `500`.

## Does it exist? — verification

Every claim below was measured read-only on 2026-09-04 against the deployed stack as `jobfetcher-dev` (us-east-1, account `198592435375`).

- **Evidence 1 — ingestion stopped dead, mid-stride.** Raw bronze objects ran at 130–146/day through 2026-09-01 and then ceased entirely. No taper, no decline.
  Reproduce:
  ```bash
  aws s3api list-objects-v2 --bucket jobfetcher-dev-198592435375 --prefix raw/ --output json \
    | python -c "import sys,json,re;ks=[o['Key'] for o in json.load(sys.stdin)['Contents']];\
  print(sorted({m.group(1) for k in ks if (m:=re.search(r'(\d{4}-\d{2}-\d{2})',k))})[-4:])"
  ```
  Expected (unfixed): `['2026-08-30', '2026-08-31', '2026-09-01']` — **the last date is 2026-09-01**. A later date means the trigger cleared on its own; re-verify before acting.

- **Evidence 2 — three consecutive green, empty runs.** From `s3://jobfetcher-dev-198592435375/runs/{date}/{run_id}.json`:

  | date | statusCode | partial | fetched | bronzed | scored | digest surfaced |
  |---|---|---|---|---|---|---|
  | 2026-09-01 | 500 | — | 0 | 0 | 0 | 0 |
  | 2026-09-02 | 200 | False | **0** | 0 | 618 ¹ | 0 |
  | 2026-09-03 | 200 | False | **0** | 0 | 0 | 257 |
  | 2026-09-04 | 200 | False | **0** | 0 | 0 | 257 |

  ¹ the 618 was the pre-existing backlog being drained after the ERR-010 fix — **not new intake**.
  Reproduce: `aws s3 cp s3://jobfetcher-dev-198592435375/runs/2026-09-04/ - --recursive | python -m json.tool`

- **Evidence 3 — the fetch stage records no reason, at any level.** The complete CloudWatch log for the 2026-09-03 06:00 UTC run (`run_id=5d9005d8`, log group `/aws/lambda/jobfetcher-dev-pipeline`):
  ```
  stage=ingest start run_date=2026-09-03
  stage=ingest done {'fetched': 0, 'bronzed': 0, 'silvered': 0, 'skipped': 0,
                     'already': 0, 'deferred': 0, 'billing_blocked': 0}
  ```
  **No error, no warning, and no JSearch line whatsoever.** `already: 0` rules out dedup — the source yielded nothing at all. *(On Git Bash, `export MSYS_NO_PATHCONV=1` first or the log-group path is mangled into an `InvalidParameterException`.)*

- **Evidence 4 — not a config change.** `s3://jobfetcher-dev-198592435375/config/search_config.yml` last modified **2026-07-08T07:04:53Z**: 3 job titles × 6 GCC countries (18 queries), `date_posted: month`, `budget.max_pages_per_query: 1`, `budget.request_budget_per_run: 25`. Those exact settings produced 140 raw objects on 2026-09-01.
  Reproduce: `aws s3api head-object --bucket jobfetcher-dev-198592435375 --key config/search_config.yml --query LastModified`

- **Evidence 5 — no alarm covers this shape.** All three alarms report `OK` throughout.
  Reproduce: `aws cloudwatch describe-alarms --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table`
  The dead-man watches *invocations* (the cron fires fine), `Errors` watches *Lambda errors* (there are none), and INV-002's filter watches a *returned 500* (this returns 200). A green, empty run is outside all three by construction.

- **Magnitude:** **3 days and counting**, unbounded. The prior instance of "nobody noticed" ([ERR-010](../../ledgers/errors.md)) ran **38 days** — and that one at least tripped an alarm every morning. This one trips nothing. There is currently **no signal at any horizon** that would end it.

## Mechanism (root cause)

**The blindness is a two-line asymmetry in [`adapters/jsearch_source.py`](../../../src/jobfetcher/adapters/jsearch_source.py) `fetch()`.**

```python
# jsearch_source.py:181-190
if exc.code in _AUTH_FAIL_CODES:      # 401/403
    raise SourceError(                 # ← fails LOUDLY, by explicit design
        f"JSearch authentication failed (HTTP {exc.code}) — the API "
        "key is missing, wrong, revoked, or the subscription lapsed"
    ) from exc
if exc.code == _RATE_LIMIT_CODE:      # 429  (jsearch_source.py:189-190)
    return                             # ← silent: no log, no counter, no signal
```

The comment sitting three lines above that `raise` states the exact principle the next branch breaks:

> *"401 = bad/missing key, 403 = auth/subscription failure — a broken credential must **FAIL LOUDLY**, else a rotated key turns into a silent zero-count 'success'."*

**A 429 now does precisely what that comment forbids.** The author identified this failure mode, guarded it for authentication, and left it open for quota — the neighbouring branch, four lines down, for a cause that is *more* likely on a metered free tier than key rotation.

`fetch()` is a generator. A bare `return` on the first request ends it having yielded nothing. `ingest()` then computes `"fetched": len(landed)` ([`core/ingest.py:359`](../../../src/jobfetcher/core/ingest.py)) over an empty list, every downstream stage correctly does nothing, and the handler returns `200`.

**There is a second silent return of the same shape** — the budget cap at [`jsearch_source.py:175-176`](../../../src/jobfetcher/adapters/jsearch_source.py) (`if made >= cap: return`). It is not the current cause (`request_budget_per_run: 25` against 18 requests, so it cannot fire first) but it is the identical defect and would produce an identical unexplained zero. Fix both or fix neither.

**What is NOT the cause, checked rather than assumed:**

| Ruled out | Evidence |
|---|---|
| The stop *behaviour* | `tests/test_jsearch_source.py::test_fetch_429_stops_gracefully` asserts a 429 stops the sweep and keeps what came before. **That behaviour is correct and must not change** — stopping politely on a rate limit is right. The defect is that stopping is invisible. |
| The request budget | 25 vs 18 requests — cannot fire before the first call (Evidence 4) |
| A config change | unchanged since 2026-07-08 (Evidence 4) |
| A dedup/`already` skip | `already: 0` — nothing was yielded to dedup (Evidence 3) |
| The ERR-010 / ADR-0036 deploy (same date) | that change touched the gold-filter **read path** and LLM budgets, not `fetch()`. Coincident dates, and the code path is disjoint — but a Surgeon should confirm with `git log -p` on `jsearch_source.py` before closing. |

**The trigger — deliberately still open.** Why JSearch returns 429 (quota exhausted / subscription lapsed / rate-limit) is **not established**. I did not call the JSearch API: that spends the resource under suspicion, and a probe can itself deepen a rate-limit. Establishing it needs the RapidAPI dashboard (usage / limit / plan state). **Fill this in before the fix ships — but note the fix below is worth shipping regardless of the answer.**

## Blast radius

- **Changes (rungs 1–2):** `src/jobfetcher/adapters/jsearch_source.py` (the two silent returns) · `src/jobfetcher/core/ingest.py` (carry a reason into the summary) · `tests/test_jsearch_source.py` + `tests/test_ingest.py` (new cases).
- **Must NOT change:** the 429 **stop semantics** (`test_fetch_429_stops_gracefully` must keep passing unmodified) · the loud 401/403 raise · the `SourceError` contract · the yielded job shape · `run_log` send-once · anything in the gold/score/notify path.
- **Unaffected:** every other adapter, the capture endpoint, the Data API read path, all migrations, the LLM budgets.
- **Rung 3 only:** `terraform/alarms.tf` — live infra, and therefore a separate, human-approved decision.

## Fix plan (the handoff guideline)

**Rung 1 — make the stop audible (`src/` only).** In `fetch()`, replace both bare returns with a logged stop that names *why* and *where*: the HTTP code, the query (title/country/page) it died on, and `made` (requests actually spent). Reuse the existing `log.warning` pattern already used four lines below for other HTTP codes — no new logging machinery.

**Rung 2 — make the stop legible to the run summary (`src/` only).** Carry the reason out of the generator so `runs/{date}/{run_id}.json` records *why* zero, not merely zero. Minimal shape: a `fetch_stopped` reason string (e.g. `rate_limited` / `budget_exhausted` / `null`) in the `ingest` dict built at [`core/ingest.py:359`](../../../src/jobfetcher/core/ingest.py). A generator cannot return a value alongside its yields, so the Surgeon picks the cheap seam — a small result object, a mutable `stats` dict passed in, or an attribute on the adapter. **Additive only; the existing keys keep their meaning.**

> **Recommended stopping point: rungs 1 + 2.** Together they make the failure *diagnosable in one CloudWatch query and visible in the durable audit trail*, with no infra change and no new dependency. That is the minimal change that solves the present problem.

**Rung 3 — make it *noticed* (infra; needs Tarig's approval).** Rungs 1–2 make it findable by someone already looking; three days of evidence say nobody was. Options, in ascending cost: emit a `PIPELINE_ALARM`-style marker on a zero-fetch **unattended daily** run and reuse INV-002's existing log-metric-filter → SNS wiring (`terraform/alarms.tf`); or state it in the digest itself, which needs no infra at all and reaches the one person who reads every morning. **Mode-gate it exactly as INV-002 did** — a `reassess` or `smoke` invocation legitimately fetches nothing and must never fire it.

**Cross-reference, do not merge:** [B-5](../../ledgers/backlog.md) ("a daily alarm is not an alarm — nothing escalates a *persistent* failure") is the neighbouring gap. This dossier is about a failure with *no* signal; B-5 is about a signal nobody escalates. Related, distinct, separately fixable.

## Validation gate

Behavioral, with a negative case. **The negative case is the whole point** — if a rate-limited run and a genuinely-empty run still look the same afterwards, the fix failed regardless of what the logs now say.

| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | A fake source raising `HTTPError(429)` on the **first** request ⇒ the run logs a warning naming the 429 and the query, **and** the `ingest` summary records the stop reason. | A source returning `200` with `{"data": []}` for every query ⇒ **no** stop reason, **no** warning — zero because there was nothing, not because we were cut off. The two runs must be **distinguishable from the run summary alone**. |
| VG-b | The budget-cap return (`made >= cap`) is likewise logged and reported, with a reason distinct from the rate-limit one. | A run that completes the full sweep **under** budget ⇒ no stop reason. |
| VG-c | `tests/test_jsearch_source.py::test_fetch_429_stops_gracefully` **passes unmodified** — a 429 mid-sweep still yields what came before and still stops. | A 429 must **not** become fatal: the run still returns `200` and still sends a digest from existing data. Escalating a graceful stop into a crash would be a regression, not a fix. |
| VG-d *(rung 3 only)* | The zero-fetch signal fires on an unattended daily run that fetched nothing. | It does **not** fire for `{"mode":"smoke"}` or `{"mode":"reassess"}`, which legitimately fetch nothing. **Prove it by making it fire on purpose** — the [ERR-015](../../ledgers/errors.md)/[ERR-016](../../ledgers/errors.md) standard: a gate asserted to work is not a gate. |

## Out of scope / rejected

- **Changing the 429 stop behaviour.** Stopping politely is correct; a covered test asserts it. Only its silence is the bug.
- **Retry/backoff against a rate limit.** Retrying a quota exhaustion burns the quota faster. Out of scope, and probably wrong.
- **Adding a second source (Adzuna / M2).** A real capability, an entirely different bottleneck. Would mask this one rather than fix it.
- **A generic "any stage returned zero" alarm.** Over-broad — zero gold candidates on a quiet day is normal and would train the operator to ignore it, which is exactly B-5's failure.
- **Fixing the quota and stopping there.** Restores service, leaves the blindness. Explicitly rejected as the *only* action.
- **Anything in `docs/` beyond this dossier and its index/backlog rows** (Investigator is read-only by [ADR-0034](../../adr/0034-investigation-dossier-system.md)).

## Connections (typed)

- `causes` → 3+ days of zero ingestion reported as `statusCode: 200`
- `caused-by` → `file:src/jobfetcher/adapters/jsearch_source.py` (the bare `return` at :190, and its twin at :176)
- `caused-by` → an external JSearch 429 *(trigger unconfirmed — RapidAPI account state)*
- `touches` → `file:src/jobfetcher/adapters/jsearch_source.py`
- `touches` → `file:src/jobfetcher/core/ingest.py`
- `touches` → `file:tests/test_jsearch_source.py`
- `touches` → `file:terraform/alarms.tf` *(rung 3 only)*
- `blocks` → the daily shortlist — the tool's entire product
- `relates-to` → [B-5](../../ledgers/backlog.md) (persistent-alarm escalation — the adjacent gap)
- `relates-to` → [INV-002](../INV-002-silent-500-alarm/README.md) (same family: a failure invisible to the alarms; its marker + metric-filter pattern is the rung-3 reuse point)
- `relates-to` → [ERR-010](../../ledgers/errors.md) (the prior "nobody noticed", 38 days)
- `relates-to` → [ADR-0010](../../adr/0010-job-source-jsearch.md) (JSearch as the single v0 source)
- `blocked-by` → the RapidAPI account state, to confirm the trigger *(not required for rungs 1–2)*

## Handoff

- **Severity tier:** `non-crucial` for **rungs 1–2** — no schema, no infra, no new dependency, no PII, no scoring-semantics change; auto-pilot eligible on a clean Examiner pass. **Rung 3 is `crucial`** (live infra) and needs both human checkpoints. Doubt rounds up: if the Surgeon finds rung 2 needs anything beyond an additive summary key, re-classify.
- **Ready-for-Surgeon checklist:** verified ✅ · root-caused ✅ (blindness, at `file:line`) · fix plan ✅ · validation gate (behavioral + negative) ✅ · out-of-scope ✅.
- **⚠️ Two things the Surgeon must do first:**
  1. **Re-run Evidence 1.** If `raw/` now shows a date after 2026-09-01 the trigger cleared itself, which changes the urgency but **not** the fix — the blindness is independent of whether it is currently firing.
  2. **Confirm the trigger** from the RapidAPI dashboard and record it here. Rungs 1–2 are worth shipping either way.
- **On fix:** fill the **Resolution — as-built** section below → set `status: fixed`.

## Resolution — as-built _(2026-09-04)_

> ✅ **Fixed** for the blindness and the capacity mismatch. **Rung 3 (announcement) deliberately NOT built** — see below.

- **What shipped.** (a) **PR #63** — both silent stops log at WARNING with the reason, the request count and the query they died on; the reason exits the generator on `JSearchSourceAdapter.last_stop_reason` and lands in the run summary as **`fetch_stopped`**. (b) **Config, pushed live** — Oman dropped (165 postings fetched for 7 matches: a 4.2% hit rate vs 29–36% elsewhere) and `request_budget_per_run` 25 → 16 so the cap can actually fire. (c) **PR #64** — `is_fetch_day` / `FETCH_EVERY_N_DAYS`: the Lambda still runs **daily**, the sweep every 3rd day (15 × ~10 = ~150 requests against the 200/month tier).
- **The trigger, confirmed.** Not a one-off. The RapidAPI dashboard read **200 calls / 11 active days**, and the audit trail showed a two-month cycle: ingest ~11 days, die ~19, reset on the 22nd. The sweep was sized **2.7× its quota**. Written up as [ERR-017](../../ledgers/errors.md).
- **Rung taken · divergence from the Fix plan.** Rungs 1–2 as written. **Rung 3 diverged and the divergence is the interesting part:** the plan proposed an every-3-days *cron*. Building it surfaced that the dead-man alarm watches a **24-hour** window and cannot be widened (`period × evaluation_periods ≤ 86400`), so a 3-day cron fires on ~20 legitimate off-days a month — the alert fatigue that let ERR-010 run 38 days. **Decoupling "how often the Lambda runs" from "how often it calls the source" removed the need for any infra change at all.**
- **Key files + decisions.** [`adapters/jsearch_source.py`](../../../src/jobfetcher/adapters/jsearch_source.py) (`STOP_RATE_LIMITED` / `STOP_BUDGET_EXHAUSTED`, reset per sweep) · [`core/ingest.py`](../../../src/jobfetcher/core/ingest.py) (`FETCH_EVERY_N_DAYS`, `SOURCE_MONTHLY_QUOTA`, `is_fetch_day`, `next_fetch_day`, `skip_fetch`) · [`handlers/pipeline.py`](../../../src/jobfetcher/handlers/pipeline.py) (the gate + `$JOBFETCHER_FETCH_EVERY_N_DAYS` override). `ingest` reads the stop reason via `getattr`, so the `SourceAdapter` port is unchanged and any adapter omitting it still works.
- **Links:** PR #62 (this dossier) · PR #63 (legibility) · PR #64 (cadence) · [ERR-017](../../ledgers/errors.md) · [B-12](../../ledgers/backlog.md).
- **⚠️ Two things a later phase must know.**
  1. **The code is merged but the deployed Lambda still runs the 2026-09-02 build.** A `build_lambda.py` + `terraform apply` is outstanding, and **nothing can be live-validated until the quota resets ~2026-09-22** — until then every run fetches zero regardless.
  2. **This is diagnosable, not announced.** A `200` with `fetched: 0` still trips no alarm; it takes someone looking. That is B-12, and it is the same shape as B-5.
- **Extending later.** Raise `FETCH_EVERY_N_DAYS` and the RapidAPI plan **together** — the arithmetic is the point, and the skip message computes it live from the spec, so it stays honest on its own. `$JOBFETCHER_FETCH_EVERY_N_DAYS` overrides the cadence (1 or less = every day; the integration suite pins it there so tests never depend on the calendar). **Correction, 2026-09-05:** this line used to say "without a redeploy", and that was wrong in a way worth keeping on the record. Terraform manages the Lambda's `environment.variables` as a **whole map**, so a value set in the console survived only until the next `terraform apply` silently removed it — a documented escape hatch that stopped working with nothing announcing it (B-13). The variable is now declared in `terraform/lambda.tf`, which makes Terraform its owner: change it **there**, and redeploy. A console edit is still a legitimate emergency lever, but it is temporary by construction and must be followed by the same edit in IaC.
