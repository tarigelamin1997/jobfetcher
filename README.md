# JobFetcher

**A serverless job-matching pipeline that fetches roles, scores them against your real profile with an LLM, and emails you a daily shortlist — built as an *evolutionary architecture* you can watch grow, one deliberate, documented migration at a time.**

> **Status: `v0.1.0` SHIPPED (2026-06-29).** The minimal working core is **built, deployed to AWS, live-validated end to end, then torn down to ~$0.** A single scheduled Lambda runs **JSearch fetch → bronze (S3 + Postgres) → silver (DeepSeek dissect) → gold filter → 7-factor ATS score → SES daily digest**, on Aurora Serverless v2 + the RDS Data API, with Terraform infra, Secrets Manager, a real test pyramid, and GitHub Actions CI. The live run scored real UAE Data-Engineer postings and **delivered two emails (0 SES bounces)** before `terraform destroy` returned the account to ~$0. Everything past v0 is a *hypothesis* re-derived after each release via the bottleneck protocol — see the [roadmap](docs/03-roadmap.md) · [CHANGELOG](CHANGELOG.md) · live [phase index](docs/ledgers/phase-index.md).

**Dual purpose, equal weight:** a tool Tarig Elamin (Data Engineer, Riyadh → GCC) uses daily to find and score jobs, *and* a portfolio piece that proves production AWS + Data-Engineering skill. Every component must earn both.

---

## The problem

A serious job search drowns you in noise: dozens of postings a day, most a poor fit you can't tell apart from the good ones until you've spent 45 minutes reading and tailoring. JobFetcher turns that into a daily, scored shortlist — *"here are the roles actually worth your time, with the reasons why"* — so the per-job triage cycle drops from 45 minutes to 5.

It does **not** auto-apply (external ATS automation is brittle and risky). It removes the *discovery, filtering, and triage* toil and leaves the human decision where it belongs.

## What makes this repo worth reading

A personal-scale tool built to **production standards**, and deliberately an exercise in **evolutionary architecture**:

- It ships as a **minimal core first** (v0), then grows only by **solving the next real bottleneck** — every added piece of complexity is justified by a capability it unlocks and recorded in an ADR.
- Each migration is a **clean, observable GitHub release** with a before/after diagram. You can read the architecture *evolve*.
- It is **honest about scale**: at ~10–30 jobs/day nothing here is justified by load — so every choice is defended on *fit and judgment*, not buzzwords. Where something exists to demonstrate a skill, it is labeled as such.

---

## Architecture

### As-built v0 (what shipped in `v0.1.0`)

One EventBridge-scheduled Lambda (`jobfetcher.handlers.pipeline.handler`) runs the whole operational medallion in sequence, threading one correlation `run_id` through logs, rows, and S3 objects:

```mermaid
flowchart LR
  EB["EventBridge<br/>daily cron"] --> H["one Lambda<br/>handlers.pipeline.handler"]
  H --> F["fetch<br/>JSearch API"]
  F --> B["bronze<br/>raw JSON → S3 + bronze_posting"]
  B --> S["silver<br/>clean + DeepSeek dissect → posting"]
  S --> G["gold<br/>deterministic FilterStrategy → gold_candidate + 1:1 cluster"]
  G --> SC["score<br/>DeepSeek 7-factor ATS → score rows"]
  SC --> N["notify<br/>SES HTML+plaintext digest"]
  SM["Secrets Manager<br/>jobfetcher/deepseek · jobfetcher/jsearch"] -.-> H
  B -. raw .-> S3[("S3")]
  B <--> PG[("Aurora Serverless v2<br/>+ RDS Data API")]
  S <--> PG
  SC <--> PG
  N -. send-once run_log .-> PG
```

- **Idempotent per run-date:** upserts + a `run_log` send-once guard (PK `(run_date, user_id)`) mean a re-run produces identical rows and **at most one digest per day**; a stage failure returns `500` so the next invocation resumes. SES (external) can't join the DB transaction, so the email is **at-least-once** — send, then mark.
- **Gold is deterministic in v0** — at 10–30 jobs/day an LLM gold-filter is largely redundant with the Scorer (P1 minimalism). An `LlmFilterStrategy` is built and config-selectable behind the same port for scale.
- **Lambda runs outside any VPC** — Aurora is reached over the **RDS Data API** (HTTPS), so there is no VPC/NAT, and Aurora Serverless v2 scales to zero when idle.

### Target shape (reached via migrations, not built at once)

Two cleanly-separated planes — the operational daily tool and the analytical DE-depth layer. **v0 is a deliberate subset of this.** Full design in [`docs/02-architecture.md`](docs/02-architecture.md); all Mermaid diagrams in [`docs/diagrams.md`](docs/diagrams.md).

