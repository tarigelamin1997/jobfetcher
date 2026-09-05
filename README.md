# JobFetcher

**A serverless job-matching pipeline that fetches roles, scores them against your real profile with an LLM, and emails you a daily shortlist — built as an *evolutionary architecture* you can watch grow, one deliberate, documented migration at a time.**

> **Status: <!--gen:current_release-->`v0.12.1` (2026-09-02)<!--/gen--> SHIPPED — the outage release** (previous: `v0.12.0`, 2026-07-17)**.** The minimal core (`v0.1.0`) is live on AWS, and **<!--fact:releases-->15<!--/fact--> releases** have shipped in all — each a clean, tagged release — and as of `v0.9.0` the pipeline **runs itself fully unattended** (the daily 06:00 UTC EventBridge cron flew solo end-to-end on 2026-07-10, digest delivered with nobody watching).
>
> **⚠️ Correction (2026-09-02) — the unattended claim above was true, then stopped being true, and nobody noticed for 38 days.** The pipeline returned `statusCode: 500` on **every run from 2026-07-25 to 2026-09-01**; the last digest went out **2026-07-24**. Three independent faults shared one symptom: the gold-filter read crossed the RDS Data API's 1 MB cap, the LLM account ran out of credit, and reasoning tokens consumed the whole `max_tokens` so the API returned HTTP 200 with empty content. The alarms fired on all 38 days and reached the operator — **detection was never the gap**; a signal arriving every morning had become indistinguishable from the product.
>
> **Resolved and live-validated 2026-09-02:** `{'gold': 618, 'scored': 618, 'failed': 0, 'deferred': 0}`, `partial: False`; 1,182 scored, nothing pending; digest sent. Root causes, the two measurement errors made while fixing it, and the method are written up in [ERR-010 → ERR-014](docs/ledgers/errors.md), [ADR-0036–0038](docs/adr/), and [decision journal §36](docs/01-session-decision-journal.md) — which is the honest version, including the wrong turns.

