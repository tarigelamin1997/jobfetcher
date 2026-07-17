# ADR-0032 — Full S3 audit persistence (every stage's procedures + results to S3)

**Status:** Accepted · **✅ shipped v0.12.0 U1 (merged PR #32, deployed 2026-07-17)** · the first of v0.12.0's two units (U2 = the local control panel, [ADR-0033](0033-local-control-panel.md)) · built by the agentic squad (implement → Examiner **CLEAN PASS** → deploy)

## Context
Only two things reached S3: the immutable **bronze raw JSON** (`raw/…` via `S3RawStore`) and the rendered **full-list HTML report** (`reports/…` via `S3ReportStore`, a presentation artifact). Every *derived* result — the silver dissections (`posting`), the gold decisions (`cluster`/status), the scores (`score` + `score_event` + subscores) — lived **only in Aurora**, and the handler's per-run summary (statusCode · per-stage counts · `partial` · reassess graduations/deltas) was persisted **nowhere** (the invoke response + CloudWatch logs only). So the pipeline's *procedures and results* were not independently durable or replayable outside the operational DB, and "what happened on run X" survived only as log lines that age out.

## Decision
Persist **every stage's structured procedures + results to S3** alongside Aurora, as a new **`adapters/s3_audit.py` → `S3AuditStore`** (mirroring `s3_raw.py`/`s3_reports.py`: bucket from `$JOBFETCHER_DATA_BUCKET`, ambient IAM, injectable client for tests). Run context (`run_id`, `run_date`) is bound at construction. **Batched JSONL — one object per stage per run** (a handful of PutObjects, not one per posting):

- `silver/{run_date}/{run_id}.jsonl` — one `DissectedPosting` (`model_dump(mode="json")`) + `posting_id`/`bronze_id` per line
- `gold/{run_date}/{run_id}.jsonl` — one filter decision per candidate (`posting_id`, `cluster_id`, `likely_fit` = the strategy's verdict, `promoted` = reached gold)
- `scores/{run_date}/{run_id}.jsonl` — one `ScoreResult` + `posting_id`/`cluster_id`/`fit_category` per line (from **both** `score_gold` and `reassess`, the latter also carrying `previous_score`)
- `runs/{run_date}/{run_id}.json` — the handler's run-summary dict (the procedure record that was logs-only)

**Non-fatal by contract:** serialize **and** put run inside a single `_guarded_put` — any failure (S3 error *or* a serialization error) logs a warning and returns `None`. An audit write can **never** fail a run; the run's DB writes + email are independent. This mirrors the [ADR-0030](0030-reachable-full-list-from-digest.md) `notify` report guard, moved to the store boundary so every call site is inherently non-fatal — even the store's *construction* is guarded in the handler.

**Additive + concurrency-safe:** four defaulted `audit_store=None` params on `ingest`/`apply_gold_filter`/`score_gold`/`reassess` (unset ⇒ byte-for-byte prior behavior — every existing test + local run). Records accumulate on the **main thread** (next to the `repo.save_*` write, after the `ThreadPoolExecutor` join), never on a worker — the H-2 rule holds. Smoke mode writes nothing (its zero-side-effects contract is untouched). An **empty** stage writes no object (the run summary records the zero, so an absent object is unambiguous).

**No migration, no IAM/Terraform change, no new dependency** — the Lambda role already grants `s3:PutObject` bucket-wide, `$JOBFETCHER_DATA_BUCKET` is already set, `alembic head` stays `0006_subscores`.

## Alternatives Considered
- **Per-posting objects** (mirroring bronze `raw/{id}.json` for every stage). Rejected: at 10–30 jobs/day it multiplies PutObjects + latency for no benefit; a batched JSONL per stage per run is a complete audit at a fraction of the calls.
- **Averaged into the DB / a new `run_metrics` table** for the run summary. Rejected here: the ask is an *S3* audit trail (durable, replayable, outside the operational DB, cheap); a DB table is a schema migration for the one artifact (the summary) and doesn't cover the stage results. Keep the audit in S3; the DB stays the operational store.
- **Guard only the S3 put (serialization outside the guard).** Rejected during review (Examiner): a serialization error would then propagate and 500 the run — the guarantee must be absolute, so `_guarded_put` wraps serialize + put together.
- **A shared base class for the three S3 stores** (DRY the bucket/boto3 boilerplate). Deferred (P1): the duplication is small and mirrors the established convention; a base earns its place only if a fourth store appears.

## Consequences
- **Easier:** the full medallion (silver/gold/scores) + the per-run procedure record are now durable + replayable in S3, independent of Aurora — an audit trail for "what did run X actually do", and a foundation the future analytics/warehouse work (M5) can read without touching the operational DB.
- **Bounded cost:** ≤4 PutObjects per run (empty stages skip) — negligible; versioning stays OFF (cost/teardown friction — a documented later option if the audit needs history).
- **Coverage note (honest):** the audit records **persisted results only** — `skipped`/`deferred`/`already`/`failed` postings appear as run-summary *counts*, not as per-item records (so the trail explains *how many*, not *which*, were dropped). Consistent with the design; revisit if a specific-posting forensic need appears.
- **Live-validated (2026-07-17, shipped v0.12.0 U1):** code-only deploy (terraform 1 change in-place), smoke `200 @ 0006_subscores`, then a live run wrote `runs/{date}/{run_id}.json` (the previously logs-only summary) + `gold/{date}/{run_id}.jsonl` (36 decision records, correct shape) to S3; `silver`/`scores` use the identical proven `S3AuditStore` path (CI-integration-covered). Examiner CLEAN PASS — all six invariants (non-fatal, main-thread concurrency, smoke-writes-nothing, additive-when-None, record correctness, empty-batch) confirmed.
