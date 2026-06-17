# Ledger · Locked Decisions

> Every locked decision, a one-line why, and where its full reasoning lives. No orphaned decisions. The narrative is in [01-session-decision-journal](../01-session-decision-journal.md); formal records (with rejected alternatives) in [adr/](../adr/). Migration-implementation decisions get their ADR when that migration is planned.

## Goals & process
| Decision | Why (one line) | Owner |
|---|---|---|
| Dual purpose, equal weight (tool = portfolio) | Every component must earn daily-use AND portfolio value | journal §1 |
| Evolutionary architecture (minimal v0 → migrations) | Fast value + minimalism + the evolution *is* the portfolio | [ADR-0001] |
| Tool-minimalism wins; DE-depth is the tiebreaker | Only build what a real tool bottleneck justifies | [ADR-0002] |
| Roadmap is directional, not fixed | Implementation reveals the next bottleneck | [ADR-0001], roadmap |
| Self-hosted / open-source (not SaaS) | Goal is a tool + portfolio, not a company | [ADR-0007] |
| Docs in-repo are canonical (Notion = operational DBs only) | Versioned with code; context survival | journal §6 |
| Diagrams = Mermaid in-repo (canonical); Eraser = optional personal/portfolio view, not committed | Renders on GitHub, versioned, never drifts, no binary bloat | journal §15 |
| AWS: `samareltayeb` profile dedicated to JobFetcher; region **us-east-1**; Bedrock via **`us.anthropic.*` inference-profile ids** (base ids fail) | Confirmed in-account; Claude 4.x are inference-profile-only | [ADR-0008] · [ERR-001] |
| **Default AWS identity = non-root IAM user `jobfetcher-dev`** (acct 198592435375), region **us-east-1** — all local dev/tooling via the **`jobfetcher` profile** (`AWS_PROFILE=jobfetcher` + `[default]` mirrors the same key, so every resolution path lands on it). Keyless **root** session (`samareltayeb`) retained for **rare root-only ops** only | One identity for everything; non-root by default; **root access keys never created** | journal §18 |
| Bedrock prerequisite: account **daily token quota > 0** | New account gated at 0 (**non-adjustable**); lifts via account maturity / AWS Support — billing is valid, credits unused | [ERR-001] |
| **LLM is model-agnostic** — config-selected Bedrock model via **Converse** (model id per task); current candidate Kimi K2 Thinking, Claude when its quota lifts | Switching models is config, not code; routes around vendor quota/availability blocks | [ADR-0012] · [ERR-001] |
| Non-root IAM **admin user `jobfetcher-dev`** (acct 198592435375) created for human CLI + AWS-Toolkit auth (static key, `[jobfetcher]` profile) — brought forward from M8 because the Toolkit's session auth kept expiring; **root keys still never created**; runtime Lambda roles stay least-privilege via Terraform; full least-privilege of the human identity deferred | AWS "stop using root" best practice; reliable extension auth was the bottleneck | journal §16, §18 |
| Decision rights: Tarig approves arch/major; Claude drives rest | Co-design then build; confirm major only | journal §1, §6 |
| Multi-user · feedback hub · BI dashboard = design-for, build-later | Seam-ready, not built in v1 | journal §6, roadmap |

## Candidate, market & scope
| Decision | Why | Owner |
|---|---|---|
| Target roles: DE / Data Platform / Data Architect only | Focused; no spread to adjacent tracks | journal §4 |
| Locations: Riyadh → GCC → relocate; not remote-global | On-site oriented | journal §4 |
| English only | Simplifies sources/scoring/CV | journal §4 |
| Profile used as-is (source of truth) | Accurate; no re-intake | journal §4 |
| Sensitivities: site offline; "Cansa Group"; honesty rules | Carried forward | journal §4 |

## Architecture
| Decision | Why | Owner |
|---|---|---|
| Two-plane (operational vs analytical) | DE-depth without diluting serverless | [02-architecture] |
| PostgreSQL operational store (over DynamoDB) | Relational data → relational store; pgvector | [ADR-0003] |
| Analytics: dbt-on-Postgres default; Snowflake conditional | Tiny data; build warehouse only if a bottleneck demands | [ADR-0004] |
| Databricks rejected (Spark→OrderFlow) | Spark-on-tiny-data is weak signal | [ADR-0004] |
| Dedup: cluster-and-surface, never hide; measured P/R | Wrong-merge (hiding a job) is the only unacceptable error | [ADR-0005] |
| Suspected-Duplicates = dedicated (5th) Notion DB | User confirms/splits ambiguous clusters | [ADR-0005] |
| CV: DOCX + pure-Python/HTML PDF, no LibreOffice | LibreOffice-in-Lambda was the #1 reliability risk | [ADR-0006] |
| One master CV; strict honesty + human-review gate | Fits DE focus; review gate also captures calibration data | [ADR-0006] |
| Region us-east-1 | Widest Bedrock availability; residency not required | [ADR-0008] |
| Batch EL now; Debezium CDC = documented scale-path | Real-time CDC not justified at 10–30/day | [ADR-0009] |
| Step Functions = a migration (M3), not v0 | Earned by real Lambda complexity, not assumed | roadmap |

