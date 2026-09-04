# CLAUDE.md — JobFetcher

> Orientation + operating rules. This file is navigation and principles only — the detail lives in [`docs/`](docs/). **The repo is the memory: any session resumes from these files alone.**

## What this is
JobFetcher is a personal-scale, serverless job-matching tool **and** a Data-Engineering/Cloud portfolio piece — built as an **evolutionary architecture**: a minimal working core (v0), then a sequence of deliberate, observable migrations, each a clean GitHub release.

- **Dual purpose, equal weight:** a tool Tarig actually uses daily to find/score jobs, *and* a repo that proves production AWS + DE skill to hiring managers. Every component must earn *both*.
- **The candidate / market:** Tarig Elamin — Data Engineer / Data Platform / Data Architect, Riyadh → GCC (on-site/relocation, not remote-global), English-only. Profile is the scoring source of truth.

## Current status

**Live: <!--gen:current_release-->`v0.12.1` (2026-09-02)<!--/gen-->.** One EventBridge cron → one Lambda running the medallion (JSearch fetch → bronze → DeepSeek dissect → gold filter → 7-factor score → SES card digest + a presigned full-list report), on Aurora Serverless v2 via the RDS Data API + S3, Terraform state in S3, three CloudWatch alarms → SNS. **Unattended since v0.9.0** — the daily 06:00 UTC run needs nobody. A second Lambda serves the public capture endpoint (below). Local surfaces: `push_config.py`, `export.py`, `track.py`, `preview_digest.py`, `panel.py` (Streamlit).

