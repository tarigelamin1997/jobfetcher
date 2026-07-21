---
id: INV-002
title: Silent 500 — a returned statusCode:500 pages nobody
status: fixed
severity: non-crucial   # additive observability (2 alarms + a marker); touches live infra but low-risk, no scoring/schema/dep/PII
logged: 2026-07-20
updated: 2026-07-21
source: B-3 "still-parked companion" (backlog) + the 2026-07-11 P2 scan; picked 2026-07-20 as the top *unblocked* bottleneck
---

# INV-002 · Silent 500 — a returned statusCode:500 pages nobody

**Status:** `fixed` (shipped + live-validated 2026-07-21) · **Severity:** `non-crucial` · **Owner of the fix:** the agentic squad

> The pipeline handler *catches* a stage failure and **returns** `statusCode:500` — which is a **successful** invocation to Lambda, so the AWS/Lambda Errors alarm never counts it and nobody is paged. An unattended daily run can die silently.

## The problem
The daily cron runs unattended (v0.9.0). When a stage fails, the handler returns `{statusCode:500}` rather than crashing — good for idempotent re-runs, but the two existing alarms miss it: the **dead-man** only fires if the run *didn't happen*, and the **Lambda Errors** alarm only counts crashes/timeouts (a *returned* 500 is a successful invocation). So a run that fails every stage and sends no digest raises **zero alerts** — exactly what happened on **2026-07-09** (a missed digest, no page).

## Does it exist? — verification
**Yes — confirmed in code + the ops config (re-runnable):**

- **Evidence 1 — the handler returns 500, never raises.** [`handlers/pipeline.py:486-496`](../../../src/jobfetcher/handlers/pipeline.py): the outer `except` logs `rlog.exception("pipeline failed: %s", exc)` then **returns** `{"statusCode": 500, ...}`. A returned dict is a *successful* Lambda invocation → the `AWS/Lambda Errors` metric is **not** incremented.
  Reproduce: read the except block, and `tests/test_db_resume.py::test_handler_wait_failure_is_still_a_loud_500` shows a forced failure → `out["statusCode"] == 500` (a return, not a raise).
- **Evidence 2 — the alarm can't see it, and the gap is self-documented.** [`terraform/alarms.tf`](../../../terraform/alarms.tf) has exactly two alarms: `pipeline_dead_man` (`AWS/Events Invocations < 1`) and `pipeline_errors` (`AWS/Lambda Errors >= 1`). The Errors-alarm comment states the gap verbatim: *"the handler catches stage failures and RETURNS `statusCode: 500` — a SUCCESSFUL invocation to Lambda, invisible here."* [ADR-0029](../../adr/0029-ops-hardening.md) names it a "documented future refinement."
- **Magnitude:** a *whole class* of failure (any returned-500 on the unattended path) is unmonitored; realized once already (2026-07-09, zero alerts). Low-frequency but high-consequence (silent daily-tool death).

## Mechanism (root cause)
The handler deliberately converts stage failures into a returned `statusCode:500` (retryable, idempotent) instead of an unhandled exception. Lambda's `Errors` metric only counts unhandled exceptions/timeouts, so the *returned* 500 is invisible to it. There is **no signal** an alarm can bind to — the only text on the failure path is the generic `pipeline failed` line, which is **also** emitted by attended-mode 500s (smoke's pre-migration/DB-unreachable gate, manual reassess), so it can't be matched naively.

## Blast radius
- **Changes:** `terraform/alarms.tf` (a managed log group + a log-metric-filter + an alarm) + a mode-gated marker line in `handlers/pipeline.py`.
- **Must NOT change:** the two existing alarms, the SNS topic + its email subscription, or the handler's return contract (still returns 500; the marker is additive).
- **Unaffected:** the whole pipeline (fetch → … → notify); the change is observability-only.

## Fix plan (the handoff guideline)
1. **A mode-gated marker** (`handlers/pipeline.py`, the outer `except`): after the existing `pipeline failed` line, emit `PIPELINE_ALARM` **only when** `mode not in ("smoke", "reassess")` — so only the *unattended* daily 500 is alarmable (`mode` is already resolved before the `try`). Reuse the existing `rlog`.
2. **Terraform** (`terraform/alarms.tf`): manage `aws_cloudwatch_log_group.pipeline` (retention bonus; **import once** on the running stack — the group is Lambda-auto-created), a `aws_cloudwatch_log_metric_filter` on `pattern = "\"PIPELINE_ALARM\""` → metric `JobFetcher/Pipeline / PipelineReturned500`, and a `aws_cloudwatch_metric_alarm` (`Sum >= 1`, `notBreaching`) → **reuse** `aws_sns_topic.alarms.arn`. Mirror the existing `pipeline_errors` alarm shape.
3. **Tests:** the marker fires on a normal-mode 500, not on smoke/reassess.

