# 04 · v0 Build Plan

> The **only fully-planned stage** (everything past v0 is planned just-in-time — [P2](00-design-philosophy.md#p2--bottleneck-driven-evolution)). This is the exhaustive apply-sequence for the minimal working core. **Do not start building it until Tarig has reviewed the docs** (the review gate). Each step has **WHY / WAIT-FOR / FAILURE-MODE**. The build itself follows safety-first: one change at a time, verify before+after, tag before risk, destructive ops need approval.

---

## v0 in one sentence
EventBridge (daily) → **one Lambda**: fetch from **one source** → land raw in **S3** + upsert rows in **Postgres** → **Bedrock** scores each new job against the profile → **daily email** with the scored shortlist. Terraform + Secrets Manager + tests + minimal CI. **That's all.**

**Explicitly NOT in v0:** CV tailoring · multi-source · clustering dedup (one source ⇒ only exact-id re-fetch dedup) · Step Functions · Notion · near-miss/graduation · warehouse/dbt · full observability. Each is a later migration.

## v0 contract (for [ledgers/interface-contracts](ledgers/interface-contracts.md))
- **Consumes:** `search_config` (queries/locations), candidate `profile` JSON, source API key, Bedrock access, a verified SES sender+recipient.
- **Produces:** `posting` rows (status `fetched`), `score` rows (status `scored`) in Postgres; raw payloads in S3 `raw/`; one daily email. A documented schema other migrations build on.

---

## Prerequisites (one-time, before the apply sequence)
1. **AWS account** in **us-east-1**, **Bedrock-ready for Anthropic Claude** — three things (all confirmed real in [ERR-001](ledgers/errors.md)): (a) **model access** enabled; (b) invoke via the **`us.anthropic.*` inference-profile id**, NOT the base model id (base id → `ValidationException`); (c) **per-day token quota > 0** — a brand-new account is gated at 0 (**non-adjustable**) and throttles every call with *"Too many tokens per day"*; this lifts via account maturity or an AWS Support case (a valid payment method + free credits do **not** bypass it). · *WAIT-FOR:* a 1-token `converse` against `us.anthropic.…` returns a completion. · *FAILURE-MODE:* `ValidationException` → base id used; `ThrottlingException`/0-quota → billing/quota; `AccessDeniedException` → access/region.
2. **One job-source API key** — JSearch (RapidAPI) **or** Adzuna app id+key. (v0 uses exactly one; the second source is M2.)
3. **SES**: verify the sender identity and the recipient (sandbox mode is fine for a single recipient). · *FAILURE-MODE:* `MessageRejected` → identity not verified.
4. **Candidate profile**: real `profile.yml`/`profile.json` kept **locally + gitignored**; a **sanitized sample** committed so the repo is runnable by others.
5. **AWS authentication (no static keys anywhere).** JobFetcher stores **zero long-lived access keys**. *Locally* (CLI/Terraform), the default `samareltayeb` profile authenticates via a **session login** → temporary STS credentials (cached under `~/.aws/login/cache`, auto-used by every SDK/CLI call); **re-sign-in when they expire**. *In production*, each deployed Lambda authenticates via its **IAM execution role** — AWS injects temporary role credentials at runtime (Terraform provisions the least-privilege role; Step 3) — nothing to configure, expire, or re-login. Third-party API keys (JSearch/SES) are a separate concern → Secrets Manager. · *WAIT-FOR:* `aws sts get-caller-identity` returns the account/identity with no `--profile` flag. · *FAILURE-MODE:* `ExpiredToken`/`InvalidClientTokenId` locally → re-run the sign-in; an expired *local* session never affects the *running* pipeline (that's IAM roles).

## Sub-decisions to resolve at build start (flagged, not pre-locked)
- **D-v0-1 — Postgres flavor.** *Recommendation:* **Aurora Serverless v2 + RDS Data API** — no VPC/NAT for the Lambda (HTTP data access), fewer moving parts ⇒ more reliable, scales toward $0 idle. *Alternative:* **RDS `db.t4g.micro`** (cheaper sticker price, but Lambda-in-VPC + VPC endpoints to avoid a NAT gateway). Decide on the simplicity-vs-cost tradeoff at build start; record an ADR.
- **D-v0-2 — Source: ✅ resolved → JSearch** ([ADR-0010](adr/0010-job-source-jsearch.md)). Single source for v0. **Step 0 probes JSearch's free tier first**, then upgrades to Pro ($25/mo). Adzuna deferred.

---

## Apply sequence

### Step 0 — Coverage probe (resolve the source on evidence, at $0)
Before building, validate the source. Register a RapidAPI key, subscribe to **JSearch's free tier (200 req/mo)**, and run real queries for the target market — e.g. `query="Data Engineer in Riyadh", country="sa"` and `query="Data Platform Engineer", country="ae"`, with a `date_posted` window. Eyeball: how many relevant DE roles/day, and do the **descriptions come through complete?**
- **WHY:** "is coverage good enough" is the bottleneck that justifies paying — settle it with data, not a guess. The free tier is plenty for the probe.
- **WAIT-FOR:** a clear yes/no on GCC depth + full JD text. If yes → subscribe **Pro ($25/mo)** and proceed. If thin → record it and reconsider (probe Adzuna / a regional board) before building.
- **FAILURE-MODE:** sparse results → widen queries (titles × cities) before concluding the source is weak; truncated JDs → that source is disqualified for scoring.

### Step 1 — Scaffold the project + resolve sub-decisions
Create the Python package layout (ports-&-adapters from day one, so M1+ stay clean), the Terraform skeleton, and resolve D-v0-1/D-v0-2 with a short ADR each.
- Suggested layout: `src/jobfetcher/{adapters/,core/,handlers/}`, `src/jobfetcher/core/{models.py,scoring.py,fingerprint.py}`, `tests/`, `terraform/`, `migrations/` (Alembic), `config/` (sample), `pyproject.toml`.
- **WHY:** boundaries + flags now = cheap migrations later (a foundational migratability requirement).
- **WAIT-FOR:** `pip install -e .` succeeds; `ruff`/`pytest` run (no tests yet).
- **FAILURE-MODE:** import path tangles → fix the package layout before writing logic, not after.

### Step 2 — Data contract + v0 schema
Define **Pydantic** models for the normalized posting + the Bedrock score output (the data-contract boundary). Write the **Alembic** migration for the v0 tables: **`bronze_posting`** (immutable raw landing), `posting` (silver — normalized), `cluster` (trivial 1:1 in v0), `score`, `profile`. Silver `posting` **retains all source fields (lossless)** — v0 has no marts, but because nothing is dropped (and bronze is immutable), the analytics dimensions ([ADR-0011](adr/0011-dimensional-analytical-model.md), M5/M6) can be modeled retroactively over history.
- **WHY:** contracts at the boundary prevent "assumed-from-inspection" bugs; Alembic from day one makes schema evolution first-class.
- **WAIT-FOR:** `alembic upgrade head` creates the tables; a round-trip insert/select of a sample posting works.
- **FAILURE-MODE:** validation errors on real API payloads → tighten the adapter's normalization, not the contract.

### Step 3 — Provision infrastructure (Terraform)
Modules/resources: S3 bucket (`force_destroy = true`), Secrets Manager entries (empty placeholders), the chosen Postgres, the Lambda + IAM role (least-privilege: only its S3 prefix, its secrets, Bedrock invoke, SES send, DB access), EventBridge daily rule, SES identity. Populate secrets once via CLI (never committed).
- **WHY:** reproducibility is the portfolio value; least-privilege is the security signal.
- **WAIT-FOR:** `terraform apply` clean; `terraform destroy` proven to return to $0 on a throwaway run.
- **FAILURE-MODE:** IAM `AccessDenied` at runtime → the role is missing a specific permission; add exactly that one, not `*`.

### Step 4 — JSearch adapter + bronze→silver landing
Implement the **JSearch adapter** behind the source-port interface: call per query (keywords + `country` + `date_posted`), **paginate to a config page-cap**, with rate-limit/backoff + **quota-header awareness** (stop gracefully near quota, landing what you have). **Bronze:** write each raw result *untouched* to S3 `raw/jsearch/{date}/` + a `bronze_posting` row (immutable). **Silver:** normalize through the Pydantic contract into `posting`; exact-id dedup on re-fetch. Thread the **correlation `run_id`**. Request-budget knobs (queries, page-cap, date window) live in config.
- **WHY:** bronze-first = the land-daily guarantee + replay; the adapter is the seam that makes M2 (multi-source) a one-file add.
- **WAIT-FOR:** a real call lands raw to S3 + `bronze_posting`, and ≥1 normalized `posting` in silver; re-running the same day adds no duplicate bronze rows for the same source-id.
- **FAILURE-MODE:** API shape drift → the contract catches it loudly (map the field in the adapter); quota exhausted → land what you got + log, never crash the run.

### Step 4b — Gold filter (cheap, before the LLM)
Apply the **deterministic profile filter** over silver → mark the **gold candidate** subset (`posting.status = gold_candidate`): location in target set, title matches target roles, seniority band, exclude keywords/companies. Filter rules are config.
- **WHY:** the LLM is the expensive step — only score likely fits. Below-bar rows stay in bronze/silver for later analytics.
- **WAIT-FOR:** an obviously-irrelevant posting (wrong location/title) is **excluded** from gold while a matching one is included.
- **FAILURE-MODE:** filter too aggressive → real fits dropped before scoring. Keep it *coarse* (the LLM does the fine judgment); log filtered counts.

### Step 5 — Scorer (Bedrock)
Implement scoring **over the gold candidates only**: load profile, build the 7-factor ATS system prompt, call Bedrock **via the Converse API** with the **config-selected model id** ([ADR-0012](adr/0012-model-agnostic-llm.md); current candidate `moonshot.kimi-k2-thinking`, ON_DEMAND) at **temperature 0**, with prompt-based structured-output enforcement, parse into the score contract (`score`, `fit_category`, `strengths`, `gaps`, `strategic_assessment`, `skills_extracted`, `sector`, `poster_type`, `legitimacy_verified`), write `score` rows. Apply the **single config threshold** (default **60**) + hard-floor **50** + near-miss band **10** — **read from the per-user `profile` config at runtime, never hardcoded.** This one threshold gates the email shortlist now (and CV writing from M1). Stamp the active threshold onto each run's records.
- **WHY:** scoring is the core value; explainability is the value, not just the number.
- **WAIT-FOR:** the same JD+profile scored twice lands within ±3 points (determinism check).
- **FAILURE-MODE:** `ValidationException` → wrong model id/region; malformed JSON → tighten the output schema / add a single retry.

### Step 6 — Notifier (SES daily digest)
Render a clean daily email: top matches (≥ threshold) with score, one-line why, and the apply link; a short "below threshold" count. Send via SES.
- **WHY:** email is the v0 surface — morning triage in 60 seconds.
- **WAIT-FOR:** a real digest email arrives with correct content and links.
- **FAILURE-MODE:** `MessageRejected` → unverified identity (sandbox) — verify recipient.

### Step 7 — Single Lambda handler
Wire fetch → score → notify in one handler, idempotent for a given run date (re-running the same day doesn't double-write or double-email). Emit structured logs prefixed with the correlation `run_id`.
- **WHY:** one Lambda is the minimal orchestration; idempotency is a foundational reliability property (and a migratability requirement).
- **WAIT-FOR:** two runs for the same date produce the same DB state and at most one email.
- **FAILURE-MODE:** partial failure mid-run → the run is resumable on the next invocation (status field drives it).

### Step 8 — Tests (the pyramid, v0 slice)
- **Unit:** normalization, fingerprint, score-output parsing, threshold routing, email rendering.
- **Integration:** the handler against **LocalStack/moto** (S3, Secrets) + a local Postgres; Bedrock mocked.
- **Live smoke:** one real end-to-end run against deployed infra.
- **WHY:** reliability + clone-and-run confidence; tests are the negative-case engine for the gate.
- **WAIT-FOR:** all green locally + one clean live smoke run.
- **FAILURE-MODE:** flaky integration → pin LocalStack versions; don't paper over with retries.

### Step 9 — Minimal CI
GitHub Actions: lint (`ruff`) → unit tests → `terraform validate`. Branch protection on `main` (PR-only). Secret scan in pre-commit.
- **WHY:** the release-centric model needs CI from day one; cheap.
- **WAIT-FOR:** a PR shows green required checks.
- **FAILURE-MODE:** CI green but app broken → a check is presence-only; make it behavioral.

### Step 10 — Deploy, first live run, tag
`terraform apply`, populate secrets, trigger the Lambda manually once, confirm the email + DB state, then enable the EventBridge schedule. **Tag `v0.1.0`.**
- **WHY:** v0 is "done" only when it delivers a real scored shortlist on a schedule.
- **WAIT-FOR:** the validation gate below passes (positive + negative).
- **FAILURE-MODE:** scheduled run doesn't fire → check the EventBridge rule + Lambda permission.

---

## v0 Validation Gate (behavioral + negative — a presence check is *no gate*)

| ID | Positive case | Negative case |
|---|---|---|
| **VG1 — Ingestion** | A real source call persists ≥1 normalized posting to Postgres *and* its raw JSON to S3. | Feed a malformed API payload → the contract rejects it and logs; it is **not** silently persisted. |
| **VG2 — Scoring is behavioral** | Score a known JD+profile → score in the expected band, with non-empty `strengths`/`gaps`/`strategic_assessment`. | Score a clearly-misaligned JD → it lands **below** the floor (proves the scorer discriminates, not just returns 200). |
| **VG3 — Determinism** | Same JD+profile scored twice → within ±3 points. | (N/A — covered by VG2's behavioral assertion.) |
| **VG4 — Idempotency** | Two handler runs for the same date → identical DB state, ≤1 email. | Kill the handler mid-run, re-invoke → it resumes; no duplicate rows/emails. |
| **VG5 — Notification** | Daily email arrives with correct matches, scores, and working apply links. | Zero matches above threshold → a valid "no matches today" email (not a crash, not silence). |
| **VG6 — Teardown** | `terraform destroy` removes everything; bill returns to ~$0. | (N/A — destroy is the negative of apply.) |
| **VG7 — Secrets hygiene** | Secret scan passes; no secret in the repo or logs. | Plant a fake key in a staged file → pre-commit/secret-scan **blocks** it. |
| **VG8 — Threshold is config** | Change `threshold` in the per-user config (no code change/redeploy) → the next run surfaces a different set of jobs accordingly. | Set threshold above every score → the run produces a valid "no matches" email; set it to 0 → all scored jobs surface. (Proves the gate reads config at runtime, not a hardcoded constant.) |

All VGs must pass before tagging `v0.1.0`. Any failure → log an `ERR-NNN` in [ledgers/errors.md](ledgers/errors.md) (root cause + prevention + **detection**) before proceeding.

---

## v0 cost estimate (sanity)
Lambda (≈30 invokes/day) ~$0 · S3 (MBs) ~$0 · Secrets Manager ~$1 · Bedrock (≈30 scores/day, Claude) ~$3–8 · SES (≈30 emails/mo) ~$0 · Postgres: **Aurora Serverless v2** ~$40/mo floor *or* **RDS t4g.micro** ~$12/mo. **`terraform destroy` → ~$0** when idle. (The DB dominates idle cost — a real input to D-v0-1.)

## On completion
Tag `v0.1.0`, write its release notes (what it does, how to deploy/destroy, the VG evidence), append the v0 **Produces** row to the contract ledger, set v0 ✅ in the [phase index](ledgers/phase-index.md) — then run the [migration-decision protocol](03-roadmap.md#the-migration-decision-protocol-how-the-next-step-is-actually-chosen): use it, find the top-3 bottlenecks, and design M1 (expected: CV tailoring) just-in-time.