> **This file does not restate what shipped, per release — one canonical home per fact ([05-methodology](docs/05-methodology.md#one-canonical-home-per-fact-err-016)).** Release narrative → [`CHANGELOG.md`](CHANGELOG.md) · live unit state → [`phase-index`](docs/ledgers/phase-index.md) · contracts → [`interface-contracts`](docs/ledgers/interface-contracts.md) · direction → [`03-roadmap`](docs/03-roadmap.md) · the reasoning and the reversals → [`journal`](docs/01-session-decision-journal.md) + [ADRs](docs/adr/). Duplicating them here is how [ERR-016](docs/ledgers/errors.md) happened.

### Standing facts — know these before touching anything

- **⚠️ The stack has a PUBLIC endpoint.** Since 2026-07-20 the digest's "✓ Mark applied" links hit **`handlers/capture.py`**, a second Lambda (same zip) behind a **Lambda Function URL with `authorization_type = "NONE"`** — the project's **only internet-facing write surface**. **The auth is an HMAC token, not the network**: a short-lived signature scoped to `{posting_id, status}` (30-day TTL, key generated + owned by Terraform in Secrets Manager), verified in constant time **before any DB touch**, so a forged or expired token writes zero rows. Accepted trade-off: a one-click GET can be pre-fetched by an email scanner ([ADR-0035](docs/adr/0035-outcome-capture-endpoint.md) · [INV-001](docs/investigations/INV-001-dark-feedback-loop/README.md)). **Treat any change near it as security-relevant.**
- **⚠️ The "runs unattended" claim failed silently for 38 days** (2026-07-25 → 09-01): every run returned `statusCode: 500`, no digest went out, and the alarms fired on all 38 days **without being acted on**. Fixed and live-validated 2026-09-02 ([ERR-010 → ERR-014](docs/ledgers/errors.md)). **Read [journal §36](docs/01-session-decision-journal.md) before touching the read path or the LLM budgets** — it records the method, the two measurement errors made *during* the fix, and why `pg_column_size()` is the wrong ruler for a Data API payload. Detection was never the gap; escalation still is ([B-5](docs/ledgers/backlog.md)).
- **⚠️ The JSearch sweep runs every 3rd day, not daily — and that is deliberate.** The Lambda still fires daily at 06:00 UTC (the dead-man alarm watches a 24-hour window and cannot be widened), but the *source sweep* costs quota: the free tier is **200 requests/month** and one sweep is `3 titles × 5 countries × 1 page = 15`. Daily sweeping needed ~450/month — 2.7× over — so the tool silently ingested nothing for ~19 days of every month until 2026-09-04 ([ERR-017](docs/ledgers/errors.md)). A non-fetch day still scores, digests and reports; the run summary says `fetch_stopped: not_a_fetch_day`. **Raise `FETCH_EVERY_N_DAYS` and the RapidAPI plan together — exceeding the quota fails silently.**
- **All five bulk reads are keyset-paginated.** The Data API's **1 MB result cap** is a hard ceiling on any unbounded `select()`. A new bulk read that does not paginate is the ERR-010 bug again.
- **DeepSeek's concurrency limit scales with account *balance*** (39 near-empty, ≥200 at $10) and is reported **only in a 429 body** — no `/limits` endpoint, no `x-ratelimit-*` headers ([ERR-014](docs/ledgers/errors.md)).
- **DeepSeek, not AWS, is the entire running cost** — ~$0.0155/posting, roughly $5–14/month; Aurora scale-to-0 means ~$0 idle ([B-10](docs/ledgers/backlog.md)).
- **Docs are CI-checked.** `python scripts/check_docs.py` fails the build on a broken internal link, a `plan §NN` citation, a **guarded count** that drifts from its ground-truth command, or a stale deployment claim. Counts in prose are wrapped in `<!--fact:name-->` markers — never retype one by hand ([ERR-016](docs/ledgers/errors.md)).
- **Tests:** <!--fact:tests_unit-->494<!--/fact--> unit + <!--fact:tests_integration-->67<!--/fact--> integration (<!--fact:tests-->561<!--/fact--> collected; integration needs Docker or `$JOBFETCHER_DB_URL`), ~95% coverage in CI, `ruff` clean.

**The roadmap is a living hypothesis** — the next migration is chosen by the P2 protocol from real use, not from the pre-drawn list. Still-future hypotheses live in [`03-roadmap`](docs/03-roadmap.md); observed bottlenecks feeding P2 live in [`backlog`](docs/ledgers/backlog.md).

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
- **Build workflow ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)):** each build unit runs the **gate trio** — `/start-step` (entry) → implement → `/review-step` (code) → `/close-step` (exit) — with **two human checkpoints** (spec approved *before* code; approval *before* merge/tag). v0 *code* builds on a branch → PR → tag. **`main` is PR-only — for EVERYTHING, docs included.** That is not a convention any more: branch protection enforces it with `enforce_admins: true`, so a direct push is rejected outright ([ERR-015](docs/ledgers/errors.md)). The gate trio runs as an **agentic per-unit pipeline** — builder → review/simplify → **independent adversarial verifier (fresh context)** → scribe/close → security/verify — fanned out across *independent* units; **CodeRabbit + the human are additional independent eyes per PR** ([ADR-0019](docs/adr/0019-agentic-build-orchestration.md)). **The canonical, invocable squad procedure** — the current roles (Investigator → Surgeon → single Examiner), the **severity-gated auto-merge** policy (crucial → human, non-crucial → auto-pilot), and how to run it — lives in [`.agents/agentic-workflow.md`](.agents/agentic-workflow.md); say *"run the agentic workflow for X"* to invoke it.
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
| [`docs/ledgers/`](docs/ledgers/) | Live state: phase index · locked decisions · interface contracts · procedure registry · error log · backlog (observed bottlenecks → P2 input). |
| [`docs/investigations/`](docs/investigations/) | Durable **Investigator dossiers** — one per bottleneck: *does it exist? (evidence, or kill) · root cause · minimal-fix plan · validation gate · typed-connection graph seam*. Produced read-only via `/investigate`; the layer between a backlog signal and an ADR decision ([ADR-0034](docs/adr/0034-investigation-dossier-system.md)). |
| [`docs/session-log/`](docs/session-log/) | The **verbatim working document** (§1–29, last written 2026-06-25) — raw, unedited design + build reasoning notes preserved in full. **This is the document the `plan §NN` citations in the ADRs and ledgers point at**, for §12–§29; §30+ were never committed. The granular source the curated docs distill; read it for the *why behind the why*. |

## What NOT to do
- Don't build ahead of the current stage. v0 first; migrations are planned **just-in-time** after the prior release ships.
- Don't add a service/tool/library that can't pass the defensibility rubric. If it's a showcase, label it one.
- **Never push to `main` directly — not code, not docs, not a release commit.** Branch → PR → merge after the gate trio passes ([ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)). There is no docs exemption: the previous wording said *"docs may go direct for speed"*, which contradicted the repo's own branch protection and was duly used to rationalise a bypass ([ERR-015](docs/ledgers/errors.md)). Protection now has `enforce_admins: true`, so the push is refused rather than argued with.
- Don't put real PII (CV/profile) in the repo — sanitized sample only; real data is gitignored and lives in private S3.
- Don't claim scale justifies the stack — it doesn't (10–30 jobs/day). Defend on *patterns at production standard, modest scale, deliberately right-sized.*
- Don't let a doc go stale after a change — update it the moment the change is made.
