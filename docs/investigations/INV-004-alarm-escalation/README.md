---
id: INV-004
title: The alarms work. Nothing escalates. A 38-day failure looks exactly like a 1-day failure.
status: handoff-ready   # open | verifying | verified | handoff-ready | in-progress | fixed | killed
severity: non-crucial   # rung 1 is src-only. Rungs 2-3 touch live infra -> crucial.
logged: 2026-09-04
updated: 2026-09-04
source: an audit of the three existing alarms, requested by Tarig before adding a fourth (B-5)
---

<!-- Case folder: docs/investigations/INV-004-alarm-escalation/README.md -->

# INV-004 · The alarms work. Nothing escalates.

**Status:** `handoff-ready` · **Severity:** `non-crucial` (rung 1) · **Owner of the fix:** _(a Surgeon, once handed off)_

> The `returned-500` alarm detected the 38-day outage on day one and reported it **29 times**. The outage still lasted 38 days. This is not a detection problem.

## The problem

This investigation was opened to answer a narrower question — *"should we add a fourth alarm for green-but-empty runs?"* ([B-12](../../ledgers/backlog.md)). Auditing the existing three first **inverted the answer**: adding a fourth alarm to this system would make it worse, because nothing that fires is being acted on.

The pipeline has three alarms. They are correctly configured, correctly wired, and they work. **What is missing is any notion of *how long* something has been broken.** An alarm that fires on day 1 and an alarm that fires on day 29 produce an identical email. So a persistent failure decays into background noise — which is the documented cause of the two worst incidents in this project's history.

## Does it exist? — verification

Measured read-only 2026-09-04 against the deployed stack (`jobfetcher-dev`, us-east-1).

- **Evidence 1 — the alarm that mattered fired 29 times and changed nothing.**
  ```bash
  aws cloudwatch describe-alarm-history --alarm-name jobfetcher-dev-pipeline-returned-500 \
    --history-item-type StateUpdate --max-records 100
  ```
  **29 `ALARM` transitions between 2026-08-06 and 2026-09-02** (~one per day), each with a matching recovery to `OK`. Every one delivered. The digest was not sent for **38 days**. The detector was never the gap ([ERR-010](../../ledgers/errors.md)).

- **Evidence 2 — the notification path is genuinely live, so "it didn't reach him" is excluded.**
  ```bash
  aws sns list-subscriptions-by-topic --topic-arn arn:aws:sns:us-east-1:198592435375:jobfetcher-dev-alarms
  ```
  One **confirmed** email subscription. The alerts arrived.

- **Evidence 3 — alarm usefulness, by firing count.**

  | Alarm | Fired | Reading |
  |---|---|---|
  | `pipeline-returned-500` | **29** (08-06 → 09-02) | Works. Ignored 29 times. |
  | `pipeline-errors` | **1** (08-22) | Works, rare, high signal. Keep unchanged. |
  | `pipeline-dead-man` | **0, ever** | Correctly silent — and **never once exercised** (see below). |

- **Evidence 4 — the escalation the fix would need cannot be built with CloudWatch alarms.** A CloudWatch alarm's evaluation window is capped at 24 hours (`period × evaluation_periods ≤ 86400`; `period` max 86400). `terraform/alarms.tf` already documents this, and it is the same limit that blocked the every-3-days cron in [INV-003](../INV-003-silent-fetch-stop/README.md). **There is no alarm configuration that can express "this has now failed 3 days running."** Escalation must come from something that can see across days.

- **Evidence 5 — the cadence change did not weaken any alarm.** Checked rather than assumed: the dead-man watches `AWS/Events Invocations` on the rule, which still fires **daily** (the fetch is gated in code, not in the schedule — INV-003); `errors` watches `AWS/Lambda Errors`; `returned-500` watches the `PIPELINE_ALARM` marker. All three are dimensioned on things the cadence change did not touch, and all three read `OK` after it.

- **Magnitude:** the two most expensive incidents in the project both had a working detector. [ERR-010](../../ledgers/errors.md): 38 days, alarm firing daily, ignored. [ERR-017](../../ledgers/errors.md): ~19 dead days a month for two months. **No amount of additional detection would have shortened either one.**

## Mechanism (root cause)

**Nothing in the system holds the concept "this has been failing for N days."**

1. **CloudWatch structurally cannot.** Alarms are stateless beyond a 24-hour evaluation window (Evidence 4). Every day of a 38-day outage is, to CloudWatch, an independent one-day event.
2. **A flapping alarm re-notifies identically.** `returned-500` uses `period = 300` with `notBreaching` on missing data, so it goes `ALARM → OK → ALARM` daily. Each transition emails. Twenty-nine emails, byte-identical, with nothing distinguishing the 29th from the 1st.
3. **All three alarms share one SNS topic and one inbox**, alongside the daily digest. There is no channel, subject or cadence difference between *"something twitched"* and *"you have received no results for a month."*