A single scheduled Lambda runs **JSearch fetch → bronze (S3 + Postgres) → silver (DeepSeek dissect) → gold filter → 7-factor ATS score (+7 shadow subscores) → SES daily digest (with a presigned full-list report link)**, on Aurora Serverless v2 + the RDS Data API, with Terraform infra (S3 remote state), Secrets Manager, CloudWatch alarms → SNS, a real test pyramid, and GitHub Actions CI. **The release arc since v0** — each a bottleneck picked from real use, not from the plan: `v0.2.0` M1 **pipeline hardening** → `v0.3.0/.1` **settings + runtime config in S3** → `v0.4.0` **reassess/replay** → `v0.5.0` **query/filter access** → `v0.6.0` **email UX** → `v0.7.0` **"the pipeline remembers"** (score + outcome lineage) → `v0.8.0` **scorer integrity** (7 subscores + shadow `code_total`) → `v0.9.0` **ops hardening** (it starts running itself) → `v0.10.0` **reachable full-list** → `v0.11.0` **scorer boundary self-consistency** → `v0.12.0` **full S3 audit persistence + a local control panel** → `v0.12.1` **the outage release**. Two units shipped between tags: the **public outcome-capture endpoint** (INV-001) and the **silent-500 alarm** (INV-002). `v0.7.0`–`v0.12.0` were built by the **agentic squad workflow** (Investigator → Surgeon → Examiner per bottleneck). **What each release actually changed, with the numbers measured live, is in the [CHANGELOG](CHANGELOG.md)** — it owns that story, and this README links rather than restates it ([why](docs/05-methodology.md#one-canonical-home-per-fact-err-016)). Everything past v0 is a *hypothesis* re-derived after each release via the bottleneck protocol — see the [roadmap](docs/03-roadmap.md) · [CHANGELOG](CHANGELOG.md) · live [phase index](docs/ledgers/phase-index.md).

**Dual purpose, equal weight:** a tool Tarig Elamin (Data Engineer, Riyadh → GCC) uses daily to find and score jobs, *and* a portfolio piece that proves production AWS + Data-Engineering skill. Every component must earn both.

---

## The problem

A serious job search drowns you in noise: dozens of postings a day, most a poor fit you can't tell apart from the good ones until you've spent 45 minutes reading and tailoring. JobFetcher turns that into a daily, scored shortlist — *"here are the roles actually worth your time, with the reasons why"* — so the per-job triage cycle drops from 45 minutes to 5.

It does **not** auto-apply (external ATS automation is brittle and risky). It removes the *discovery, filtering, and triage* toil and leaves the human decision where it belongs.

## What makes this repo worth reading

A personal-scale tool built to **production standards**, and deliberately an exercise in **evolutionary architecture**:

- It ships as a **minimal core first** (v0), then grows only by **solving the next real bottleneck** — every added piece of complexity is justified by a capability it unlocks and recorded in an ADR.
- Each migration is a **clean, observable GitHub release**: a semver tag, a CHANGELOG entry recording what was measured live, an ADR naming the rejected alternatives, and — where the topology changed — the updated Mermaid diagram in [`docs/diagrams.md`](docs/diagrams.md). You can read the architecture *evolve* through that trail. *(This bullet used to promise a **before/after diagram per release**. Fifteen releases shipped without one; the claim is [retracted rather than quietly dropped](docs/adr/0001-evolutionary-architecture.md).)*
- It is **honest about scale**: at ~10–30 jobs/day nothing here is justified by load — so every choice is defended on *fit and judgment*, not buzzwords. Where something exists to demonstrate a skill, it is labeled as such.

---

## Architecture

### As-built (what's live today, `v0.12.1`)

One EventBridge-scheduled Lambda (`jobfetcher.handlers.pipeline.handler`) runs the whole operational medallion **fully unattended** (the daily 06:00 UTC cron ran solo end-to-end 2026-07-10), threading one correlation `run_id` through logs, rows, and S3 objects. Since v0 it has gained **in-Lambda concurrency** (a `ThreadPoolExecutor` fans out silver dissection with all DB writes kept on the main thread) behind a **deadline guard** (a run returns `partial` rather than timing out), a **`{"mode":"reassess"}` replay path** (re-score already-bronzed postings against the current profile, zero JSearch calls), **runtime config read from S3** (settings change with no rebuild/redeploy), **append-only `score_event` + `application_event` lineage** (re-scores and human overrides never erase judgments), a **7-factor score carrying 7 bounded subscores + a SHADOW-mode weighted `code_total`** (logged + persisted, never yet the product number — an M7 cut-over criterion), a **card-style SES digest that tells the truth** — "new since last digest" leads (graduations badged `↑ old→new`), earlier matches compact into a "still open" count, and same-fingerprint repeats collapse to one card ([ADR-0027](docs/adr/0027-digest-truthfulness.md)) — whose overflow lines now carry a **presigned link to a self-contained HTML page of every scored job** ([ADR-0030](docs/adr/0030-reachable-full-list-from-digest.md)), and an **ops-hardened runtime** — a **`{"mode":"smoke"}` post-deploy gate**, two **CloudWatch alarms → SNS email** (a dead-man on the daily rule + a Lambda Errors alarm), and an **Aurora cold-start resume-wait** so a scale-to-zero resume is waited out, not fatal ([ADR-0029](docs/adr/0029-ops-hardening.md); [ERR-009](docs/ledgers/errors.md)), a **boundary-resampled scorer** (a score near the cutoff is re-scored and the median kept, so the shortlist boundary isn't a coin-flip; a graduation is badged only on a real profile change — [ADR-0031](docs/adr/0031-boundary-self-consistency-honest-graduations.md)), and **full S3 audit persistence** (every stage's structured results also land in S3 as batched JSONL — `silver/`/`gold/`/`scores/`/`runs/`, non-fatal, alongside Aurora) plus a **local Streamlit control panel** (`streamlit run scripts/panel.py`) for browse/curate/config ([ADR-0032](docs/adr/0032-full-s3-audit-persistence.md) · [ADR-0033](docs/adr/0033-local-control-panel.md)):

```mermaid
flowchart LR
  EB["EventBridge<br/>daily 06:00 UTC cron"] --> H["one Lambda<br/>handlers.pipeline.handler"]
  H --> SMK{"mode?"}
  SMK -. smoke .-> SG["smoke gate<br/>Data-API + alembic_version<br/>→ 200/400/500"]
  SMK -- default --> F["fetch<br/>JSearch API"]
  F --> B["bronze<br/>raw JSON → S3 + bronze_posting"]
  B --> S["silver<br/>clean + DeepSeek dissect → posting"]
  S --> G["gold<br/>deterministic FilterStrategy → gold_candidate + 1:1 cluster"]
  G --> SC["score<br/>DeepSeek 7-factor ATS + 7 shadow subscores<br/>+ weighted code_total → score rows"]
  SC --> N["notify<br/>SES card digest + presigned full-list link"]
  N --> RPT["report<br/>self-contained HTML → S3 reports/"]
  N -. "signed ✓Mark-applied links" .-> CAP
  RPT -. "signed links" .-> CAP
  YOU(["you, in your inbox"]) -- "one click (GET ?token=…)" --> CAP["capture Lambda<br/>PUBLIC Function URL (auth = NONE)<br/>HMAC verify BEFORE any DB touch"]
  CAP -- "valid token only" --> PG
  SM["Secrets Manager<br/>jobfetcher/deepseek · jobfetcher/jsearch<br/>+ the Terraform-owned capture signing key"] -.-> H
  SM -.-> CAP
  B -. raw .-> S3[("S3<br/>raw · audit · reports · config")]
  RPT -. presigned .-> S3
  S & G & SC -. "audit jsonl v0.12<br/>silver/gold/scores" .-> S3
  H -. run summary → runs/ .-> S3
  B <--> PG[("Aurora Serverless v2<br/>+ RDS Data API")]
  S <--> PG
  SC <-. score_event lineage .-> PG
  N -. send-once run_log .-> PG
  H -. errors / dead-man .-> AL["CloudWatch alarms<br/>→ SNS email"]
  PANEL["scripts/panel.py (local)<br/>browse · curate · config"] <-. live Data API .-> PG
  PANEL -. config push .-> S3
```

- **Idempotent per run-date:** upserts + a `run_log` send-once guard (PK `(run_date, user_id)`) mean a re-run produces identical rows and **at most one digest per day**; a stage failure returns `500` so the next invocation resumes. SES (external) can't join the DB transaction, so the email is **at-least-once** — send, then mark.
- **Concurrent, isolated, deadline-bounded** ([`v0.2.0` M1](docs/adr/0021-m1-pipeline-hardening.md)): the LLM dissect step runs on a `ThreadPoolExecutor` (~13× throughput; DB writes stay main-thread), each posting is retried with jitter and **failure-isolated** (a provider blip skips one posting, never the run), and a deadline guard returns `partial` before the 15-min wall — with `maximum_retry_attempts=0` so AWS never blind-retries a dead run.
- **Immutable bronze enables replay** ([`v0.4.0`](docs/adr/0023-reassess-replay.md)): a `{"mode":"reassess"}` invocation re-scores the already-fetched postings against the **current** profile with **zero JSearch calls** — as your profile grows a `stretch` role graduates to `strong_fit`, and `previous_score` tracks before→after. Live-proven: 180 reassessed, 15 graduated, bronze untouched.
- **Runtime config in S3, not the zip** ([`v0.3.0`](docs/adr/0022-runtime-config-in-s3.md)): the `SearchSpec` + profile YAMLs are read from S3 at runtime and the profile row **re-syncs from config every run** (fixing the old write-once trap). Changing any of the three strictness knobs (threshold · hard-floor · near-miss band) or the JSearch query is `python scripts/push_config.py` — no rebuild, no redeploy.
- **Gold is deterministic in v0** — at 10–30 jobs/day an LLM gold-filter is largely redundant with the Scorer (P1 minimalism). The subset-title filter ("Data Architect" needs `data`+`architect`) is config-selectable via `$GOLD_FILTER_STRATEGY`; an `LlmFilterStrategy` is built behind the same port for scale.
- **One public write surface, and the auth is a token — not the network** ([INV-001 / ADR-0035](docs/adr/0035-outcome-capture-endpoint.md)): the "✓ Mark applied" links in the digest and the full-list report hit a **second Lambda behind a Function URL with `authorization_type = "NONE"`**. Every request must carry a short-lived **HMAC-signed token** scoped to exactly `{posting_id, status}` (30-day TTL, signing key generated and owned by Terraform in Secrets Manager). `verify` runs in constant time **before any DB touch**, so a forged or expired token returns 400 having written **zero rows**; a valid one drives exactly one append-only `application_event`. This exists because outcome capture was CLI-only, so the log had **0 rows** and scoring had no ground truth to calibrate against. **Accepted trade-off:** a one-click GET can be pre-fetched by an email scanner → a spurious `applied`; the append-only log plus `track.py override` makes that correctable, and a confirmation interstitial is the documented fast-follow.
- **⚠️ The JSearch sweep runs every 3rd day, not daily — and that is deliberate.** The Lambda still fires daily at 06:00 UTC (the dead-man alarm watches a 24-hour window and cannot be widened), but the *source sweep* costs quota: the free tier is **200 requests/month** and one sweep is `3 titles × 5 countries × 1 page = 15`. Daily sweeping needed ~450/month — **2.25× over** — so the tool silently ingested nothing for roughly half of every month until 2026-09-04 (the often-quoted **2.7× / ~19 dead days** are the *pre-fix* figures, from the 6-country `3 × 6 × 1 = 18` sweep at ~540/month; they are correct in [02-architecture](docs/02-architecture.md) and [ERR-017](docs/ledgers/errors.md), and were wrong here — spliced onto the post-fix sweep size) ([ERR-017](docs/ledgers/errors.md)). A non-fetch day still scores, digests and reports; the run summary says `fetch_stopped: not_a_fetch_day`. **Raise `FETCH_EVERY_N_DAYS` and the RapidAPI plan together — exceeding the quota fails silently.**
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
| **Compute** | AWS Lambda, outside any VPC — the scheduled pipeline handler (EventBridge daily cron) **plus a second handler from the same zip behind a public Function URL** for outcome capture ([ADR-0035](docs/adr/0035-outcome-capture-endpoint.md)) |
| **Store** | Aurora Serverless v2 (**0–2 ACU**: `min_capacity = 0` scale-to-0, `max_capacity` from `var.db_max_acu`, default 2) via the **RDS Data API** · S3 (raw bronze payloads · runtime config YAMLs · the **`silver/`/`gold/`/`scores/`/`runs/` audit trail** (v0.12.0) · presigned reports) |
| **DB access** | SQLAlchemy 2 + `sqlalchemy-aurora-data-api` behind a `Repository` port · Alembic migrations |
| **LLM** | OpenAI-compatible API, **provider + model in config** ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md)); v0 = **DeepSeek** (`deepseek-v4-flash` dissect · `deepseek-v4-pro` score). Bedrock parked. |
| **Email** | SES (HTML + plaintext digest) |
| **Secrets** | Secrets Manager — **four**, and only two are yours to create. `jobfetcher/deepseek` + `jobfetcher/jsearch` are **CLI-created data-source keys**; `jobfetcher/capture-token` (the HMAC signing key) and `rds!cluster-…` (the Aurora master password, from `manage_master_user_password = true`) are **generated and owned by Terraform** — never in outputs or logs, and both Lambdas read the DB one via `$DB_SECRET_ARN`. Don't hand-delete the `rds!` secret; it belongs to the cluster. |
| **IaC** | Terraform ≥ 1.10 — **<!--fact:tf_resources-->32<!--/fact--> resources**, us-east-1, least-privilege IAM (no Bedrock); **S3 remote state** (`backend "s3"`, native `use_lockfile` locking, deliberately unmanaged state bucket) |
| **Known trade-off** | Aurora runs **unencrypted at rest**, deliberately — a labeled decision, not an oversight ([ADR-0038](docs/adr/0038-aurora-unencrypted-at-rest.md)). The data is experimental and re-derivable; encryption is free but cannot be enabled in place, so turning it on destroys and recreates the cluster. Revisited the moment the data stops being throwaway. |
| **Observability** | 3 CloudWatch alarms (dead-man on the daily rule · Lambda Errors · a returned `statusCode:500` via log-metric-filter) → 1 SNS topic → email; `{"mode":"smoke"}` post-deploy gate |
| **AWS SDK** | boto3 |
| **Tests** | pytest — **<!--fact:tests-->607<!--/fact--> collected: <!--fact:tests_unit-->539<!--/fact--> unit + <!--fact:tests_integration-->68<!--/fact--> integration** (integration needs Docker or `$JOBFETCHER_DB_URL`; live-key tests skip without a key), live smoke, ~95% coverage in CI (85% floor) |
| **CI** | GitHub Actions — ruff + tests + 85% coverage floor + `terraform validate` + **gitleaks** secret-scan; pre-commit (gitleaks + ruff) |

