---
id: INV-001
title: Dark feedback loop — the tool has no ground truth
status: fixed
severity: crucial          # the recommended fix (a capture endpoint) touches live infra + a public surface + auth; a rung-1 interim is non-crucial
logged: 2026-07-20
updated: 2026-07-20
source: B-3 companion (backlog) + the 2026-07-11 P2 scan; re-verified live 2026-07-20
---

# INV-001 · Dark feedback loop — the tool has no ground truth

**Status:** `fixed` (shipped + live-validated 2026-07-20) · **Severity:** `crucial` · **Owner of the fix:** the agentic squad (Surgeon) — **Rung 2**

> ✅ **Checkpoint A (brief approved, 2026-07-20):** build **Rung 2** — a public **Lambda Function URL** capture endpoint that the digest/report "Mark applied" links hit → `track_application_event`; auth = a **short-lived HMAC-signed token** scoped to `{posting_id, status}` (TTL, signing key in Secrets Manager), mirroring the v0.10.0 presigned-report pattern. Ship the **Rung-1 report hint alongside**. Build in progress via the squad; the PR (checkpoint B) and the live deploy remain human checkpoints.

> The pipeline scores jobs, emails them, and forgets — it never learns whether a job it rated 90 led to an application, an interview, or nothing. It has **zero ground truth**, so scoring can't be measured or calibrated.

## The problem
Every day the tool produces a scored shortlist, but nothing flows *back*: did the user apply? interview? get rejected? The outcome log is effectively empty and human corrections are near-zero. Two consequences: (1) you can't answer *"are these scores actually any good?"*, and (2) **M7 scoring-calibration is impossible** — there is nothing to calibrate *toward*. This is the deeper bottleneck under the scorer work (v0.8.0 subscores, v0.11.0 boundary resample): all of it improves a number no one has ever confirmed is right.

## Does it exist? — verification
**Yes — measured live over the Aurora Data API, 2026-07-20** (read-only, re-runnable):

- **Evidence 1 — the outcome log is empty.** `application_event` (the append-only outcome table, migration 0005 / [ADR-0026](../../adr/0026-outcome-tracking-override-lineage.md)) has **0 rows** across the entire history.
  Reproduce (read-only): `aws rds-data execute-statement --resource-arn <cluster> --secret-arn <secret> --database jobfetcher --region us-east-1 --sql "SELECT count(*) FROM application_event"` → expected `0`.
- **Evidence 2 — human corrections are near-zero.** `score.score_override` is set on **1 of 286** scored rows.
  Reproduce: `… --sql "SELECT count(*) FILTER (WHERE score_override IS NOT NULL) AS overrides, count(*) AS total FROM score"` → expected `overrides=1, total=286`.
- **Magnitude: 100% of scores are unlabeled; 0 outcomes ever recorded.** There is no dataset — not a small one — to measure accuracy against. This is a total absence, not a thin signal.
- **Re-verified 2026-07-20 via `/investigate`:** both counts unchanged (`application_event=0` · `score_override 1/286`); the code claims below hold (`track_application_event` exists as the reuse point, the panel Curate tab is present, `render_digest` is a fix surface). The dossier is complete + current → advanced to `handoff-ready`.

## Mechanism (root cause)
The *capability* to record outcomes exists — `scripts/track.py` (`applied|interview|offer|rejected|withdrawn` + `override`, migration 0005) and now the v0.12.0 control-panel Curate tab ([ADR-0033](../../adr/0033-local-control-panel.md)). The bottleneck is **friction at the moment of the action**:

