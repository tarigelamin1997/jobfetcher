# CLAUDE.md — JobFetcher

> Orientation + operating rules. This file is navigation and principles only — the detail lives in [`docs/`](docs/). **The repo is the memory: any session resumes from these files alone.**

## What this is
JobFetcher is a personal-scale, serverless job-matching tool **and** a Data-Engineering/Cloud portfolio piece — built as an **evolutionary architecture**: a minimal working core (v0), then a sequence of deliberate, observable migrations, each a clean GitHub release.

- **Dual purpose, equal weight:** a tool Tarig actually uses daily to find/score jobs, *and* a repo that proves production AWS + DE skill to hiring managers. Every component must earn *both*.
- **The candidate / market:** Tarig Elamin — Data Engineer / Data Platform / Data Architect, Riyadh → GCC (on-site/relocation, not remote-global), English-only. Profile is the scoring source of truth.

## Current status
**v0 in progress — Step 0 (ingestion probe).** Design + docs complete; the first code now exists ([`scripts/`](scripts/), [`config/`](config/)): a validated, `SearchSpec`-driven **JSearch coverage probe**, proven end-to-end against the live API (key in Secrets Manager). Chosen LLM = **Kimi K2 Thinking** (`moonshot.kimi-k2-thinking`, model-agnostic via Converse). Open blocker: account-wide Bedrock **daily-token quota = 0** ([ERR-001](docs/ledgers/errors.md)). Build plan: [`docs/04-v0-build-plan.md`](docs/04-v0-build-plan.md); live status: [`docs/ledgers/phase-index.md`](docs/ledgers/phase-index.md).

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
- **Build workflow ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)):** each build unit runs the **gate trio** — `/start-step` (entry) → implement → `/review-step` (code) → `/close-step` (exit) — with **two human checkpoints** (spec approved *before* code; approval *before* merge/tag). v0 *code* builds on a branch → PR → tag; `main` is PR-only (docs may go direct for speed).
- **Documentation is constructed, not described** — written live as decisions happen, not reconstructed later. Every doc carries **What / Why / So-what**. A `[TO BE FILLED]` placeholder is a blocker, not a draft.
- **Decisions → ADRs** ([`docs/adr/`](docs/adr/)) with the rejected alternatives named. Errors → the error log ([`docs/ledgers/errors.md`](docs/ledgers/errors.md)) answering the Five Questions (what/why/how/fix/prevention+detection).
- **Testing:** unit (logic) + integration (LocalStack/moto) + dbt tests (marts) + a live smoke run. Validation gates are **behavioral + carry a negative case** — a presence/liveness check is *no gate*.
- **Correlation IDs** on every pipeline run (cheap observability). Guards/contracts where they earn their keep, not by rote.
- **Diagrams:** Mermaid, in-repo ([`docs/diagrams.md`](docs/diagrams.md)) — renders on GitHub, versioned, never drifts. Eraser is an optional personal/portfolio view (diagram-as-code + visuals), **not committed**.

## The architecture in one breath
Two planes (full detail in [`docs/02-architecture.md`](docs/02-architecture.md)):
- **Operational** (the daily tool): scheduled run → fetch → dedup (cluster-and-surface, never hide) → Bedrock score → CV tailor → notify, on **Postgres + S3**, secrets in **Secrets Manager**, region **us-east-1**.
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

## What NOT to do
- Don't build ahead of the current stage. v0 first; migrations are planned **just-in-time** after the prior release ships.
- Don't add a service/tool/library that can't pass the defensibility rubric. If it's a showcase, label it one.
- Don't commit v0 *code* directly to `main` — branch → PR → merge after the gate trio passes ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)); docs may go direct for speed.
- Don't put real PII (CV/profile) in the repo — sanitized sample only; real data is gitignored and lives in private S3.
- Don't claim scale justifies the stack — it doesn't (10–30 jobs/day). Defend on *patterns at production standard, modest scale, deliberately right-sized.*
- Don't let a doc go stale after a change — update it the moment the change is made.
