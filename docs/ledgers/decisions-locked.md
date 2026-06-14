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
| Data sources: JSearch + Adzuna; official APIs only, no scraping | Multi-source via a pluggable adapter; ToS-safe | journal §7 |
| Scoring: keep 7-factor ATS framework (tune weights) | Encodes the trusted ATS framework | journal §7 |
| Explainability critical (strengths/gaps/strategic assessment) | The reasoning is the value, not just a number | journal §7 |
| Thresholds 75 / 55 / 10 (config-editable) | Active-but-selective bar | journal §7 |
| Lightweight scoring calibration loop + accuracy SLO | Reliability ROI; corrections tune the prompt | journal §7 |
| Scam-gate + poster-type label (no hard company filter) | Surface context; user decides | journal §7 |
| UI: email + Notion both first-class; status tracked in Notion | Email triage + Notion act/track | journal §7 |
| Near-miss watch→re-score→graduate loop (full) | Distinctive feature | journal §7 |
| Observability right-sized (few alarms + documented SLOs) | Full suite is over-built for one user | [ADR-0002], §9 |

## Security, cost, infra
| Decision | Why | Owner |
|---|---|---|
| Secrets Manager, IAM-scoped per function | Zero secrets in code; security signal | journal §7 |
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