dbt / Snowflake / Debezium-CDC / Spark are documented *scale-paths* or live in sibling projects — not in this repo today. See the [decision journal](docs/01-session-decision-journal.md).

---

## How to run

> **New here?** Follow the self-contained **[Getting Started guide](docs/getting-started.md)** — a clone-to-first-run walkthrough (accounts → keys → SES → your own state bucket → deploy → first digest → teardown, with troubleshooting). The steps below are the concise reference for readers already comfortable with AWS + Terraform.

### Prerequisites

- An **AWS session** for the `jobfetcher-dev` IAM user (region us-east-1).
- Two **Secrets Manager** secrets **you create**: `jobfetcher/deepseek` (DeepSeek API key) and `jobfetcher/jsearch` (JSearch API key). *(Terraform creates two more on `apply` — the capture-endpoint signing key and the Aurora master password — so a healthy account shows four. You don't manage those.)*
- **SES** sender + recipient addresses verified (sandbox is fine for personal use).
- Your config: copy the committed samples to the gitignored local files and fill them in —
  - `config/search_config.sample.yml` → `config/search_config.local.yml` (the per-user [`SearchSpec`](src/jobfetcher/core/search_spec.py); every field required, fails loudly on anything missing/invalid).
  - `config/profile.sample.yml` → `config/profile.local.yml` (the scoring source of truth).
  - The samples are sanitized; **real profile/PII is gitignored** and never enters the repo.

### Deploy

```bash
python scripts/build_lambda.py        # package the Lambda artifact
terraform -chdir=terraform init       # S3 remote-state backend (unmanaged bucket, native locking)
terraform -chdir=terraform apply      # 32 resources (Aurora + Data API, S3, Lambda, EventBridge, SES, IAM, alarms + SNS)
alembic upgrade head                  # create the schema on Aurora, over the Data API
# invoke {"mode":"smoke"} → 200 (deploy gate); EventBridge then fires daily at 06:00 UTC → statusCode 200
```

`terraform destroy` returns the account to ~$0 when idle (Aurora scales to zero between runs regardless).

### Day-to-day (no redeploy needed)

Once deployed, the routine loop runs entirely on config + invokes — the Lambda zip stays put:

```bash
python scripts/push_config.py         # validate + upload the config YAMLs to S3 → new settings live next run
# ...invoke {"mode":"reassess"} → re-score every bronzed posting against the updated profile, 0 JSearch calls
python scripts/export.py              # snapshot the DB → portable SQLite + CSV (flat jobs table + bronze/runs/profile)
python scripts/track.py applied <id>  # record an outcome: applied|interview|offer|rejected|withdrawn (find/events/override too)
python scripts/preview_digest.py      # render the card-style email in a browser before it goes out
pip install -e '.[panel]' && streamlit run scripts/panel.py   # local control panel: browse/curate the DB + edit config (v0.12.0)
```

- **Change any setting** — edit `config/*.local.yml`, run `push_config.py`; the three strictness knobs and the JSearch query take effect on the next run, no rebuild.
- **Re-score on a better profile** — add a skill, push config, invoke `{"mode":"reassess"}`; watch `stretch` roles graduate to `strong_fit` ([ADR-0023](docs/adr/0023-reassess-replay.md)).
- **Query your data** — `export.py` gives you a portable file you filter/search/sort in Datasette (`pip install -e '.[query]'`), DB Browser, or Excel ([ADR-0024](docs/adr/0024-query-via-export.md) · [`docs/querying.md`](docs/querying.md)).
- **Browse + curate live** — `pip install -e '.[panel]'` → `streamlit run scripts/panel.py`: a **local control panel** (Streamlit, never in the Lambda) to browse/filter every scored job, override a score / record an outcome, and edit your search config → push to S3 — all against the live DB, no redeploy ([ADR-0033](docs/adr/0033-local-control-panel.md)).
- **Record what happens after the digest** — `track.py applied|interview|offer|rejected|withdrawn <posting_id>` appends to an immutable outcome log (`find "<title>"` looks up the id; `events` shows a job's trail); the next `export.py` shows each job's **latest application status**. **Override a score you disagree with** — `track.py override <posting_id> <score>`: it sets `score_override` *and* lands in the same lineage log as the LLM's scorings — nothing is erased, and the override survives later re-scores ([ADR-0026](docs/adr/0026-outcome-tracking-override-lineage.md)).
- **Read the digest as news** — "New since last digest" leads with full cards (a graduation is badged `↑ old→new`); everything already announced compacts into a "still open" count with top-5 one-liners, same-role repeats collapse to one card (`seen n×`), and `digest_max_age_days` ages stale postings out ([ADR-0027](docs/adr/0027-digest-truthfulness.md)).
- **Preview the email** — `preview_digest.py` renders the digest locally so format changes are seen before send.

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
- **M1 re-validated live (2026-07-02):** re-run on the exact ~132-posting backlog that had killed the pre-fix code → `statusCode 200`, backlog fully dissected + scored, **~13× throughput** (~1.1→~14–15 dissections/min), **0 run-fatal errors** (failures isolated per-posting), junk eliminated, **21-job digest sent** ([ADR-0021](docs/adr/0021-m1-pipeline-hardening.md); ERR-006/007).
- **Reassess proven live (2026-07-06):** `{"mode":"reassess"}` re-scored **180** postings against an improved profile, **15 graduated** (e.g. Data Platform Engineer @ Saudi Aramco 35→85), **bronze unchanged** (no re-fetch) ([ADR-0023](docs/adr/0023-reassess-replay.md)).
- **Lineage proven live (2026-07-08):** migrations `0004`+`0005` applied over the Data API with the **228-score baseline backfill verified**; a reassess smoke wrote **771 lineage events through the new dual-write**, **10 graduations standing**, and one `track.py override` exercised the codebase's first **`.rowcount` over the Data API** (clean) ([ADR-0025](docs/adr/0025-score-event-lineage.md) · [ADR-0026](docs/adr/0026-outcome-tracking-override-lineage.md); ERR-008).
- **First unattended flight (2026-07-10 06:00 UTC):** the daily EventBridge cron ran the whole pipeline end-to-end and delivered a digest **with nobody watching** — post-deploy smoke gate `200 @ 0006_subscores`, both alarms armed ([ADR-0029](docs/adr/0029-ops-hardening.md)).
- **Reachable full-list proven live (2026-07-10):** `get_all_scored` over the Aurora Data API returned **286 rows** (61 above / 225 below threshold), rendered into a **~242 KB self-contained HTML report** written to S3 and presigned into the digest ([ADR-0030](docs/adr/0030-reachable-full-list-from-digest.md)).
- **The public capture endpoint proven live (2026-07-20):** the security core was validated on the deployed Function URL *without polluting the outcome log* — forged / expired / absent token → **400**, valid token for an unknown posting → **404 with 0 rows written**. Auth is verified before any DB touch, so the negative cases never reach Aurora ([ADR-0035](docs/adr/0035-outcome-capture-endpoint.md); [INV-001](docs/investigations/INV-001-dark-feedback-loop/README.md)).
- **Validation gates VG1–VG8** are **behavioral and carry a negative case** (a presence/liveness check is no gate): ingestion, scoring, best-effort determinism, idempotency, notification, teardown, secrets hygiene, threshold-is-config. Each maps to named positive + negative tests in [`tests/README.md`](tests/README.md).
- **CI** runs ruff, the test suite with an 85% coverage floor, `terraform validate`, and a gitleaks secret-scan on every push.

---

## Roadmap

`v0.1.0` is the **irreducible working core**. Everything after it is chosen by the **bottleneck-decision protocol**, not a fixed plan: ship → use → surface the top-3 bottlenecks to the next real capability → rank by leverage (capability ÷ complexity) → break the biggest with the minimal migration → repeat. **The protocol has already overruled the plan:** the pre-drawn *M1 = CV tailoring* hypothesis lost to real use — live running surfaced pipeline throughput/reliability as the biggest bottleneck, so **M1 became pipeline hardening** and CV tailoring was re-queued. <!--fact:releases-->15<!--/fact--> releases have shipped; the still-future queue below is *direction, not contract* — re-derived after each release. Full protocol + migration table in [`docs/03-roadmap.md`](docs/03-roadmap.md).

```mermaid
flowchart LR
  v0["v0.1.0 ✅<br/>fetch → score → email<br/>deployed · $0"] --> M1["v0.2.0 ✅<br/>M1 pipeline hardening"]
  M1 --> S["v0.3–0.6 ✅<br/>settings/config-in-S3 ·<br/>reassess · query · email UX"]
  S --> S2["v0.7–0.10 ✅<br/>lineage + outcomes · scorer<br/>subscores · ops hardening ·<br/>reachable full-list · unattended"]
  S2 --> S3n["v0.11–0.12 ✅<br/>scorer boundary self-consistency ·<br/>S3 audit trail · local control panel"]
  S3n --> CV["⬜ CV tailoring<br/>(old M1, re-queued)"]
  CV --> M2["⬜ multi-source + dedup"]
  M2 --> M3["⬜ Step Functions"]
  M3 --> M4["⬜ Notion + near-miss"]
  M4 --> M5["⬜ dbt marts + skill/sector intel"]
  M5 --> M7["⬜ observability + calibration"]
  M7 --> M8["⬜ → v1.0.0"]
```

---

## Design philosophy & docs

This project treats **documentation as infrastructure** — the repo is the memory; any contributor (human or agent) resumes from the files alone. Two principles govern every decision:

- **P1 — Absolute minimalism.** Build the minimal complexity that solves the *present* problem; design cheap seams for the future, don't build the future.
- **P2 — Bottleneck-driven evolution.** After each release, solve the highest-leverage bottleneck with the minimal migration, ship, repeat.
- **Defensibility rubric.** Every component must answer *"why this and not the simpler thing?"* without "to put it on my resume." If it can't, it's cut or labeled an honest showcase.

| Doc | What it holds |
|---|---|
| 🚀 [`docs/getting-started.md`](docs/getting-started.md) | **Clone → first run** — the self-contained setup walkthrough (start here) |
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