**Rejected:** an infra-only filter on `"pipeline failed"` — false-fires on the *expected* pre-migration smoke 500 + manual reassess → alarm fatigue (the project's no-false-green ethos). The ~4-line mode-gated marker buys precision cheaply.

## Validation gate
| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | A **normal-mode** run that fails a stage → the log contains `PIPELINE_ALARM`; the metric filter matches that line — runnable: `aws logs test-metric-filter --filter-pattern '"PIPELINE_ALARM"' --log-event-messages "PIPELINE_ALARM: unattended run returned statusCode=500" "pipeline failed: RuntimeError: boom" "pipeline done {...}"` → **only** the marker line matches — so the alarm would fire. | A **smoke-mode** and a **reassess-mode** 500 → the log has `pipeline failed` but **NOT** `PIPELINE_ALARM` (no false-fire) — `test_returned_500_alarm_marker_only_on_unattended_run`. |
| VG-b | Post-deploy: `describe-alarms --alarm-names jobfetcher-<env>-pipeline-returned-500` exists + wired to the SNS topic; the filter is present on the log group. | On a healthy stack the alarm sits `OK`/`INSUFFICIENT_DATA` — it does not fire without a real returned-500. |

## Out of scope / rejected
- **A "digest didn't send" (returned-200-but-empty) alarm** — a different gap (partial runs skip notify by design); not this unit.
- **Managing the capture Lambda's log group** — only the pipeline is the alarm target here.
- **A custom EMF/structured-logging overhaul** — the text-term filter is sufficient at this scale.

## Connections (typed — the graph seam)
- `caused-by` → `file:src/jobfetcher/handlers/pipeline.py` (returns 500 instead of raising)
- `relates-to` → [ADR-0029](../../adr/0029-ops-hardening.md) (the two alarms + the documented gap this closes)
- `touches` → `file:terraform/alarms.tf`
- `touches` → `file:src/jobfetcher/handlers/pipeline.py`
- `blocks` → "trustworthy unattended ops" (a silent daily failure)
- `relates-to` → [B-3 companion](../../ledgers/backlog.md) (where this was first named)

## Handoff
- **Severity tier:** `non-crucial` (additive observability; touches live CloudWatch but low-risk, no scoring/schema/dep/PII). The one-time log-group **import** is a non-destructive state op; the deploy is the standing checkpoint.
- **Ready-for-Surgeon checklist:** verified ✅ · root-caused ✅ · fix plan ✅ · validation gate (behavioral + negative) ✅ · out-of-scope ✅ · typed connections ✅.
- **On fix:** the **Resolution** section below is filled at close → set `status: fixed`.

## Resolution — as-built _(filled at close)_
> ✅ **Shipped + live-validated 2026-07-21.** Merged PR #35 → one-time log-group import → `terraform apply` (2 add / 4 change / 0 destroy). Smoke `200`. The alarm is live: `describe-alarms` → `State=OK`, `AlarmActions=[…:jobfetcher-dev-alarms]`, `PipelineReturned500` / `Sum>=1` / `notBreaching`; the metric filter `"PIPELINE_ALARM"` is on `/aws/lambda/jobfetcher-dev-pipeline`; `aws logs test-metric-filter` matches **only** the marker line; unit tests prove the marker fires on a normal-mode 500 but not on smoke/reassess.

- **What shipped:** the pipeline's returned-`statusCode:500` (a *successful* Lambda invocation, invisible to the AWS/Lambda Errors alarm) now **pages**. The handler emits a distinctive **`PIPELINE_ALARM`** line on the unattended daily-run 500; a CloudWatch log-metric-filter turns it into a `JobFetcher/Pipeline/PipelineReturned500` metric; an alarm (`Sum>=1`, `notBreaching`) → the **existing** SNS topic/email. The dead-man + Errors alarms are unchanged.
- **Rung taken · divergence from the Fix plan:** as planned — no divergence. Chose the **mode-gated marker** (code + infra) over an infra-only `"pipeline failed"` filter so the alarm never fires on the *expected* pre-migration smoke 500 or a manual reassess (avoiding alarm fatigue). One deploy prerequisite: a **one-time `terraform import`** of the Lambda-auto-created log group (now managed, retention 30d).
- **Key files + decisions:** [`handlers/pipeline.py`](../../../src/jobfetcher/handlers/pipeline.py) (the mode-gated marker in the outer `except`; `mode` is bound before the `try`) · [`terraform/alarms.tf`](../../../terraform/alarms.tf) (managed log group + metric filter + alarm, reusing `aws_sns_topic.alarms`) · [`tests/test_db_resume.py`](../../../tests/test_db_resume.py) (`test_returned_500_alarm_marker_only_on_unattended_run`).
- **Links:** PR #35 · CHANGELOG `[Unreleased]` (batches into the next `v0.12.x`) · merge commit `0278e1f` · closes the [ADR-0029](../../adr/0029-ops-hardening.md) documented gap.
- **Extending / editing later:** the marker line could carry the **failing stage** for richer alerting (a pure string change — the filter matches the `PIPELINE_ALARM` prefix). A separate, still-open gap is a **"digest didn't send" (returned-200-but-empty/partial)** alarm — a *different* metric (out of scope here). The managed log group now also bounds log retention (30d); a fresh clone needs **no import** (Terraform creates the group).
