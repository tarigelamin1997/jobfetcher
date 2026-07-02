# CLAUDE.md — JobFetcher

> Orientation + operating rules. This file is navigation and principles only — the detail lives in [`docs/`](docs/). **The repo is the memory: any session resumes from these files alone.**

## What this is
JobFetcher is a personal-scale, serverless job-matching tool **and** a Data-Engineering/Cloud portfolio piece — built as an **evolutionary architecture**: a minimal working core (v0), then a sequence of deliberate, observable migrations, each a clean GitHub release.

- **Dual purpose, equal weight:** a tool Tarig actually uses daily to find/score jobs, *and* a repo that proves production AWS + DE skill to hiring managers. Every component must earn *both*.
- **The candidate / market:** Tarig Elamin — Data Engineer / Data Platform / Data Architect, Riyadh → GCC (on-site/relocation, not remote-global), English-only. Profile is the scoring source of truth.

## Current status
**v0.2.0 SHIPPED (2026-07-02) — M1 "pipeline hardening", the P2 bottleneck protocol's actual first migration.** Running the tool live on the full 18-query GCC sweep measured three real bottlenecks + a zombie-retry hazard (overruling the pre-drawn *M1 = CV tailoring* hypothesis): serial throughput timed out at silver ~17; one DeepSeek `503` killed a whole run; the gold filter paid the pro-model to reject obvious junk; AWS blind-retried the dead run. All fixed as three gate-trio units — **H-1** LLM retry+jitter & symmetric failure isolation (a provider blip skips one posting, never the run); **H-2** in-Lambda `ThreadPoolExecutor` concurrency (DB writes stay main-thread) + a deadline guard (runs return `partial`, never time out) + `maximum_retry_attempts=0` in Terraform + memory 512→1024; **H-3** subset-title gold filter ("Data Architect" needs `data`+`architect`) + config-selectable `$GOLD_FILTER_STRATEGY`. **Re-validated live on the exact ~132-posting backlog the pre-fix code died on:** `statusCode 200`, backlog fully dissected + scored, **0 run-fatal errors** (15 dissect + 0 score failures isolated), **21-job populated digest sent** (real GCC DE roles scored 60–95). Measured **~13× throughput** (~1.1→~14–15 dissections/min), no timeouts, junk eliminated ([ADR-0021](docs/adr/0021-m1-pipeline-hardening.md); ERR-006/007). Tests: **212 unit + integration green**, `ruff` clean. **Next candidate = the digest email UX** (poor format · apply-links must be visible). *(v0.1.0 baseline below.)*

**v0.1.0 SHIPPED (2026-06-29) — the minimal working core is deployed to AWS, validated live end-to-end, then torn down to ~$0.** The full v0 pipeline — **EventBridge → one Lambda: fetch → bronze → silver (LLM dissect) → gold (filter) → score → notify (SES digest)** — is built (Steps 0–10) and ran for real on AWS: `terraform apply` → **14-resource** stack (Aurora SLv2 + Data API, S3, Lambda, EventBridge, SES, least-priv IAM), schema migrated via **`alembic upgrade head` over the Data API**, invoke → `statusCode 200` → **fetched 10 → bronzed 10 → silvered 8 → gold 8 → scored 8 → notify sent** on real UAE Data-Engineer postings. **Two emails delivered (SES 0 bounces):** a no-matches digest (VG5 zero-path) and, on an **idempotent re-run** (`already: 8` skipped — VG4 live), a 7-job shortlist (VG5 matches-path). The live run caught **2 Data-API deploy-only bugs** invisible to local psycopg2 tests + CI's postgres service (`migrations/env.py` `%`→`%%` ARN-escape · `handlers/pipeline.py` `cluster_arn`→`aurora_cluster_arn` — would break **every** deploy) + a Lambda timeout 300→900s (≈30s/posting); all fixed (PR #13). Then **`terraform destroy` → 14 destroyed**, verified GONE, back to ~$0 (Secrets Manager keys preserved). **Scale finding (a real P2 bottleneck → reinforces M3):** the single Lambda fits the daily incremental run (~10–30 jobs) but **can't** do the full 18-query × 30-day backfill inside the 15-min max. **LLM = OpenAI-compatible API, provider + model in config** ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md)); v0 = **DeepSeek** (`deepseek-v4-flash` dissect/filter · `deepseek-v4-pro` score), live since 2026-06-24. Tests: **180 unit + ~26 integration + ~3 live green**, 89% coverage, `ruff` clean. **Next = the P2 bottleneck protocol** — use the live tool, surface the top-3 bottlenecks to the next real capability, rank by leverage, pick **M1**. Build plan: [`docs/04-v0-build-plan.md`](docs/04-v0-build-plan.md); live status: [`docs/ledgers/phase-index.md`](docs/ledgers/phase-index.md).

