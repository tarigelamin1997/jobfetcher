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
| Region **us-east-1**; Bedrock is a **parked** LLM backend (if ever used, Claude 4.x need `us.anthropic.*` inference-profile ids — base ids fail) | us-east-1 = broadest model availability; v0 LLM routes around the Bedrock quota via the OpenAI-compatible API | [ADR-0008] · [ADR-0017] |
| **Default AWS identity = non-root IAM user `jobfetcher-dev`** (acct 198592435375), region **us-east-1** — all local dev/tooling via the **`jobfetcher` profile** (`AWS_PROFILE=jobfetcher` + `[default]` mirrors the same key, so every resolution path lands on it). Keyless **root** session (`samareltayeb`) retained for **rare root-only ops** only | One identity for everything; non-root by default; **root access keys never created** | journal §18 |
| **v0 LLM prerequisite = a DeepSeek API key** in Secrets Manager (`jobfetcher/deepseek`) — OpenAI-compatible API, **no new-account quota gate** | Routes around the Bedrock quota (ERR-001 mitigated); register at platform.deepseek.com (5M free tokens) | [ADR-0017] |
| **LLM = model-agnostic over the OpenAI-compatible API** — model **+ provider** in config (`base_url`/`api_key`/`model` per task); **v0 backend = DeepSeek** (`deepseek-v4-flash` cheap / `deepseek-v4-pro` strong); Bedrock / Anthropic-direct / Ollama are config swaps | Switching model *or provider* is config, not code; routes around the Bedrock quota | [ADR-0012] · [ADR-0017] |
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
| **Operational DB = Aurora Serverless v2 + RDS Data API** (Lambda outside any VPC) — resolves D-v0-1 | HTTPS DB access ⇒ no VPC/NAT/endpoints; a VPC-bound Lambda would need a ~$32/mo NAT for the public JSearch fetch | [ADR-0014] |
| **Type-replaceability** — every stage = a config-selected strategy behind a port (`SourceAdapter`/`Dissector`/`FilterStrategy`/`Embedder`/`Scorer`/`Repository`); swappable **by type** | Upgrades are config + an adapter, not a rewrite; enables P2 evolution | [ADR-0015] · 00-philosophy P3 |
| **Aurora SLv2 `min_capacity = 0`** (scale-to-0 → ~$0 idle between daily runs); pick an engine version supporting Data API + `pgvector` + scale-to-0 (build check) | Daily batch idles ~23h; re-affirmed over external Postgres (nothing forces us off AWS, unlike Bedrock→DeepSeek) | [ADR-0014] |
| **Persistence access = SQLAlchemy Core + `sqlalchemy-aurora-data-api` dialect, behind a `Repository` port**; DB tests on a **real local Postgres** (LocalStack can't mock the Data API) | Same code local↔Aurora by connection URL; storage is a swappable port; high-fidelity local tests | [ADR-0018] · [ADR-0015] |
| **Dissected output = JSONB + scalar columns on `posting`** (`skills jsonb` + sector/normalized_title/seniority/language/city/state/country/…); `score` **drops** `skills_extracted`/`sector`/`seniority` (now silver-derived); `dim_skill`/`fct_job_skill` bridge = M5 (no early bridge) | Lossless + minimal; the dimensional model grows retroactively per question (P1) | [ADR-0016] · [ADR-0011] · [ADR-0018] |
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
| **Search targeting = validated, fully-explicit per-user `SearchSpec`** (no assumed inputs): `country`=query-param · `job_title`=query-text · `city`/`state`=gold-filters; `language=en` forces English metadata; intake = config (v0) → form (multi-user) | Different users, different targets; nothing taken for granted; gold-filter targets come from the user, not hardcode | [02-architecture] · plan §21 |
| **Ingestion = medallion landing** — bronze (land-all-raw, immutable) → silver (clean+dedup) → gold (profile-filter) → score | Land-daily guarantee + cheap filter before the LLM | journal §13 · [02-architecture] |
| **Immutable bronze ⇒ replay** — silver/gold/score are pure functions over bronze | Reprocess history with zero new API calls when filters/profile change | journal §13 |
| **Quota/request-budget** — charged per request; query (keywords + `country` + date) = source-side pre-filter; page-cap + date-window are config | API quota is the real cap, not storage | [ADR-0010] |
| **Silver text pipeline** — pure, versioned steps `clean → LLM-dissect → fingerprint` on job_description/title (embed = M2); rest is field-mapping | The dissection is the heavy step (ADR-0016); pure+versioned ⇒ replayable | journal §14 · [ADR-0016] |
| **Silver = LLM `Dissector` on every posting** (cheap model `deepseek-v4-flash` → structured `skills[]`/levels/sector/title/seniority/location/language) — populates the market-wide dimensional tables; **language is a byproduct field (no `lingua`)** | Skill-demand/sector analytics need *all* postings, not just gold; **whole pipeline live-runnable on DeepSeek** (ERR-001 worked around) | [ADR-0016] · [ADR-0017] |
| **Gold filter = LLM `FilterStrategy`** (cheap model; judges fit on the dissected fields) — swappable by type | Better fit-precision than coarse rules; runs on already-structured data | [ADR-0015] · [ADR-0016] |
| **Origin-level lineage** — each silver row carries `bronze_id` + `pipeline_version`; field→source is a documented constant | Trace-to-origin + exact re-derive (replay-based) | journal §14 |
| **Never-discard → dimensional modeling** — retain all (bronze lossless); model dims by *insight* not by *field*; grow a dim when a question needs it (retroactively via replay) | Compounding insight without table-per-field sprawl | [ADR-0011] · 00-philosophy |
| **Analytical = constellation model** — facts (fct_job_posting · `fct_job_skill` bridge · fct_job_score · fct_application) over conformed dims (date/skill/title/company/sector/location) + point-in-time profile (SCD2); skills+title derived from text | Insights emerge from joins; the skill bridge is the linchpin | [ADR-0011] |
| **Analytics priority order** — `dim_skill`+`fct_job_skill` first → point-in-time profile + score facts (trends) → `dim_sector`; title/company supporting; built at M5/M6 | Tarig's priority insights: skill-demand/gaps · progress trends · sector intel | [ADR-0011] |
| Scoring: keep 7-factor ATS framework (tune weights) | Encodes the trusted ATS framework | journal §7 |
| Explainability critical (strengths/gaps/strategic assessment) | The reasoning is the value, not just a number | journal §7 |
| Single threshold (default 60), runtime-editable per user, gates shortlist + CV; floor 50, near-miss 10 | One user-tunable knob; change without redeploy; active value stamped per run for measurement | journal §7 + plan §12 |
| Lightweight scoring calibration loop + accuracy SLO | Reliability ROI; corrections tune the prompt | journal §7 |
| **Scoring determinism = best-effort (VG3 softened)** — temp 0 *is* sent (the guaranteed invariant), but `deepseek-v4-pro` is non-deterministic even at temp 0 (MoE/FP variance — observed deltas to ~14 pts); v0 accepts a generous sanity band (~±20), precise stability + calibration deferred to **M7**. Rejected: switch scoring model (deviates from ADR-0017 v0=DeepSeek), average-N samples (3× cost/latency, anti-P1) | The v0 score is a triage signal, not a precise number; ±3 is unachievable on the chosen model — pay for precision only when an accuracy bottleneck (M7) earns it | build-plan Step 5 (Unit B) |
| **VG4 idempotency = at-least-once email (the dual-write window)** — the run-date **`run_log` guard** (PK `(run_date, user_id)`, migration 0003) makes ingest/gold/score idempotent (upserts) and gates the digest to **≤1 per day**; but SES (external) + the `run_log` write can't be atomic, so a rare crash *between* a successful send and `mark_digest_sent` **re-sends** rather than drops — chosen as the safer default (send-then-record). Rejected: guard-*before*-send (risks a *lost* digest — worse); a transactional outbox (overkill at 10–30 jobs/day) = the documented scale-up | A rare duplicate digest beats a silently-missed one; outbox earns its place only at scale | build-plan Step 7 |
| Scam-gate + poster-type label (no hard company filter) | Surface context; user decides | journal §7 |
| UI: email + Notion both first-class; status tracked in Notion | Email triage + Notion act/track | journal §7 |
| Near-miss watch→re-score→graduate loop (full) | Distinctive feature | journal §7 |
| Observability right-sized (few alarms + documented SLOs) | Full suite is over-built for one user | [ADR-0002], §9 |

## Security, cost, infra
| Decision | Why | Owner |
|---|---|---|
| **All secrets in AWS Secrets Manager**, IAM-scoped per function — convention: one secret per service named `jobfetcher/<service>` (e.g. `jobfetcher/jsearch`), JSON value, region us-east-1; created via CLI under `jobfetcher-dev`, read by scripts (boto3) + Lambdas; **never in env/repo** (env-var fallback only for quick local tests) | Zero secrets in code; one store for local + prod (store-once, use-everywhere); security signal | journal §7 |
| **AWS auth: deployed pipeline = no static keys** (Lambdas use **IAM execution roles**, AWS injects creds at runtime); **local** dev = session login (keyless) **or** a **non-root IAM user key** (`jobfetcher` profile) — **never root keys** | Temporary runtime creds > long-lived keys; the local method is the operator's choice, root keys are the one hard no | journal §18 |
| **Runtime Lambda IAM = least-privilege, no Bedrock** — `secretsmanager:GetSecretValue` (`jobfetcher/deepseek` + `jobfetcher/jsearch`) · `rds-data` (Data API) on the cluster · its S3 prefix · SES send; LLM = DeepSeek over HTTPS (Lambda outside VPC ⇒ outbound internet) | DeepSeek replaced Bedrock ([ADR-0017]) ⇒ no `bedrock:InvokeModel`; least-privilege is the security signal | [ADR-0017] · [ADR-0014] |
| Public repo PII-scrubbed; real profile gitignored → private S3 | Privacy + clone-and-runnable sample | [ADR-0007] |
| Cost ceiling ~$50/mo OK; some credits; `terraform destroy` → $0 | Optimize for signal, stay cost-aware | journal §6 |
| IaC: Terraform | Tarig's showcase + most-recognized | journal §6 |
| **Lambda packaging = vendor Linux wheels via `pip --platform manylinux2014_x86_64 --only-binary` (no Docker), bundle the `jobfetcher` pkg + config, prune runtime-provided boto3/botocore, direct `filename` zip (<50 MB)** — built by `scripts/build_lambda.py` before `terraform apply`; the package targets Linux even when built on Windows | Docker-free build on a thermally-limited Windows host; small zip ⇒ cheap cold start; Data API ⇒ no psycopg2, boto3 is runtime-provided | [ADR-0020] |
| **Aurora Data-API connect params (live-only contract)** — the aurora-data-api dialect's connect-kwarg is **`aurora_cluster_arn`** (not `cluster_arn`), and Data-API URLs carry **`%`-encoded ARNs** that must be `%%`-escaped before configparser (Alembic) sees them | Both are reachable *only* on the live Data-API path (local/CI = psycopg2) — caught at deploy, not by tests | [ERR-004] · [ERR-005] · [ADR-0018] |
| Testing: unit + LocalStack/moto (S3/Secrets) + **local Postgres for the DB** + dbt tests + live smoke | Reliability + clone-and-run confidence; DB tests via the aurora-data-api dialect (local↔cloud parity) | journal §6 · [ADR-0018] |
| Enforcement = the gate trio, run as an **agentic per-unit pipeline** (builder→review→**independent fresh-context verifier**→scribe→guardian) + cross-unit fan-out; **CodeRabbit + human = extra independent eyes per PR** | The in-build reviewer can share the orchestrator's blind spots — an unbiased verifier caught real crash-bugs on Step 4 | [ADR-0013] · [ADR-0019] |
| **CI = GitHub Actions** on PR→main + push→main (`ruff` + `pytest --cov --cov-fail-under=85` [`postgres:16-alpine` service] + `terraform validate` + **gitleaks** secret-scan; pre-commit = gitleaks + ruff); **VG7 enforced** in pre-commit + CI; `main` **now requires** the 3 checks (lint-and-test/terraform-validate/secret-scan) — external **GitGuardian/CodeRabbit excluded** so a false-positive can't block a merge; `enforce_admins=false` (docs-direct preserved); **`ruff-format` deferred** (a separate one-time reformat — the tree predates it) | Cheap CI from day one for the release-centric model; behavioral checks (real DB + coverage floor, not presence-only); the format-the-whole-tree churn is a deliberate standalone commit, not folded in | build-plan Step 9 |

## v0 boundary & versioning
| Decision | Why | Owner |
|---|---|---|
| v0 = single Lambda · one source · score · email | Irreducible working core; leaner v0 = more migrations | [04-v0-build-plan] |
| **v0.1.0 SHIPPED + deployed live (2026-06-29)** — 14-resource Terraform stack applied, schema migrated + pipeline ran end-to-end over the Data API (`statusCode 200`, VG4 + VG5 live, SES 0 bounces), then destroyed to ~$0; scale finding: single Lambda ⇏ the full 18-query × 30-day backfill in 15 min → reinforces **M3** | v0 is "done" only when it delivers a real scored shortlist on real AWS; the backfill limit is the first re-derived P2 bottleneck | [phase-index] · roadmap |
| ~~CV = M1~~ → **M1 = pipeline hardening** (P2 protocol overrode the CV hypothesis) | Live full-sweep usage showed the *real* first bottleneck was completing a run at all, not CV tailoring; CV re-queued as a later candidate | [ADR-0021] · roadmap |
| **v0.2.0 SHIPPED (2026-07-02)** — M1 pipeline hardening: **H-1** LLM retry (429/5xx + conn, exp backoff + full jitter; auth/404 fail fast; `LlmConfig.max_retries`/`backoff_base_s`) + symmetric `LlmError` isolation in `land_silver`; **H-2** in-Lambda `ThreadPoolExecutor` for LLM calls (default 8, `$PIPELINE_MAX_WORKERS`; **DB writes stay main-thread** — Data-API dialect thread-safety not relied on) + a **deadline guard** (`partial`/`deferred`, notify skipped on partial) + `aws_lambda_function_event_invoke_config maximum_retry_attempts=0` + memory 512→1024; **H-3** subset-title gold filter (all target tokens required) + `$GOLD_FILTER_STRATEGY = deterministic\|llm` | Re-validated live on the failed backlog: ~13× throughput, no timeouts, 0 run-fatal errors, junk titles eliminated, 21-job digest sent | [ADR-0021] · [ERR-006] · [ERR-007] |
| Semver: v0.x per migration → v1.0.0 at M8 → v1.x/v2.0 | Clean evolution story | roadmap |

*Reference labels like [ADR-000X] resolve under [../adr/](../adr/); [ERR-00X] under [errors.md](errors.md); [phase-index] / [04-v0-build-plan] under their docs.*