**The seam that makes a fix cheap already exists.** `Repository.get_last_digest_sent_at(user_id)` ([core/ports.py](../../../src/jobfetcher/core/ports.py)) returns `MAX(run_log.digest_sent_at)` and is already implemented and used by the digest's new/still-open split. **"Days since a digest actually reached the user" is computable today, with no new table, no new column, and no new query.**

That metric is also the *right* one: it measures the **product outcome** (did a shortlist reach the human?) rather than any internal stage. It would have read 38 during ERR-010 and would climb during ERR-017 — both incidents, one number, no per-failure-mode enumeration.

## Blast radius

- **Changes (rung 1):** `src/jobfetcher/core/ingest.py` (or `handlers/pipeline.py`) — read the existing port method, compute staleness, put it in the digest + run summary. `tests/`.
- **Must NOT change:** the three existing alarms' thresholds or dimensions (they work — this dossier is explicit that they are not the problem) · `run_log`'s schema or the send-once guard · the digest's send-once semantics.
- **Unaffected:** the fetch path, scoring, the capture endpoint, all migrations.
- **Rungs 2–3 only:** `terraform/alarms.tf` + SNS — live infra, and a separate decision.

## Fix plan (the handoff guideline)

**Rung 1 — the pipeline knows how stale it is, and says so (`src/` only). Recommended.**
Compute `days_since_last_digest` from the existing `get_last_digest_sent_at`. Surface it in two places: the **run summary** (so `runs/*.json` carries it for forensics) and the **digest itself** — which is the channel the operator demonstrably reads. Below a threshold it is a quiet one-liner; above it, the digest leads with it. Reuse `notify` in [core/ingest.py](../../../src/jobfetcher/core/ingest.py) and the existing renderer; no new port, no new I/O.

**Rung 2 — a distinct escalation signal (infra).**
A second log marker (e.g. `PIPELINE_ESCALATE`) emitted only when staleness crosses the threshold, with its own metric filter and its own alarm — reusing INV-002's existing wiring pattern. The point is not another detector but a **differently-shaped notification**: a distinct subject line that cannot be confused with the daily one.

**Rung 3 — a separate channel (infra + external).**
A second SNS topic for escalations only (SMS, or an address that is not the same inbox as the digest). Only worth doing if rungs 1–2 prove insufficient in practice.

> **Recommended stopping point: rung 1.** It is `src`-only, uses a port method that already exists, needs no AWS change, and puts the signal in the one channel with a proven read rate. Rung 2 should be judged *after* rung 1 has run for a while — adding infra to fix an attention problem is what this dossier exists to warn against.

**Explicitly sequenced before [B-12](../../ledgers/backlog.md):** the "green but empty" alarm is a good idea and should be built **after** escalation exists, not before. Added to today's system it becomes a fourth identical email in an inbox that has already ignored 29.

## Validation gate

Behavioral, with a negative case. The negative case is the one that matters: an escalation that fires on ordinary days is not an escalation.

| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | With the last digest N days old (N over the threshold), the run summary carries the staleness **and** the digest states it prominently. | With a digest sent **yesterday**, there is **no** escalation text anywhere — normal operation must look completely normal, or the signal is worthless within a week. |
| VG-b | With **no digest ever sent** (`get_last_digest_sent_at` returns `None`), the code does not crash and does not report a nonsensical age. | A first-ever run must not be treated as maximally stale and page on day one. |
| VG-c | The staleness is computed from `run_log`, i.e. from **a digest actually being sent** — not from the pipeline merely completing. | A run that completes but sends nothing must **not** reset the counter. That distinction is the entire point: ERR-010's pipeline ran daily for 38 days. |
| VG-d *(rungs 2–3)* | The escalation alarm fires when staleness crosses the threshold. | It does **not** fire for `smoke` or `reassess`, and not on a normal day. **Prove it by making it fire** ([ERR-015](../../ledgers/errors.md)/[ERR-016](../../ledgers/errors.md) standard) — an escalation asserted to work is exactly the failure mode under investigation. |

## Out of scope / rejected

- **Adding the B-12 "green but empty" alarm now.** Deferred on this dossier's own evidence — sequence it after escalation.
- **Changing the three existing alarms' thresholds/dimensions.** They work. The audit found no misconfiguration.
- **Deleting or muting the `returned-500` alarm** because it was noisy. It was *correct*. The noise was the absence of escalation, not the alarm.
- **A per-failure-mode alarm set.** Enumerating failure modes in advance is what missed ERR-017 entirely; one outcome-based signal (did a digest reach the human?) covers modes nobody has thought of.
- **Anything that pages more often.** The constraint this whole dossier operates under is a fixed attention budget.

## ⚠️ Separate finding — the dead-man alarm has never been tested

**Tracked here deliberately, at Tarig's request, so it is not lost.** `jobfetcher-dev-pipeline-dead-man` has **zero** state changes in its entire history. That is *probably* correct — the EventBridge schedule has never stopped firing, so the alarm has never had cause. But it means the path **"the schedule stops" → "an email arrives"** has never been exercised end to end.