- Recording is a **separate, deliberate step** — a terminal command (`track.py applied <posting_id>`, and you must first `find` the id) or opening a local Streamlit app. The user applies to a job *in their browser, from the email*; they will not context-switch to a terminal/app afterward.
- The benefit is **delayed and invisible** (it only pays off later, in analytics that don't exist yet), so the friction ≫ the perceived reward → the log stays empty. Root cause: **there is no capture affordance where the user already is** (the digest / the full-list report). `scripts/track.py` is the only capture path and it's CLI-only.

## Blast radius
- **Changes:** a **capture surface** at the point of action — the digest email ([`core/notifier.py`](../../../src/jobfetcher/core/notifier.py) `render_digest`) and/or the full-list report page ([`core/report.py`](../../../src/jobfetcher/core/report.py)) gain per-job "Mark applied / interview / …" affordances, backed by a small **write endpoint** that calls the existing `Repository.track_application_event`.
- **Must NOT change:** the **append-only** `application_event` schema (0005) and the `APPLICATION_STATUSES` vocabulary; the scoring lineage; no new outcome fields on the scoring pipeline.
- **Unaffected:** fetch → silver → gold → score → notify (the capture is a *return* path, orthogonal to the forward pipeline).

## Fix plan (the handoff guideline)
The single high-leverage move is **capture at the moment of action**. Reuse the existing write path — `Repository.track_application_event(*, posting_id, status, note=None)` (keyword-only args; [`repository_postgres.py:481`](../../../src/jobfetcher/adapters/repository_postgres.py)) — which already validates both the **status** (rejects anything outside `APPLICATION_STATUSES` before touching the DB) and that the **posting exists** (rolls back → zero rows written), raising `RepositoryError` on either. Rungs:

1. **Rung 0 (already shipped) — the v0.12.0 panel Curate tab** cut the friction vs the raw CLI, but it's still a *separate local app*. Not sufficient alone (the log is still empty).
2. **Rung 1 (minimal, non-crucial) — reduce the residual friction:** make the panel the obvious path (surface it in the digest footer + docs) and/or have the full-list report emit a ready-to-paste `track.py applied <id>` per row. Cheap, no infra — but it doesn't fully close the "one click from the email" gap.
3. **Rung 2 (recommended — the real friction-killer, CRUCIAL) — a capture endpoint the email/report links to:** a tiny **AWS Lambda Function URL** (or API Gateway route) that the "Mark applied" links hit → `track_application_event`. One click from the inbox → a row lands. **Open question the Surgeon must resolve:** auth on a public endpoint — a **short-lived signed token** in the link (mirroring the v0.10.0 presigned-report pattern) scoped to `{posting_id, status}`, so a stray click can't be forged. New infra (a Lambda + URL + IAM) → CRUCIAL tier.
4. **Rung 3 (end-state, deferred) — inline outcome actions in a hosted dashboard** ([ADR-0024](../../adr/0024-query-via-export.md) end-state) — the B-1-rung-3 surface. Bigger build; not now.

**Recommendation:** Rung 2 — it's the smallest change that actually closes the loop (one click from where the user already is), reuses the whole existing write + validation path, and adds exactly one small surface. Ship Rung 1's report-side hint alongside it as the zero-cost interim.

## Validation gate
| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | Clicking "Mark applied" for posting P in the digest/report → **exactly one** `application_event` row for P with `status='applied'` (`SELECT status,count(*) FROM application_event WHERE posting_id='P'` → `applied,1`); the next `export.py`/panel shows P's latest status = applied. | A **forged/expired token** (or unknown posting / bad status) → **zero rows written** + a clear rejection — reuse `track_application_event`'s existing validation (it rejects unknown posting/bad status with `RepositoryError`, zero rows). |
| VG-b | Recording a second, later status (e.g. `interview`) for P → a **new** append-only row; the latest-status read returns `interview` (history preserved, not overwritten). | Double-clicking the same link → at most one *intended* transition; a replayed token past its TTL is rejected (no duplicate spurious row from a stale link). |

## Out of scope / rejected
- **A hosted dashboard now** (Rung 3) — deferred ([ADR-0024](../../adr/0024-query-via-export.md) end-state).
- **Auto-inferring outcomes** (e.g. scraping ATS status) — no reliable signal; the tool deliberately doesn't touch external ATS.
- **New outcome columns on the scoring pipeline** — keep the append-only `application_event` as the single outcome log; the capture only *writes* to it.
- **Building the M7 calibration loop in the same unit** — this dossier unblocks it (supplies the labels); calibration is its own later unit.

## Connections (typed — the graph seam)
- `caused-by` → `file:scripts/track.py` (outcome capture is CLI-only, high-friction)
- `blocks` → M7 scoring calibration (no ground truth to calibrate toward)
- `blocks` → "measure scoring accuracy" (no labels → no accuracy metric)
- `touches` → `file:src/jobfetcher/core/notifier.py`
- `touches` → `file:src/jobfetcher/core/report.py`
- `relates-to` → [ADR-0026](../../adr/0026-outcome-tracking-override-lineage.md) (the outcome/override lineage this feeds)
- `relates-to` → [ADR-0033](../../adr/0033-local-control-panel.md) (the panel Curate tab — the partial, still-separate mitigation)
- `relates-to` → [B-3 companion](../../ledgers/backlog.md) (where this was first named)

## Handoff
- **Severity tier:** `crucial` for the recommended Rung 2 (a public capture endpoint = live infra + a new surface + auth) → human checkpoints: the brief/rung decision **and** the PR before merge; the deploy is always a checkpoint. Rung 1 alone is non-crucial.
- **Ready-for-Surgeon checklist:** verified ✅ (live, 2026-07-20) · root-caused ✅ · fix plan (rungs + recommendation) ✅ · validation gate (behavioral + negative) ✅ · out-of-scope ✅ · typed connections ✅. **Open decision for the human:** which rung, and (Rung 2) the token/auth approach.
- **On fix:** the **Resolution** section below is filled at close → set `status: fixed`.

## Resolution — as-built _(filled at close)_
> ✅ **Shipped + live-validated 2026-07-20.** Merged PR #34 → `terraform apply` (8 add / 2 change / 0 destroy) → the public capture endpoint is live at a `*.lambda-url.us-east-1.on.aws` URL. Pipeline smoke `200` @ `alembic 0006_subscores`. **Capture flow validated live (non-polluting):** no-token → **400**, forged signature → **400**, expired token (real key) → **400**, and a **valid token signed with the real Secrets-Manager key for an unknown posting → 404 with zero rows** (proves the key→verify→`track_application_event` wiring end-to-end without writing a spurious outcome). The positive row-write is covered by the 17 unit tests + the first real digest click.

- **What shipped:** Rung 2 — a **public AWS Lambda Function URL** the digest/report "✓ Mark applied" links hit → verifies a short-lived HMAC token → records ONE outcome via the existing `Repository.track_application_event` (append-only `application_event`). One click from the inbox now lands a row where before the only path was `scripts/track.py`. Auth is the **token, not the network**: `verify` runs before any DB touch, so a forged/expired token returns 400 with **zero rows**. Per-job links are injected into the digest + full-list report; unconfigured → no link (graceful).
- **Rung taken · divergence from the Fix plan:** **Rung 2** as recommended, **plus** Rung 1's report-side surface (a per-status "Mark" column on the full-list page). No divergence from the plan; the open auth question was resolved to the **HMAC-signed-token** approach (30-day TTL, scoped to `{posting_id, status}`, key **Terraform-generated** in Secrets Manager). GET-prefetch handled by **human decision at Checkpoint B**: ship one-click GET as-is; a **confirmation-interstitial fast-follow** is the documented mitigation if spurious rows ever appear.
- **Key files + decisions:** `core/capture_token.py` (pure HMAC sign/verify — constant-time, empty-key-safe, status-vocab-checked) · `handlers/capture.py` (the Function URL handler — same zip, different entry point; module-cached signing key; correlation-ID logging; verify-before-DB) · `core/notifier.py`/`core/report.py`/`core/ingest.py` (pure injected `capture_link` callable) · `handlers/pipeline.py` (`build_capture_link` threaded into `notify`, lazy-imported to break a cycle) · `terraform/capture.tf` (public Function URL `auth=NONE`, least-priv IAM, `random_password` signing key). Decision record: [ADR-0035](../../adr/0035-outcome-capture-endpoint.md). **Deploy note:** the intended `reserved_concurrent_executions=5` cap was **removed at apply time** — this account's total concurrency limit is 10 and AWS requires ≥10 *unreserved*, so any reservation is rejected; the account's 10-execution ceiling is the de-facto cap, and the DB stays protected (verify-before-DB) regardless.
- **Links:** PR #34 · [ADR-0035](../../adr/0035-outcome-capture-endpoint.md) · CHANGELOG `[Unreleased]` (batches into the next `v0.12.x`) · merge commit `f7a121a`.
- **Extending / editing later:** the **capture endpoint accepts any `APPLICATION_STATUSES` value** — richer per-status actions (interview/offer/rejected) in the digest are a pure render change (the endpoint already supports them). The **confirmation-interstitial fast-follow** (defeat GET-prefetch): render an HTML "Confirm" page on the GET and only write on a POST — the `verify → track_application_event` core stays; add a POST branch in `handlers/capture.py`. The **hosted-dashboard end-state** (B-1 rung 3) would reuse this same write path. **Gotcha:** the pipeline Lambda (signer) and the capture Lambda (verifier) must share the *same* signing-key secret; a Terraform key rotation invalidates in-flight email links until the next digest re-signs.
