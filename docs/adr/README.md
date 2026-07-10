# Architecture Decision Records

Each ADR records one significant decision: **what was chosen, what was rejected, and why.** The rejected alternative is the point — it proves the tradeoff was evaluated. Format: Status · Context · Decision · Alternatives Considered (≥2, with project-specific rejection reasons) · Consequences.

These are the **foundational** decisions made during the design session — the ones that govern the whole project regardless of which migration is in flight. **Migration-specific implementation decisions get their own ADR when that migration is planned** (just-in-time, per the [roadmap](../03-roadmap.md)) — e.g. scoring-weight tuning (M-score), Step-Functions topology (M3), Snowflake adoption (if/when its bottleneck appears).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-evolutionary-architecture.md) | Evolutionary architecture: minimal v0 + bottleneck-driven migrations | Accepted |
| [0002](0002-tool-minimalism-wins.md) | Tool-minimalism is the gate; DE-depth is the tiebreaker | Accepted |
| [0003](0003-postgres-over-dynamodb.md) | PostgreSQL as the operational store (over DynamoDB) | Accepted |
| [0004](0004-warehouse-strategy.md) | Analytics: dbt-on-Postgres default; Snowflake conditional (over Databricks) | Accepted |
| [0005](0005-dedup-cluster-and-surface.md) | Deduplication: cluster-and-surface, never hide | Accepted |
| [0006](0006-cv-renderer.md) | CV rendering without LibreOffice-in-Lambda | Accepted |
| [0007](0007-self-hosted-distribution.md) | Self-hosted / open-source distribution (not SaaS) | Accepted |
| [0008](0008-region-us-east-1.md) | Region: us-east-1 | Accepted |
| [0009](0009-batch-not-debezium-v0.md) | Batch EL now; Debezium CDC as a documented scale-path | Accepted |
| [0010](0010-job-source-jsearch.md) | Job source: JSearch (probe-free → Pro), single-source for v0; Adzuna deferred | Accepted |
| [0011](0011-dimensional-analytical-model.md) | Analytical model: insight-driven dimensional (constellation) schema; grow per question | Accepted |
| [0012](0012-model-agnostic-llm.md) | Model-agnostic LLM; model id + base_url in config (swap models/providers freely) | Accepted |
| [0013](0013-enforcement-gate-trio-branch-pr.md) | Enforcement: gate-trio slash-commands (`/start-step` · `/review-step` · `/close-step`) + branch/PR workflow | Accepted |
| [0014](0014-operational-store-aurora-serverless-data-api.md) | Operational store: Aurora Serverless v2 + RDS Data API (no VPC); resolves D-v0-1 | Accepted |
| [0015](0015-type-replaceable-pipeline-stages.md) | Type-replaceable pipeline stages: every stage = a config-selected strategy behind a port | Accepted |
| [0016](0016-llm-dissection-at-silver.md) | LLM dissection at silver (every posting) → structured fields for the market-wide dimensional tables | Accepted |
| [0017](0017-llm-transport-openai-compatible-deepseek.md) | LLM transport = OpenAI-compatible API; v0 provider = DeepSeek (Bedrock parked, ERR-001 mitigated) | Accepted |
| [0018](0018-persistence-sqlalchemy-data-api-repository.md) | Persistence access: SQLAlchemy + aurora-data-api dialect, behind a `Repository` port | Accepted |
| [0019](0019-agentic-build-orchestration.md) | Agentic build orchestration: per-unit gate pipeline (builder→review→scribe→guardian) + cross-unit fan-out | Accepted |
| [0020](0020-lambda-deployment-packaging.md) | Lambda deployment packaging: vendor Linux wheels via `pip --platform` (no Docker), bundle + prune boto3, direct zip | Accepted · ✅ Validated live (v0.1.0) |
| [0021](0021-m1-pipeline-hardening.md) | M1 pipeline hardening: LLM retry+jitter & symmetric isolation · in-Lambda concurrency + deadline guard · subset-title gold filter + selectable LLM filter | Accepted · ✅ Validated live (v0.2.0) |
| [0022](0022-runtime-config-in-s3.md) | Runtime config in S3 (not bundled): the Lambda reads the search spec + profile from S3 each run → change settings via `push_config.py`, no redeploy | Accepted |
| [0023](0023-reassess-replay.md) | Reassess/replay: a `{"mode":"reassess"}` re-scores existing jobs against the updated profile with **no re-fetch** (immutable-bronze replay) → jobs graduate as skills grow | Accepted |
| [0024](0024-query-via-export.md) | Query/filter via `scripts/export.py` → SQLite/CSV opened in Datasette/DB-Browser/Excel (not a custom UI) — filter/search/organize for free | Accepted |
| [0025](0025-score-event-lineage.md) | Append-only `score_event` lineage: `save_score` dual-writes an immutable history row (scoring_model + profile_hash + run_id) in one transaction — re-scores never erase judgments — + a `reassess_max_age_days` age bound on replay | Accepted |
| [0026](0026-outcome-tracking-override-lineage.md) | Outcome tracking: append-only `application_event` log (applied/interview/offer/rejected/withdrawn via `scripts/track.py`) + human score overrides dual-written into the `score_event` lineage (`scoring_model='human-override'`) — the pipeline finally records what happens *after* the digest | Accepted |
| [0027](0027-digest-truthfulness.md) | Digest truthfulness: the daily email splits **"new since last digest"** (a fresh `scored_at` vs the last `run_log.digest_sent_at` that is actually news — first scoring or graduation, badged `↑ old→new`) from a compact "still open" list, collapses same-fingerprint duplicates render-time to one card (`seen n× — scores lo–hi`), and bounds the digest with a REQUIRED `digest_max_age_days` knob — cluster-attach dedup deferred to M2 | Accepted |
| [0028](0028-scorer-subscores-shadow.md) | Scorer subscores + SHADOW code-total: the 7-factor weights written down for the first time (**`FACTOR_WEIGHTS`** .30/.20/.15/.15/.10/.05/.05, Tarig's PDF-derived formula, module-load sum==1.0) — the prompt demands `score` FIRST (anti-anchoring) + 7 per-factor subscores; the weighted `code_total` (banker's rounding) is logged + persisted (`subscores` JSONB on `score`/`score_event`, migration 0006) but **never the product number** — cut-over is an M7 decision; reassess gains the `delta_buckets` variance instrument. **⚠️ migrate 0006 BEFORE deploy (run-fatal class)** | Accepted |
| [0029](0029-ops-hardening.md) | Ops hardening (Squad Run 5, final): **S3 remote state** (deliberately **unmanaged** `jobfetcher-tfstate-*` bucket — the state must survive `terraform destroy`; TF ≥ 1.10 native `use_lockfile`, no DynamoDB; one-time human-present migration, [runbook §3](../runbooks/deploy.md)) + **two alarms → one SNS topic → email** (a **dead-man** on the daily rule — `RuleName` dim, 86400 × 1 `Sum < 1`, missing = breaching, worst-case ~2-day lag documented honestly — + **Lambda Errors** 3600 × 1, notBreaching; a returned `statusCode`-500 is NOT counted — future custom metric) + the **`{"mode":"smoke"}`** post-deploy gate (Data-API connect + `alembic_version` vs `$ALEMBIC_HEAD`; 200/400/500; zero side effects — the ERR-004/005 watch-item cycle becomes a mechanical gate); `scripts/invoke.py` explicitly decided against (procedure-only) | Accepted · ✅ shipped v0.9.0 |
| [0030](0030-reachable-full-list-from-digest.md) | Reachable full job list from the digest: the still-open overflow + below-threshold footer become a **presigned S3 link** to a self-contained, filterable HTML page of ALL scored jobs; new `core/report.py` + `S3ReportStore` + `Repository.get_all_scored`, built inside a **non-fatal** `notify` guard; no migration/IAM/dep. The first bottleneck shipped by the autonomous agentic squad | Accepted · ✅ shipped v0.10.0 |

> Full reasoning narrative: [01-session-decision-journal](../01-session-decision-journal.md). Crisp decision list: [ledgers/decisions-locked](../ledgers/decisions-locked.md).
