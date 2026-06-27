# Changelog

All notable changes to JobFetcher are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); the project ships **semantic-versioned releases per migration** ([roadmap](docs/03-roadmap.md), [ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)). Until **v0.1** ships, everything lives under *Unreleased*.

The ***why*** behind every entry is the [session decision journal](docs/01-session-decision-journal.md); formal decisions are [ADRs](docs/adr/); the raw reasoning trail is the [working document](docs/session-log/working-document.md).

## [Unreleased] — v0.1 in progress (Step 6 / notify)

### 🏆 Milestones

- **2026-06-27 — v0 DATA PATH CODE-COMPLETE.** The full v0 pipeline now exists as code — **fetch → bronze → silver (LLM dissect) → gold (filter) → score → notify**. On `main`: Step 0 probe, C-1 silver `Dissector`, C-2 schema + `Repository`, C-3 Terraform infra, Step 4 fetch + bronze→silver, Step 4b gold filter + `Profile`, Step 5 Scorer. Step 6 (SES Notifier) is built and in **PR #8** (not yet merged). Remaining: Step 7 (single-Lambda wiring) · Step 8 (test round-up) · Step 9 (minimal CI) · Step 10 (deploy + first live run → tag `v0.1.0`). **153 unit + ~22 integration tests green; `ruff` clean.**
- **2026-06-26 — v0 DESIGN COMPLETE.** The v0 implementation plan, **19 ADRs**, and the storage-layer design are fully documented; the M1–M8 foundations are laid (directional, a living hypothesis). The first build unit is **live** (C-1 silver `Dissector`). Tagged `milestone/v0-design-complete-2026-06-26`. Next: the **agentic per-unit build** ([ADR-0019](docs/adr/0019-agentic-build-orchestration.md)), starting with C-2 (schema + `Repository`). `v0.1.0` is reserved for when the pipeline ships end-to-end.
- **2026-06-24 — THE LLM IS LIVE.** After weeks blocked by a Bedrock new-account daily-token quota of **0** ([ERR-001](docs/ledgers/errors.md)), the scoring/dissection path is unblocked. We stopped waiting on AWS and **routed around it** — the LLM transport now runs on the **OpenAI-compatible API with DeepSeek** (`deepseek-v4-flash`), verified end-to-end (`scripts/deepseek_smoke.py` → HTTP 200). The whole pipeline (bronze → silver dissection → gold → score) is now **live-runnable**, not blocked work. Full arc + reasoning: [journal §18](docs/01-session-decision-journal.md). ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md))

### Added

- **Step 6 — Notifier (SES daily digest, in PR #8):** HTML + plaintext digest of the day's scored matches; a zero-matches "no matches today" email (VG5). Apply-URL `href` is **scheme-allowlisted to http/https** as an email-injection guard, verified un-bypassable (the fresh verifier's should-fix).
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
