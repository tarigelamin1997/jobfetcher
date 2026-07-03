# Changelog

All notable changes to JobFetcher are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); the project ships **semantic-versioned releases** ([roadmap](docs/03-roadmap.md), [ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)): a **minor** bump (`v0.x.0`) per migration / real capability, a **patch** bump (`v0.x.y`) for small fixes + improvements between migrations, and `v1.0.0` at M8. **v0.1.0 shipped 2026-06-29.**

The ***why*** behind every entry is the [session decision journal](docs/01-session-decision-journal.md); formal decisions are [ADRs](docs/adr/); the raw reasoning trail is the [working document](docs/session-log/working-document.md).

## [Unreleased]

*Next migration candidate (P2): the **digest email UX** — the format is poor and the job apply-links must be visible.*

## [v0.3.1] — 2026-07-03 — employment_types fix (first patch release)

### Fixed
- **`employment_types` was a silent no-op with no validation** — it was typed `list[str]` (any string accepted) **and** never actually passed to the JSearch query. Now it's an **`EmploymentType` enum** (`FULLTIME`/`PARTTIME`/`CONTRACTOR`/`INTERN`) — a typo fails **loudly at config-load** (like `date_posted`/`remote`) — **and** it's wired into the `/search` request, so setting it actually narrows results. `[]` still means no filter.

## [v0.3.0] — 2026-07-03 — user-customizable settings (no redeploy)

**Toward "fully customizable per user."** The job-seeker settings became genuinely user-owned + editable without a rebuild — a settings change is now one command, not a deploy.