```mermaid
flowchart TB
  subgraph OP["Operational plane — the daily tool (AWS serverless)"]
    SCHED[EventBridge cron] --> FETCH[fetch: source adapters]
    FETCH --> DEDUP[dedup: cluster &amp; surface]
    DEDUP --> SCORE[score: LLM via DeepSeek, explainable]
    SCORE --> CV[tailor CV: DOCX + PDF, draft→review]
    CV --> NOTIFY[notify: email + Notion]
    FETCH -. raw .-> S3[(S3)]
    DEDUP --- PG[(Postgres)]
    SCORE --- PG
  end
  subgraph AN["Analytical plane — DE depth"]
    PG --> DBT[dbt marts: tests · lineage · incremental]
    S3 --> DBT
    DBT --> INTEL[Skill-Demand + Sector Intelligence]
    INTEL --> NOTION[(Notion)]
  end
  SECRETS[Secrets Manager] -.-> SCORE
  NOTIFY --> NOTION
```

The CV tailor, multi-source clustering dedup, Step Functions, Notion, and the dbt analytical plane are all **later migrations** — the diagram is the *destination*, the [roadmap](docs/03-roadmap.md) is the path.

---

## Tech stack

| Area | Choice |
|---|---|
| **Language** | Python 3.11 · Pydantic 2 |
| **Compute** | AWS Lambda (one handler, outside any VPC) · EventBridge daily cron |
| **Store** | Aurora Serverless v2 (scale-to-0) via the **RDS Data API** · S3 (raw payloads) |
| **DB access** | SQLAlchemy 2 + `sqlalchemy-aurora-data-api` behind a `Repository` port · Alembic migrations |
| **LLM** | OpenAI-compatible API, **provider + model in config** ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md)); v0 = **DeepSeek** (`deepseek-v4-flash` dissect · `deepseek-v4-pro` score). Bedrock parked. |
| **Email** | SES (HTML + plaintext digest) |
| **Secrets** | Secrets Manager (`jobfetcher/deepseek`, `jobfetcher/jsearch`) |
| **IaC** | Terraform 1.14 — **14 resources**, us-east-1, least-privilege IAM (no Bedrock) |
| **AWS SDK** | boto3 |
| **Tests** | pytest — **180 unit + ~26 integration + ~3 live, 89% coverage** |
| **CI** | GitHub Actions — ruff + tests + 85% coverage floor + `terraform validate` + **gitleaks** secret-scan; pre-commit (gitleaks + ruff) |

dbt / Snowflake / Debezium-CDC / Spark are documented *scale-paths* or live in sibling projects — not in this repo today. See the [decision journal](docs/01-session-decision-journal.md).

---

## How to run

### Prerequisites

- An **AWS session** for the `jobfetcher-dev` IAM user (region us-east-1).
- Two **Secrets Manager** secrets: `jobfetcher/deepseek` (DeepSeek API key) and `jobfetcher/jsearch` (JSearch API key).
- **SES** sender + recipient addresses verified (sandbox is fine for personal use).
- Your config: copy the committed samples to the gitignored local files and fill them in —
  - `config/search_config.sample.yml` → `config/search_config.local.yml` (the per-user [`SearchSpec`](src/jobfetcher/core/search_spec.py); every field required, fails loudly on anything missing/invalid).
  - `config/profile.sample.yml` → `config/profile.local.yml` (the scoring source of truth).
  - The samples are sanitized; **real profile/PII is gitignored** and never enters the repo.

### Deploy

```bash
python scripts/build_lambda.py        # package the Lambda artifact
terraform -chdir=infra apply          # ~14 resources (Aurora + Data API, S3, Lambda, EventBridge, SES, IAM)
alembic upgrade head                  # create the schema on Aurora, over the Data API
# invoke the Lambda (EventBridge fires daily; or invoke manually) → statusCode 200
```

`terraform destroy` returns the account to ~$0 when idle (Aurora scales to zero between runs regardless).

### Local dev & tests

The suite is a pyramid; default development needs no Docker. Full gate map in [`tests/README.md`](tests/README.md).

```bash
# Unit (pure logic; LLM/DB/AWS all faked) — needs nothing
python -m pytest -m "not integration" -q

# Coverage
python -m pytest -m "not integration" --cov=src/jobfetcher --cov-report=term -q

# Integration (orchestrators + handler vs real local Postgres + moto S3/SES; LLM faked)
docker compose up -d
JOBFETCHER_DB_URL=postgresql+psycopg2://jobfetcher:jobfetcher@127.0.0.1:5433/jobfetcher \
  python -m pytest -m integration -q
docker compose stop

# Live (real DeepSeek end-to-end) — runs within the integration command when a key resolves;
# skips automatically without $DEEPSEEK_API_KEY (or the jobfetcher/deepseek secret).
```

