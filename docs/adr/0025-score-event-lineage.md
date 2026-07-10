# ADR-0025 — Append-only `score_event` lineage + a reassess age bound

**Status:** Accepted · shipped v0.7.0 (2026-07-08)
**Date:** 2026-07-07

## Context

`save_score` upserts the `score` row in place — the current-view design is right for the digest and the export, but it means **every re-score destroys the prior judgment**: strengths, gaps, `strategic_assessment`, and `scored_at` are overwritten, and only *one* generation of `previous_score` survives. The `score` table also carries **no lineage** — no `scoring_model`, no `profile_hash`, no `run_id` — and the `profile` row is overwritten from config every run (the v0.3.0 write-once fix), so a score **cannot be joined to the profile that produced it**. Because DeepSeek scoring is **non-reproducible even at temp 0** (the VG3 best-effort finding), overwritten history is *irrecoverable* — you can't replay bronze to get yesterday's score back.

**Measured magnitude (Investigator-verified, live DB):** after the single 2026-07-06 reassess, **100% of the 180 scored postings had `previous_score` set** — the original narratives were *already gone*, and the original numeric scores were **one more reassess-invoke away from permanent loss**. This directly undercuts two named follow-ons ("45→62→78 over time", ADR-0023/0024) and the M7 calibration hypothesis, which need history that no longer existed.

A second, smaller finding rode along: `get_scored_for_reassess` had **no age filter** — reassess cost grows linearly with history and pays LLM tokens to re-score months-old, almost-certainly-filled postings forever.

## Decision

Keep `score` as the **current view** (every read path untouched) and add an **append-only `score_event` log** written in the same transaction — plus an age bound on reassess. Ships as migration **`0004_score_event_lineage`** (chains to `0003_run_log_send_guard`); shipped in **v0.7.0**.