## Governing principles (read [`docs/00-design-philosophy.md`](docs/00-design-philosophy.md) for the full version)
- **P1 — Absolute minimalism.** Build the minimal complexity that solves the *present* problem. Complexity is entropic — it accrues uninvited; the default stance is to *resist* it. Design cheap seams for the future; don't build the future.
- **P2 — Bottleneck-driven evolution.** After each release: identify the top-3 bottlenecks blocking the next *real* capability, rank by leverage (capability ÷ complexity), solve the biggest with the minimal migration, ship, repeat. The roadmap is a **living hypothesis**, not a contract.
- **Tool-minimalism wins.** Only build what a real *tool* bottleneck justifies. DE-depth is the *tiebreaker* when a build is justified — never a license to add. The portfolio takes what the tool honestly produces.
- **Defensibility rubric.** Every component must answer *"why this and not the simpler thing?"* without "to put it on my resume." If it can't, cut it or label it an honest showcase. (4 lenses in the philosophy doc.)
- **Two pillars (from the methodology):** (1) *documentation as infrastructure* — the repo is the memory; (2) *a standard not wired into a command is a suggestion* — adopted as discipline now, machinery added only when justified.

## How Claude works here
- **Decision rights:** Tarig approves architecture + major/irreversible decisions; Claude drives the rest and documents it. **Confirm major decisions only** — don't stop every step, don't barrel through irreversible ones.
- **Safety-first (Castle Principle):** build don't demolish · smallest change that works · one change at a time · verify before *and* after · **document before you delete** · **destructive ops (rm, DROP, terraform destroy, force-push) require explicit approval.**
- **AWS dev identity:** all local development uses the non-root **`jobfetcher-dev`** IAM user (CLI profile `jobfetcher`, also the `[default]`), region **us-east-1**; the keyless **root** session (`samareltayeb`) is for *rare root-only ops only*; **CI/CD and Lambda runtime get their own least-privilege IAM roles — never the personal key.** Full model in [`docs/ledgers/decisions-locked.md`](docs/ledgers/decisions-locked.md).
- **Build workflow ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)):** each build unit runs the **gate trio** — `/start-step` (entry) → implement → `/review-step` (code) → `/close-step` (exit) — with **two human checkpoints** (spec approved *before* code; approval *before* merge/tag). v0 *code* builds on a branch → PR → tag; `main` is PR-only (docs may go direct for speed). The gate trio runs as an **agentic per-unit pipeline** — builder → review/simplify → **independent adversarial verifier (fresh context)** → scribe/close → security/verify — fanned out across *independent* units; **CodeRabbit + the human are additional independent eyes per PR** ([ADR-0019](docs/adr/0019-agentic-build-orchestration.md)).
- **Documentation is constructed, not described** — written live as decisions happen, not reconstructed later. Every doc carries **What / Why / So-what**. A `[TO BE FILLED]` placeholder is a blocker, not a draft.
- **Decisions → ADRs** ([`docs/adr/`](docs/adr/)) with the rejected alternatives named. Errors → the error log ([`docs/ledgers/errors.md`](docs/ledgers/errors.md)) answering the Five Questions (what/why/how/fix/prevention+detection).
- **Testing:** unit (logic) + integration (LocalStack/moto) + dbt tests (marts) + a live smoke run. Validation gates are **behavioral + carry a negative case** — a presence/liveness check is *no gate*.
- **Correlation IDs** on every pipeline run (cheap observability). Guards/contracts where they earn their keep, not by rote.
- **Diagrams:** Mermaid, in-repo ([`docs/diagrams.md`](docs/diagrams.md)) — renders on GitHub, versioned, never drifts. Eraser is an optional personal/portfolio view (diagram-as-code + visuals), **not committed**.

