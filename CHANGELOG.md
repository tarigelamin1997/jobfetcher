# Changelog

All notable changes to JobFetcher are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); the project ships **semantic-versioned releases per migration** ([roadmap](docs/03-roadmap.md), [ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)). **v0.1.0 shipped 2026-06-29.**

The ***why*** behind every entry is the [session decision journal](docs/01-session-decision-journal.md); formal decisions are [ADRs](docs/adr/); the raw reasoning trail is the [working document](docs/session-log/working-document.md).

## [Unreleased]

*Next: run the **bottleneck (P2) protocol** on the live v0 — surface the top-3 bottlenecks to the next real capability, rank by leverage, pick **M1**.*

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