- **The `score_event` table** — one immutable row per scoring event: `event_id` (serial PK, server-assigned), `cluster_id` (FK, NOT NULL), `score` + `fit_category` (NOT NULL), `strengths`/`gaps` (JSONB), `strategic_assessment`, `poster_type`, `legitimacy_verified`, `previous_score`, **`scoring_model` + `profile_hash` (NOT NULL — an event is never written without its provenance)**, `run_id`, `scored_at` (timestamptz, default `now()`); indexed on `cluster_id` and `run_id`. Plus a nullable **`profile.profile_hash`** column (which profile+knobs the row was last synced from).
- **Baseline backfill** — the migration inserts **one synthetic event per existing complete `score` row** (`scoring_model`/`profile_hash` = `'pre-0004'`), rescuing the 180 current scores into the log *before* the next reassess erases them; hollow rows (NULL cluster_id/score/fit_category, possible under the constraint-free v0 DDL) are skipped by a `WHERE` guard.
- **`save_score` dual-writes** — the (unchanged) `score` upsert + a `score_event` INSERT in **one `engine.begin()` transaction**: either failure rolls back both, so the current view and the history can never diverge. New **required** kwargs `scoring_model` + `profile_hash`; optional `run_id`. **Data-API hardening:** the event INSERT is `.inline()` with `implicit_returning=False` on the table — a **plain single-statement INSERT with no RETURNING and no PK prefetch** (`select nextval(...)`), deliberately avoiding two SQL surfaces the aurora-data-api dialect has never exercised in this project ([ERR-004/005](../ledgers/errors.md): Data-API-only paths are exactly where deploy-only bugs live).
- **Lineage threading** — `Scorer.model_id` is threaded through `score_gold`/`reassess`; the handler computes **`profile_hash` = sha256 of the sorted-keys JSON of the profile dump + the 3 strictness knobs** at the profile-sync spot, stores it on the `profile` row, and stamps it on every event — so any score joins to the exact profile *content* that judged it, across runs and machines.
- **Reassess age bound** — `get_scored_for_reassess(max_age_days=...)`: when > 0, postings are aged by **`COALESCE(posting.fetched_at, bronze_posting.fetched_at)`** via a LEFT JOIN on the existing bronze lineage FK (`posting.fetched_at` is NULL on all live rows — nothing writes it — so bronze's NOT NULL default-`now()` timestamp is the *effective* age source and the bound actually bites); **unknown-age rows are INCLUDED** (NULL-age = unknown, not old — a row can never silently drop out of reassess forever); the cutoff is a **bound parameter**, never interpolated SQL; **`0`/`None` = unbounded**, emitting a query string-identical to the previous one. The method is now declared on the **`Repository` Protocol** in `ports.py` (closing a pre-existing v0.4.0 omission).
- **New REQUIRED `SearchSpec` field `reassess_max_age_days`** (`ge=0, le=365`; `0` = unbounded) — required like every other knob (the SearchSpec every-field-explicit/fail-loud contract); the sample YAML recommends **45**.
- **`scripts/export.py`** now exports a **`score_events`** table in the SQLite snapshot (the score-delta/provenance index next to the flat `jobs` current view).

## Alternatives considered

- **A DB trigger writing the event log.** Rejected: less testable, hides the write path from the `Repository` port (ADR-0018's whole point), and adds dialect risk on the Data API — exactly the surface ERR-004/005 proved is only provable live.
- **Lineage columns on `score` itself.** Rejected: still destroys history on every upsert — lineage without the event trail solves half the problem and the cheaper half.
- **Reconstructing history by replaying bronze.** Rejected: impossible — LLM scoring is non-deterministic even at temp 0 (the VG3 finding), so past scores are not reproducible. History must be *recorded*, not re-derived.
- **Full event-sourcing (drop the `score` current view).** Rejected: churns every read path (digest, export, reassess set) for zero present benefit — anti-P1. The current-view + append-log pair is the minimal shape.
- **Making `reassess_max_age_days` optional-with-default.** Rejected: violates the `SearchSpec` every-field-explicit / fail-loud contract (the same rule that made `hard_floor`/`near_miss_band` required in v0.3.0).

## Consequences

- **History is permanent from here on** — every scoring/reassess appends; nothing is overwritten. The **M7 calibration loop and the funnel/trend analytics (M5–M6) gain their data source**: score-over-time per cluster, per-model and per-profile-version cohorts, the "45→62→78" chart named in ADR-0023/0024 — realized as designed, when the tool honestly produced the need.
- **An honest asymmetry (recorded, not hidden):** an event's `previous_score` records **what that `save_score` call received** — a fresh `score_gold` scoring writes event `previous_score=NULL` even if the score-row upsert carries an old score into the *row's* `previous_score`. Deltas are recoverable from **event order** regardless (the log, not the field, is the history).
- **⚠️ Deploy sequencing (breaking config change):** `reassess_max_age_days` is REQUIRED and the runtime config lives in S3 ([ADR-0022](0022-runtime-config-in-s3.md)) — **deploying this code without pushing config first makes every subsequent run fail loudly** (`SearchSpec` ValidationError) until the config is pushed. The deploy order is: **update the local config YML → `scripts/push_config.py` → deploy/invoke.** (Registered in the [procedure registry](../ledgers/procedure-registry.md).)
- **Residual live-validation item:** the first `save_score` over the **Aurora Data API** after this change must be watched in the release's live smoke — the inline no-RETURNING INSERT is compile-verified, but the Data-API path is only provable live (the ERR-004/005 lesson).
- Verified: 272 unit green; 31 integration passed + 5 live-key skips (local Postgres, 2026-07-07); 94.76% full-suite coverage (85% floor); `ruff` clean; independent fresh-context adversarial + integration review passed with zero blocking defects.

Full reasoning: [journal](../01-session-decision-journal.md). Related: [ADR-0023](0023-reassess-replay.md) (the replay this protects + the deferred score-history now built), [ADR-0024](0024-query-via-export.md) (the export surface it extends), [ADR-0018](0018-persistence-sqlalchemy-data-api-repository.md) (the `Repository` port), [ADR-0022](0022-runtime-config-in-s3.md) (the config-push flow the deploy order depends on).