## The architecture in one breath
Two planes (full detail in [`docs/02-architecture.md`](docs/02-architecture.md)):
- **Operational** (the daily tool): scheduled run → fetch → dedup (cluster-and-surface, never hide) → LLM score (DeepSeek) → CV tailor → notify, on **Postgres + S3**, secrets in **Secrets Manager**, region **us-east-1**.
- **Analytical** (DE-depth): **dbt marts on Postgres** by default (tests/lineage/incremental). A dedicated warehouse (**Snowflake**) is *conditional* — added only if a real analytics bottleneck demands it. Built CDC/Debezium + Spark showcases live in the OrderFlow project, not here.

**v0 is far smaller than that** — one Lambda, one source, score, email. Everything else is a migration. See the roadmap.

## Map of the docs
| Doc | What it holds |
|---|---|
| [`docs/00-design-philosophy.md`](docs/00-design-philosophy.md) | P1/P2, defensibility rubric, the two pillars, safety-first — the operating constitution. |
| [`docs/01-session-decision-journal.md`](docs/01-session-decision-journal.md) | The full reasoning trail — Part 1 (design session) + Part 2 (build phase: AWS identity · the Bedrock-quota wall · ingestion · gate-trio · Aurora · the silver-dissection evolution). *Why* every choice was made, including the reversals. Context-survival core. |
| [`docs/02-architecture.md`](docs/02-architecture.md) | Two-plane design, data model/ERD, dedup, scoring, CV, diagrams. |
| [`docs/03-roadmap.md`](docs/03-roadmap.md) | Directional roadmap + the migration-decision (bottleneck) protocol + end-state vision. |
| [`docs/04-v0-build-plan.md`](docs/04-v0-build-plan.md) | Exhaustive, step-by-step v0 build plan + validation gate. The only fully-planned stage. |
| [`docs/05-methodology.md`](docs/05-methodology.md) | How we adopt/right-size/cut the Master Project Implementation Plan. |
| [`docs/diagrams.md`](docs/diagrams.md) | Mermaid visual index — full-stack architecture, roadmap, dimensional model. |
| [`docs/adr/`](docs/adr/) | One ADR per foundational decision (rejected alternatives named). |
| [`docs/ledgers/`](docs/ledgers/) | Live state: phase index · locked decisions · interface contracts · procedure registry · error log. |
| [`docs/session-log/`](docs/session-log/) | The **verbatim working document** (§1–27) — raw, unedited design + build reasoning notes preserved in full. The granular source the curated docs distill; read it for the *why behind the why*. |

## What NOT to do
- Don't build ahead of the current stage. v0 first; migrations are planned **just-in-time** after the prior release ships.
- Don't add a service/tool/library that can't pass the defensibility rubric. If it's a showcase, label it one.
- Don't commit v0 *code* directly to `main` — branch → PR → merge after the gate trio passes ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)); docs may go direct for speed.
- Don't put real PII (CV/profile) in the repo — sanitized sample only; real data is gitignored and lives in private S3.
- Don't claim scale justifies the stack — it doesn't (10–30 jobs/day). Defend on *patterns at production standard, modest scale, deliberately right-sized.*
- Don't let a doc go stale after a change — update it the moment the change is made.