### Added
- **The 3 shortlist-strictness knobs are now user config** — `threshold` + `hard_floor` + `near_miss_band` are all fields on the `SearchSpec` (before, only `threshold` was; floor/band were code constants), validated `hard_floor <= threshold`. ([#18])
- **[ADR-0022] Runtime config in S3** — the two config YAMLs moved out of the Lambda zip; the handler reads them from **S3 at runtime**. **Change a setting = edit the YAML + `python scripts/push_config.py`** (validates then uploads) → the next run uses it, **no rebuild/redeploy**. `SearchSpec`/`Profile` gain `from_yaml_text`; new `adapters/s3_config.py` (`S3ConfigStore` + `read_config_text` s3://-or-local dispatch); Terraform seeds the config to S3 with `ignore_changes` (never clobbers a runtime edit); the build no longer bundles config. ([#19])

### Fixed
- **The write-once trap** — the handler seeded the `profile` row only on the first run, freezing the entire profile + knobs; it now **re-syncs from the config every run**, so editing a config file actually takes effect. ([#18])

### Validation (live, 2026-07-03)
- **Filter-only change:** threshold **60 → 95 → 60** via `push_config.py` only — the DB `profile` row re-synced each time and the shortlist tracked it (**21 → 1 → 27** jobs), two digests delivered. **Zero redeploy.**
- **Fetch-driving change:** `countries` GCC → **Egypt** → **8 real Egyptian Data-Engineer jobs fetched** (a country never in the DB before), proving `job_titles`/`countries` drive the live JSearch query from S3, not just a re-filter. **Zero redeploy.**

## [v0.2.0] — 2026-07-02 — M1: pipeline hardening

**The bottleneck-driven first migration.** The P2 protocol ran the tool live on the full 18-query GCC sweep and measured three real bottlenecks (overruling the pre-drawn *M1 = CV tailoring* hypothesis): serial throughput that timed out, a single provider `503` that killed a whole run, and a gold filter loose enough to pay the pro-model to reject obvious junk — plus AWS blind-retrying the dead run. All fixed and **re-validated live on the exact workload that failed** ([ADR-0021](docs/adr/0021-m1-pipeline-hardening.md)).

### Changed
- **Throughput (H-2):** silver dissection + scoring now run their LLM calls on a bounded **`ThreadPoolExecutor`** (default 8, `$PIPELINE_MAX_WORKERS`); **all DB writes stay on the main thread**. A **deadline guard** (`context.get_remaining_time_in_millis()` − 60s) stops starting new work before the timeout, returns `partial: true` with a `deferred` count, and skips notify so the idempotent re-run sends the digest. **Measured ~13× faster (~1.1→~14–15 dissections/min); a run can no longer time out.**
- **Precision (H-3):** the deterministic gold filter now requires **all** of a target title's tokens in the posting title ("Data Architect" → `data`+`architect`), not any single shared token; the built `LlmFilterStrategy` is now selectable via `$GOLD_FILTER_STRATEGY`. **The six live junk titles (Alliances Manager, Computer Vision Engineer, …) are eliminated.**
- **Lambda infra:** `memory_size` 512→**1024 MB** (CPU scales with memory for the worker threads); **`aws_lambda_function_event_invoke_config { maximum_retry_attempts = 0 }`** codifies the fix for the async zombie-retry.

### Added
- **Retry + jitter (H-1):** `OpenAICompatLlmClient.complete()` retries only transient failures (429/5xx + connection/timeout) with exponential backoff + full jitter (`LlmConfig.max_retries`, `backoff_base_s`); auth/model-not-found fail fast. `land_silver` now isolates `LlmError` symmetrically with `score_gold` — one blip skips one posting, never the run.
- **[ADR-0021]** (M1 pipeline hardening, with the measured before/after table + the honest M3 caveat); **ERR-006** (503 no-retry crash) + **ERR-007** (async auto-retry re-fetch) in the error log; new unit tests (retry policy, failure isolation, concurrency wall-clock, deadline deferral, subset-title, strategy resolution) — **212 unit + integration green**.

### Live validation (2026-07-02)
- Re-ran the ~132-posting backlog the pre-fix code died on: `statusCode 200`, backlog fully dissected + scored, **0 run-fatal errors** (15 dissect + 0 score failures isolated), and a **populated 21-job digest sent** — real GCC Data-Engineer roles scored 60–95 across all six countries. (Bonus finding: the market is *not* thin; the earlier "no matches" was the tiny Oman/Architect sample.)

## [v0.1.1] — 2026-06-29 — documentation refresh

An all-round documentation update over **[v0.1.0]** reflecting the deployed reality — **no pipeline change** (v0.1.0's code, including the two Data-API deploy fixes, stands).

### Changed
- **README** rewritten for the deployed + live-validated v0.1.0 (as-built flow, tech-stack table, how-to-deploy/run + the local test pyramid, the live-validation proof, the evolutionary roadmap).
- **`docs/02-architecture.md`** — the as-built deployed v0 (14-resource stack, RDS Data API, deterministic gold-filter default, the `run_log` send-once guard, the scale finding); **`docs/03-roadmap.md`** — v0 shipped, **M3 now evidence-backed** (the single-Lambda full-backfill limit), M1 = a hypothesis re-derived via the bottleneck protocol.
- **ADRs** — status touches on 0008 / 0014 / 0018 (validated live v0.1.0) + **NEW [ADR-0020]** (Lambda deployment packaging — Linux wheels via `pip --platform`, no Docker, boto3 pruned), indexed.
- **Ledgers** — interface-contracts → **shipped**; decisions-locked + the Aurora Data-API connect-param live-only contract + ADR-0020 + a v0.1.0-deployed row; procedure-registry + the Lambda-packaging/deploy procedure; build-plan Step 10 → the *actual* deploy (build-lambda · apply/migrate/invoke/validate/destroy · the 2 bugs · the tag).
- **Diagrams** — `docs/diagrams.md` reflects the as-built + shipped v0; a fresh **Eraser** v0.1.0 architecture diagram (personal/portfolio view, not committed).

## [v0.1.0] — 2026-06-29 — v0 SHIPPED

**The minimal working core is built, deployed, and live-validated end-to-end** — then torn down to ~$0. EventBridge → one Lambda: **JSearch fetch → S3 + Postgres (Aurora Serverless v2, RDS Data API) → DeepSeek dissect + 7-factor ATS score → SES daily digest**, with Terraform infra, Secrets Manager, the v0 test pyramid, and minimal CI. This is the whole v0 arc: **Steps 0–10** (probe · silver `Dissector` · schema + `Repository` · Terraform infra · fetch/silver landing · gold filter + `Profile` · Scorer · Notifier · single-Lambda handler · test round-up · CI · deploy + live run).

### 🚀 Shipped — the live validation (2026-06-29, the Step-10 deploy)

- **`terraform apply` → the 14-resource stack** (Aurora SLv2 + Data API, S3, Lambda, EventBridge, SES sender, least-privilege IAM); SES sender + recipient verified; schema created on Aurora via **`alembic upgrade head` over the Data API**.
- **The full pipeline ran end-to-end on real AWS:** invoke → `statusCode 200` → **fetched 10 → bronzed 10 → silvered 8 → gold 8 → scored 8 → notify sent**. Real UAE Data-Engineer postings scored (GSSTech, Contango, Michael Page, …). **Two emails delivered, SES 0 bounces:** a no-matches digest (threshold 60, all 8 below — **VG5 zero-path**) and, on an **idempotent re-run** (`already: 8` skipped — **VG4 live**), a populated 7-job shortlist (threshold lowered to 20 — **VG5 matches-path**). The `run_log` send-once guard recorded both runs.
- **`terraform destroy` → 14 destroyed**, independently verified GONE (Aurora / Lambda / S3); the two Secrets Manager keys preserved (reusable). Back to ~$0.

### Fixed — 2 deploy-only bugs the live run caught (PR #13)

Invisible to local psycopg2 tests + CI's `postgres` service — neither exercises the Aurora **RDS Data API** path:

- **`migrations/env.py`** — configparser choked on the `%`-encoded ARNs; escape `%` → `%%`.
- **`handlers/pipeline.py`** — the Data-API dialect's connect kwarg is **`aurora_cluster_arn`**, not `cluster_arn`; would break **every** deploy.
- **Lambda timeout 300 → 900s** — each posting ≈30s (an LLM call + a Data-API write).

### 📈 Scale finding (a genuine P2 bottleneck → reinforces M3)

The single Lambda **can't** run the **full** 18-query × 30-day backfill inside the 15-min max (throughput ≈30s/posting × hundreds of jobs); the daily **incremental** run (~10–30 new jobs) fits comfortably. The smoke run used a minimal config (1 country, 3 days). This is a real future migration signal → **M3 (Step Functions)**.

### 🏆 Milestones

- **2026-06-27 — v0 DATA PATH CODE-COMPLETE.** The full v0 pipeline now exists as code — **fetch → bronze → silver (LLM dissect) → gold (filter) → score → notify**. On `main`: Step 0 probe, C-1 silver `Dissector`, C-2 schema + `Repository`, C-3 Terraform infra, Step 4 fetch + bronze→silver, Step 4b gold filter + `Profile`, Step 5 Scorer. Step 6 (SES Notifier) is built and in **PR #8** (not yet merged). Remaining: Step 7 (single-Lambda wiring) · Step 8 (test round-up) · Step 9 (minimal CI) · Step 10 (deploy + first live run → tag `v0.1.0`). **153 unit + ~22 integration tests green; `ruff` clean.**
- **2026-06-26 — v0 DESIGN COMPLETE.** The v0 implementation plan, **19 ADRs**, and the storage-layer design are fully documented; the M1–M8 foundations are laid (directional, a living hypothesis). The first build unit is **live** (C-1 silver `Dissector`). Tagged `milestone/v0-design-complete-2026-06-26`. Next: the **agentic per-unit build** ([ADR-0019](docs/adr/0019-agentic-build-orchestration.md)), starting with C-2 (schema + `Repository`). `v0.1.0` is reserved for when the pipeline ships end-to-end.
- **2026-06-24 — THE LLM IS LIVE.** After weeks blocked by a Bedrock new-account daily-token quota of **0** ([ERR-001](docs/ledgers/errors.md)), the scoring/dissection path is unblocked. We stopped waiting on AWS and **routed around it** — the LLM transport now runs on the **OpenAI-compatible API with DeepSeek** (`deepseek-v4-flash`), verified end-to-end (`scripts/deepseek_smoke.py` → HTTP 200). The whole pipeline (bronze → silver dissection → gold → score) is now **live-runnable**, not blocked work. Full arc + reasoning: [journal §18](docs/01-session-decision-journal.md). ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md))

### Added

- **Step 10 — deploy + first live run (this release):** the 14-resource Terraform stack applied to real AWS, schema migrated over the Data API, the full pipeline run + validated end-to-end (see the live-validation block above), then destroyed to ~$0. Tagged **`v0.1.0`**.
- **Step 9 — minimal CI:** GitHub Actions on PR→main + push→main — 3 jobs: `ruff` + `alembic upgrade head` + `pytest --cov --cov-fail-under=85` (against `postgres:16-alpine`; live DeepSeek/JSearch tests skip without keys) · `terraform validate` (AWS-free) · **gitleaks** secret-scan (**VG7** — pre-commit + CI, blocks a planted fake key). Green CI → `main`'s required status checks.
- **Step 8 — test round-up:** the v0 pyramid complete + green — **180 unit + ~26 integration + ~3 live**, `ruff` clean, **89% full-suite coverage**; closed the two CI-invisible gaps (a **VG3 offline negative** — temp-≠-0 caught without a key — and **`SearchSpec` contract negatives**); added [`tests/README.md`](tests/README.md) (the VG1–VG8 → test traceability map) + `pytest-cov` tooling.
- **Step 7 — single Lambda `handler`:** `handlers/pipeline.py` wires `ingest → apply_gold_filter → score_gold → notify` behind one entry point — seeds the `profile` row, threads the correlation `run_id`, env-resolves the DB engine (local URL vs Aurora Data API) + config paths, `{statusCode, run_id, …counts}` summary, a stage failure → `500` → next run resumes. **VG4 idempotent** via upserts + a **`run_log` send-once guard** (migration 0003, PK `(run_date, user_id)`); the email is **at-least-once** (the SES↔`run_log` dual-write can't be atomic).
- **Step 6 — Notifier (SES daily digest):** HTML + plaintext digest of the day's scored matches; a zero-matches "no matches today" email (VG5). Apply-URL `href` is **scheme-allowlisted to http/https** as an email-injection guard, verified un-bypassable (the fresh verifier's should-fix).
- **Step 5 — Scorer (7-factor ATS):** explainable scoring at temp 0 on `deepseek-v4-pro`; `UNIQUE(score.cluster_id)` enforced (migration 0002); VG8 (threshold-is-config) proven.
- **Step 4b — gold filter + `Profile` contract:** v0 default is a **deterministic** `FilterStrategy` (an LLM gold-filter is redundant with the Scorer at 10–30 jobs/day — built and config-selectable, but off by default); **1:1 clusters** (`cluster_id = posting_id`) close the score-keys-on-`cluster_id` gap until real clustering arrives (M2). New `Profile` contract + sanitized sample.
- **Step 4 — fetch + bronze→silver landing:** the source fetch and the bronze→silver landing path, wired through to the `Dissector` and `Repository`.
- **C-3 — Terraform infra:** Aurora SLv2 (scale-to-0) + RDS Data API, S3, least-privilege IAM, SES, EventBridge — **apply-validated end-to-end, then destroyed to ~$0**.
- **C-2 — schema + `Repository`:** the v0 schema (Alembic migrations) behind the `Repository` port, on SQLAlchemy Core + the `aurora-data-api` dialect; **live-validated on real Postgres** (5/5 round-trip).
- **C-1 — silver `Dissector` (first application code, LIVE):** `OpenAICompatLlmClient` (the `LlmClient` port) + a grounded, evidence-required JD dissection (`DissectedPosting` contract + `grounding_check`) — proven on real probe JDs (the thin JD invented **0** tools; the detailed JD returned 17 grounded skills). `src/jobfetcher/`.
- **[ADR-0019] (ran for real, amended)** — agentic build orchestration: each unit runs the gate trio as a per-unit pipeline, fanned out across *independent* units; **executed across C-2…Step 6**. The pipeline gained an **independent adversarial verifier (fresh context, not pre-framed)** — distinct from the in-build Reviewer, added mid-phase on Tarig's shared-blind-spot insight (an orchestrator-spun reviewer shares the builder's framing). It **validated**: on Step 4 the in-build Reviewer reported 0 must-fixes but the fresh verifier found **3 crash blockers**; on gold/scorer/notifier it confirmed clean (and caught the notifier's apply-URL allowlist should-fix). Three independent eyes = verifier + CodeRabbit + human.
- **[ADR-0018]** — persistence access: SQLAlchemy Core + the `aurora-data-api` dialect behind a `Repository` port; local Postgres for DB tests (LocalStack can't mock the Data API).
- **Storage layer designed** (plan §31): Aurora SLv2 **scale-to-0**; dissected output = **JSONB + scalar columns on `posting`** (the `dim_skill`/`fct_job_skill` bridge stays at M5); `score` reconciled (`skills`/`sector`/`seniority` are silver-derived, not re-extracted).
- **[ADR-0017]** — LLM transport = OpenAI-compatible API; v0 provider = **DeepSeek**; Bedrock parked. The model-agnostic port now swaps *provider*, not just model, via config (`base_url`/`api_key`/`model`) — one `OpenAICompatLlmClient` serves DeepSeek, Ollama, Anthropic-direct, or Bedrock.
- `scripts/deepseek_smoke.py` — key-free smoke test that proves the DeepSeek unblock.
- DeepSeek API key in **Secrets Manager** (`jobfetcher/deepseek`).
- **[ADR-0016]** — LLM dissection at the silver layer on *every* posting → populates the market-wide dimensional model.
- **[ADR-0015]** — type-replaceability as a first-class tenet (P3): every stage a config-selected strategy behind a port (`SourceAdapter`/`Dissector`/`FilterStrategy`/`Embedder`/`Scorer`).
- **[ADR-0014]** — operational store = Aurora Serverless v2 + RDS Data API, Lambda outside any VPC (resolves D-v0-1).
- **[ADR-0013]** — enforcement gate-trio (`/start-step` · `/review-step` · `/close-step`) + branch/PR workflow.
- First code: validated `SearchSpec` + JSearch coverage probe (`scripts/`).
- Full context-survival docs: journal Part 2 (build-phase reasoning) + the verbatim working-document archive.

### Changed

- **LLM transport: Bedrock Converse → OpenAI-compatible API** ([ADR-0012] retitled) — supersedes the interim Kimi-on-Bedrock choice (Kimi was only picked because it was *on* Bedrock).
- **Silver: `lingua` language-detect → LLM dissection on every posting** ([ADR-0016]) — "only gold reaches the LLM" reversed (market-wide analytics need all postings).
- **ERR-001: Open → Mitigated** — worked around via DeepSeek; the Bedrock quota is still 0 but no longer blocks. AWS case `178220019100382` stays open as *optional*.
- **Dev infra: dedicated local Postgres** (`jobfetcher-db`, docker-compose) for storage tests — LocalStack can't mock the RDS Data API ([ADR-0018]).

### Notes — honest tradeoffs (read before changing the LLM)

- **Scorer VG3 (determinism) → best-effort.** `deepseek-v4-pro` is non-deterministic *even at temp 0* (MoE / FP non-determinism), so byte-identical re-scores aren't guaranteed. Temp 0 is still sent (the guaranteed invariant); the v0 score is a **triage signal**, with precise calibration deferred to **M7**.

- DeepSeek is **China-hosted** and its ToS permits **training on API inputs** — accepted for *public JD text*; the `Scorer` (which sends the CV/profile) can flip to local Ollama or Anthropic-direct via config ([ADR-0017]).
- The DeepSeek API needs a **funded balance** — its "free signup tokens" did not apply (returned `402` until a small top-up).