LocalStack can't mock the Aurora Data API, so integration DB tests use a **real local Postgres** ([ADR-0018](docs/adr/0018-persistence-sqlalchemy-data-api-repository.md)); moto still covers S3 + SES.

---

## Proof

- **Live end-to-end validation (2026-06-29):** `terraform apply` → 14 resources → `alembic upgrade head` over the Data API → invoke → `statusCode 200` → **fetched 10 → bronzed 10 → silvered 8 → gold 8 → scored 8 → notify sent**. **Two emails delivered, 0 SES bounces:** a no-matches digest (threshold 60) and, on an **idempotent re-run** (`already: 8` skipped — VG4 live), a populated shortlist (threshold lowered to 20). Then `terraform destroy` → 14 destroyed, back to ~$0.
- **Validation gates VG1–VG8** are **behavioral and carry a negative case** (a presence/liveness check is no gate): ingestion, scoring, best-effort determinism, idempotency, notification, teardown, secrets hygiene, threshold-is-config. Each maps to named positive + negative tests in [`tests/README.md`](tests/README.md).
- **CI** runs ruff, the test suite with an 85% coverage floor, `terraform validate`, and a gitleaks secret-scan on every push.

---

## Roadmap

`v0.1.0` is the **irreducible working core**. Everything after it is chosen by the **bottleneck-decision protocol**, not a fixed plan: ship → use → surface the top-3 bottlenecks to the next real capability → rank by leverage (capability ÷ complexity) → break the biggest with the minimal migration → repeat. The directional hypothesis (M1 CV tailoring → M2 multi-source dedup → … → M8 v1.0.0) is *direction, not contract* — re-derived after each release. Full protocol + migration table in [`docs/03-roadmap.md`](docs/03-roadmap.md).

```mermaid
flowchart LR
  v0["v0.1.0 ✅ shipped<br/>fetch → score → email<br/>deployed · live-validated · $0"] --> M1["M1 ⬜ CV tailoring"]
  M1 --> M2["M2 ⬜ multi-source + dedup"]
  M2 --> M3["M3 ⬜ Step Functions"]
  M3 --> M4["M4 ⬜ Notion + near-miss"]
  M4 --> M5["M5 ⬜ dbt marts"]
  M5 --> M6["M6 ⬜ skill + sector intel"]
  M6 --> M7["M7 ⬜ observability + calibration"]
  M7 --> M8["M8 ⬜ → v1.0.0"]
```

---

## Design philosophy & docs

This project treats **documentation as infrastructure** — the repo is the memory; any contributor (human or agent) resumes from the files alone. Two principles govern every decision:

- **P1 — Absolute minimalism.** Build the minimal complexity that solves the *present* problem; design cheap seams for the future, don't build the future.
- **P2 — Bottleneck-driven evolution.** After each release, solve the highest-leverage bottleneck with the minimal migration, ship, repeat.
- **Defensibility rubric.** Every component must answer *"why this and not the simpler thing?"* without "to put it on my resume." If it can't, it's cut or labeled an honest showcase.

| Doc | What it holds |
|---|---|
| 🧭 [`CLAUDE.md`](CLAUDE.md) | Operating rules + navigation |
| 🧩 [`docs/00-design-philosophy.md`](docs/00-design-philosophy.md) | P1/P2, the defensibility rubric, the two pillars — the constitution |
| 📓 [`docs/01-session-decision-journal.md`](docs/01-session-decision-journal.md) | *Why* the design is what it is — including the reversals (the Bedrock-quota wall, the silver-dissection evolution) |
| 🏛️ [`docs/02-architecture.md`](docs/02-architecture.md) | The full two-plane design, data model/ERD, dedup, scoring |
| 📊 [`docs/diagrams.md`](docs/diagrams.md) | All Mermaid diagrams — architecture · ingestion · roadmap · dimensional model |
| 🗺️ [`docs/03-roadmap.md`](docs/03-roadmap.md) | Directional roadmap + the migration-decision protocol |
| 🔨 [`docs/04-v0-build-plan.md`](docs/04-v0-build-plan.md) | The v0 build, step by step + the validation gate |
| 🧱 [`docs/adr/`](docs/adr/) | Architecture decision records, with the roads not taken |
| 🗂️ [`docs/ledgers/`](docs/ledgers/) | Live state — phase index · locked decisions · contracts · error log |

---

*Built by Tarig Elamin. Personal-scale tool, production-grade engineering, evolved deliberately.*