## Pipeline behavior
| Decision | Why | Owner |
|---|---|---|
| Source = **JSearch** (probe free 200-req → Pro $25/mo); single-source for v0; Adzuna deferred; official APIs only, no scraping | Coverage + full JD text from one API (Google-for-Jobs, supports GCC); single source ⇒ no cross-source dedup in v0; pay only on evidence | [ADR-0010] |
| **Ingestion = medallion landing** — bronze (land-all-raw, immutable) → silver (clean+dedup) → gold (profile-filter) → score | Land-daily guarantee + cheap filter before the LLM | journal §13 · [02-architecture] |
| **Immutable bronze ⇒ replay** — silver/gold/score are pure functions over bronze | Reprocess history with zero new API calls when filters/profile change | journal §13 |
| **Quota/request-budget** — charged per request; query (keywords + `country` + date) = source-side pre-filter; page-cap + date-window are config | API quota is the real cap, not storage | [ADR-0010] |
| **Silver text pipeline** — pure, versioned, ordered steps (clean → lang-detect → segment → fingerprint → embed) on job_description/title; rest is field-mapping | Most transforms are on text; pure+versioned ⇒ replayable | journal §14 |
| **Origin-level lineage** — each silver row carries `bronze_id` + `pipeline_version`; field→source is a documented constant | Trace-to-origin + exact re-derive (replay-based) | journal §14 |
| **Never-discard → dimensional modeling** — retain all (bronze lossless); model dims by *insight* not by *field*; grow a dim when a question needs it (retroactively via replay) | Compounding insight without table-per-field sprawl | [ADR-0011] · 00-philosophy |
| **Analytical = constellation model** — facts (fct_job_posting · `fct_job_skill` bridge · fct_job_score · fct_application) over conformed dims (date/skill/title/company/sector/location) + point-in-time profile (SCD2); skills+title derived from text | Insights emerge from joins; the skill bridge is the linchpin | [ADR-0011] |
| **Analytics priority order** — `dim_skill`+`fct_job_skill` first → point-in-time profile + score facts (trends) → `dim_sector`; title/company supporting; built at M5/M6 | Tarig's priority insights: skill-demand/gaps · progress trends · sector intel | [ADR-0011] |
| Scoring: keep 7-factor ATS framework (tune weights) | Encodes the trusted ATS framework | journal §7 |
| Explainability critical (strengths/gaps/strategic assessment) | The reasoning is the value, not just a number | journal §7 |
| Single threshold (default 60), runtime-editable per user, gates shortlist + CV; floor 50, near-miss 10 | One user-tunable knob; change without redeploy; active value stamped per run for measurement | journal §7 + plan §12 |
| Lightweight scoring calibration loop + accuracy SLO | Reliability ROI; corrections tune the prompt | journal §7 |
| Scam-gate + poster-type label (no hard company filter) | Surface context; user decides | journal §7 |
| UI: email + Notion both first-class; status tracked in Notion | Email triage + Notion act/track | journal §7 |
| Near-miss watch→re-score→graduate loop (full) | Distinctive feature | journal §7 |
| Observability right-sized (few alarms + documented SLOs) | Full suite is over-built for one user | [ADR-0002], §9 |

## Security, cost, infra
| Decision | Why | Owner |
|---|---|---|
| Secrets Manager, IAM-scoped per function | Zero secrets in code; security signal | journal §7 |
| **AWS auth: deployed pipeline = no static keys** (Lambdas use **IAM execution roles**, AWS injects creds at runtime); **local** dev = session login (keyless) **or** a **non-root IAM user key** (`jobfetcher` profile) — **never root keys** | Temporary runtime creds > long-lived keys; the local method is the operator's choice, root keys are the one hard no | journal §18 |
| Public repo PII-scrubbed; real profile gitignored → private S3 | Privacy + clone-and-runnable sample | [ADR-0007] |
| Cost ceiling ~$50/mo OK; some credits; `terraform destroy` → $0 | Optimize for signal, stay cost-aware | journal §6 |
| IaC: Terraform | Tarig's showcase + most-recognized | journal §6 |
| Testing: unit + LocalStack/moto + dbt tests + live smoke | Reliability + clone-and-run confidence | journal §6 |
| Enforcement machinery (commands/gates) = emergent | Decide during implementation per P1/P2 | [05-methodology] |

## v0 boundary & versioning
| Decision | Why | Owner |
|---|---|---|
| v0 = single Lambda · one source · score · email | Irreducible working core; leaner v0 = more migrations | [04-v0-build-plan] |
| CV = M1 (first migration) | High value, clean first release | roadmap |
| Semver: v0.x per migration → v1.0.0 at M8 → v1.x/v2.0 | Clean evolution story | roadmap |

*Reference labels like [ADR-000X] resolve under [../adr/](../adr/).*