This is the [ERR-015](../../ledgers/errors.md) shape: a mechanism that looks real, that everyone assumes works, and that has never been made to fire. ERR-015 was closed by pushing a commit and *being rejected*; ERR-016's gate by pushing a regression and *watching CI go red*. The dead-man deserves the same treatment and has not had it.

**⚠️ HALF-TESTED 2026-09-04 — and be precise about which half.**

```bash
aws cloudwatch set-alarm-state --alarm-name jobfetcher-dev-pipeline-dead-man   --state-value ALARM --state-reason "INV-004 deliberate test"
# then, AWS/SNS on the jobfetcher-dev-alarms topic:
#   NumberOfNotificationsDelivered = 1.0
#   NumberOfNotificationsFailed    = 0.0
```

**PROVEN:** the notification path. The alarm enters `ALARM` → SNS publishes → the confirmed
email subscription delivers. That had never once been exercised in the alarm's life. It was
restored to `OK` immediately after, and the EventBridge schedule was never touched.

**STILL NOT PROVEN — and the distinction matters:** the *detection* half. `set-alarm-state`
**bypasses metric evaluation entirely**, so this says nothing about whether a genuinely
missing invocation drives the alarm into `ALARM` — i.e. whether `treat_missing_data:
breaching` on the sparse `AWS/Events Invocations` metric behaves as designed.

**Do not read this as "the dead-man works."** Read it as: *if it ever fires, the email will
reach you.* Whether it fires when it should is still an assumption.

**How to finish the test** (needs ~2 days and one skipped run, so it must be scheduled): disable the EventBridge rule for long enough for a 24-hour window to close with no invocation, confirm the email arrives, re-enable. The cost is one skipped run; the alternative is trusting an untested detector on the one failure mode nothing else covers. **Not urgent, but it should be scheduled rather than remembered.**

## ⚠️ Separate finding — the capture endpoint has no monitoring at all

All three alarms are dimensioned on `jobfetcher-dev-pipeline`. **`jobfetcher-dev-capture` — the public, internet-facing write endpoint ([ADR-0035](../../adr/0035-outcome-capture-endpoint.md)) — has none.** It could be erroring on every request, or being probed, and nothing would say so. Recorded, not proposed: it is a different question from escalation, and this dossier is explicitly against reflexively adding alarms.

## Connections (typed)

- `causes` → a 38-day outage with a working detector firing daily ([ERR-010](../../ledgers/errors.md))
- `caused-by` → CloudWatch's 24-hour alarm evaluation ceiling (`period × evaluation_periods ≤ 86400`)
- `caused-by` → three alarms sharing one SNS topic and one inbox with no severity distinction
- `blocks` → [B-12](../../ledgers/backlog.md) (the green-but-empty alarm should not ship before this)
- `touches` → `file:src/jobfetcher/core/ingest.py` · `file:src/jobfetcher/core/ports.py` (read-only reuse of `get_last_digest_sent_at`)
- `touches` → `file:terraform/alarms.tf` *(rungs 2–3 only)*
- `depends-on` → `run_log.digest_sent_at` (migration 0003) — the existing cross-day state
- `relates-to` → [B-5](../../ledgers/backlog.md) (the original signal: "a daily alarm is not an alarm")
- `relates-to` → [INV-002](../INV-002-silent-500-alarm/README.md) (built the `returned-500` alarm this dossier finds insufficient — not wrong, insufficient)
- `relates-to` → [INV-003](../INV-003-silent-fetch-stop/README.md) / [ERR-017](../../ledgers/errors.md) (same 24h ceiling, same attention budget)
- `relates-to` → [ERR-015](../../ledgers/errors.md) (the untested-mechanism pattern, for the dead-man finding)

## Handoff

- **Severity tier:** `non-crucial` for **rung 1** — no schema, no infra, no new dependency, no PII, no scoring change; auto-pilot eligible on a clean Examiner pass. **Rungs 2–3 are `crucial`** (live infra + notification routing) and need both human checkpoints.
- **Ready-for-Surgeon checklist:** verified ✅ · root-caused ✅ · fix plan ✅ · validation gate (behavioral + negative) ✅ · out-of-scope ✅.
- **The one thing the Surgeon must hold onto:** this is an **attention** problem, not a detection problem. Every instinct will be to add signal. The evidence says the system already produced 29 correct signals and got zero response. If a proposed change increases the number of ordinary-day notifications, it is the wrong change.
- **On fix:** fill the Resolution below → set `status: fixed`.

## Resolution — as-built _(filled at close, when the fix ships)_

> ⏳ **Pending** — not yet built.

- **What shipped:** _(the as-built, in prose)_
- **Rung taken · divergence from the Fix plan:** _(which rung; any deviation + why)_
- **Key files + decisions:** _(where the code lives; the load-bearing choices)_
- **Links:** PR #… · ADR … · CHANGELOG `[vX.Y.Z]` · commit `<sha>`
- **Extending / editing later:** _(the seams and gotchas)_
