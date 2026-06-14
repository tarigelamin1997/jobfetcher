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
1. **AWS account** in **us-east-1**, with **Bedrock model access** enabled for the chosen Claude model (request in the Bedrock console). · *WAIT-FOR:* model shows "Access granted." · *FAILURE-MODE:* `AccessDeniedException` on invoke → access not granted / wrong region.
2. **One job-source API key** — JSearch (RapidAPI) **or** Adzuna app id+key. (v0 uses exactly one; the second source is M2.)
3. **SES**: verify the sender identity and the recipient (sandbox mode is fine for a single recipient). · *FAILURE-MODE:* `MessageRejected` → identity not verified.
4. **Candidate profile**: real `profile.yml`/`profile.json` kept **locally + gitignored**; a **sanitized sample** committed so the repo is runnable by others.

## Sub-decisions to resolve at build start (flagged, not pre-locked)
- **D-v0-1 — Postgres flavor.** *Recommendation:* **Aurora Serverless v2 + RDS Data API** — no VPC/NAT for the Lambda (HTTP data access), fewer moving parts ⇒ more reliable, scales toward $0 idle. *Alternative:* **RDS `db.t4g.micro`** (cheaper sticker price, but Lambda-in-VPC + VPC endpoints to avoid a NAT gateway). Decide on the simplicity-vs-cost tradeoff at build start; record an ADR.
- **D-v0-2 — Which single source.** Pick JSearch *or* Adzuna for v0 based on which gives better KSA/GCC DE coverage in a quick manual probe. Record why.

---

## Apply sequence

### Step 1 — Scaffold the project + resolve sub-decisions
Create the Python package layout (ports-&-adapters from day one, so M1+ stay clean), the Terraform skeleton, and resolve D-v0-1/D-v0-2 with a short ADR each.
- Suggested layout: `src/jobfetcher/{adapters/,core/,handlers/}`, `src/jobfetcher/core/{models.py,scoring.py,fingerprint.py}`, `tests/`, `terraform/`, `migrations/` (Alembic), `config/` (sample), `pyproject.toml`.
- **WHY:** boundaries + flags now = cheap migrations later (a foundational migratability requirement).
- **WAIT-FOR:** `pip install -e .` succeeds; `ruff`/`pytest` run (no tests yet).
- **FAILURE-MODE:** import path tangles → fix the package layout before writing logic, not after.

### Step 2 — Data contract + v0 schema
Define **Pydantic** models for the normalized posting + the Bedrock score output (the data-contract boundary). Write the **Alembic** migration for the v0 tables: `posting`, `cluster` (trivial 1:1 in v0), `score`, `profile`.
- **WHY:** contracts at the boundary prevent "assumed-from-inspection" bugs; Alembic from day one makes schema evolution first-class.
- **WAIT-FOR:** `alembic upgrade head` creates the tables; a round-trip insert/select of a sample posting works.
- **FAILURE-MODE:** validation errors on real API payloads → tighten the adapter's normalization, not the contract.

### Step 3 — Provision infrastructure (Terraform)
Modules/resources: S3 bucket (`force_destroy = true`), Secrets Manager entries (empty placeholders), the chosen Postgres, the Lambda + IAM role (least-privilege: only its S3 prefix, its secrets, Bedrock invoke, SES send, DB access), EventBridge daily rule, SES identity. Populate secrets once via CLI (never committed).
- **WHY:** reproducibility is the portfolio value; least-privilege is the security signal.
- **WAIT-FOR:** `terraform apply` clean; `terraform destroy` proven to return to $0 on a throwaway run.
- **FAILURE-MODE:** IAM `AccessDenied` at runtime → the role is missing a specific permission; add exactly that one, not `*`.

### Step 4 — Source adapter (one source)
Implement the adapter: call the source API (pagination + basic rate-limit/backoff), normalize each result through the Pydantic contract, write raw JSON to S3 `raw/{source}/{date}/`, upsert `posting` rows (exact-id dedup on re-fetch). Thread the **correlation `run_id`**.
- **WHY:** the adapter is the seam that makes M2 (multi-source) a one-file add.
- **WAIT-FOR:** a real call returns ≥1 normalized posting persisted to Postgres + S3.
- **FAILURE-MODE:** API shape drift → the contract catches it loudly; map the new field in the adapter.

### Step 5 — Scorer (Bedrock)
Implement scoring: load profile, build the 7-factor ATS system prompt, call Bedrock (**temperature 0**) with structured-output enforcement, parse into the score contract (`score`, `fit_category`, `strengths`, `gaps`, `strategic_assessment`, `skills_extracted`, `sector`, `poster_type`, `legitimacy_verified`), write `score` rows. Apply thresholds **75 / 55 / 10**.
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

All VGs must pass before tagging `v0.1.0`. Any failure → log an `ERR-NNN` in [ledgers/errors.md](ledgers/errors.md) (root cause + prevention + **detection**) before proceeding.

---

## v0 cost estimate (sanity)
Lambda (≈30 invokes/day) ~$0 · S3 (MBs) ~$0 · Secrets Manager ~$1 · Bedrock (≈30 scores/day, Claude) ~$3–8 · SES (≈30 emails/mo) ~$0 · Postgres: **Aurora Serverless v2** ~$40/mo floor *or* **RDS t4g.micro** ~$12/mo. **`terraform destroy` → ~$0** when idle. (The DB dominates idle cost — a real input to D-v0-1.)

## On completion
Tag `v0.1.0`, write its release notes (what it does, how to deploy/destroy, the VG evidence), append the v0 **Produces** row to the contract ledger, set v0 ✅ in the [phase index](ledgers/phase-index.md) — then run the [migration-decision protocol](03-roadmap.md#the-migration-decision-protocol-how-the-next-step-is-actually-chosen): use it, find the top-3 bottlenecks, and design M1 (expected: CV tailoring) just-in-time.
