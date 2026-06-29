# Ledger · Error & Incident Log

> Every error, incident, deviation, or chaos-discovered gap is logged here — **constructed live, not reconstructed later.** Read this log *before* re-attempting any fix (a solution that reintroduces a known-failed approach is a documentation failure, not an engineering one). Paste error messages **verbatim** — the exact tokens are the searchable value. A stage cannot close with an open error.

## Entry template (the Five Questions)
```
### ERR-NNN — <short description>     [Open | Resolved]
- Stage / component · Layer · Type (data | connectivity | config | dependency | logic)
- Discovered: <date>  ·  Resolved: <date>  ·  Source: <implementation | chaos-discovered>
1. What happened?   <symptom as observed; verbatim error string>
2. Why did it happen? <root cause — the exact line/config/assumption that was wrong>
3. How did it happen? <chain of events>
4. How did we fix it? <exact files / commits / commands>
5. How do we prevent recurrence? <a concrete guard: a test / DQ check / CI gate / schema rule>
   + Detection: <what check would have caught this earlier — mandatory>
- Blast radius: <what downstream was affected>
- Prevention implemented? <Yes/No + commit>   (an incident without an implemented prevention is OPEN)
```

## Log
### ERR-001 — Anthropic Bedrock invocation blocked (base model ID + zero daily token quota)   [Mitigated — worked around via ADR-0017]
- Stage: pre-build · Bedrock readiness · Layer: Infra · Type: config / dependency
- Discovered: 2026-06-16 · Source: investigation (account `198592435375`, us-east-1, profile `samareltayeb`)

1. **What happened?** "Can't use Anthropic models." Two distinct failures, observed via `bedrock-runtime converse`:
   - base model id → `ValidationException: Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0 with on-demand throughput isn't supported. Retry your request with the ID or ARN of an inference profile that contains this model.`
   - `us.` inference-profile id → `ThrottlingException: Too many tokens per day, please wait before trying again.`
2. **Why?** (a) Claude 4.x models in us-east-1 are **inference-profile-only** — base ids aren't invokable on-demand. (b) The account's per-day Bedrock token quota is **0 for every model AND `Adjustable = False`** (AWS default is *billions*) → on-demand inference is blocked entirely. **NOT** root/IAM (root reaches Bedrock fine), and **NOT** billing (Tarig confirmed a valid payment method + unused new-account credits).
3. **How?** (a) The call used the base model id. (b) The account is **brand-new**: AWS gates new accounts with a **non-adjustable 0 daily-token quota** on Bedrock until the account matures — independent of billing. (Free credits are *spend*, not a *rate* quota — they can't buy past a 0 cap.)
4. **How fixed?** (a) ✅ use the **`us.anthropic.*` inference-profile id** (e.g. `us.anthropic.claude-sonnet-4-6`). (b) ⏳ the daily quota is **non-adjustable**, so a Service-Quotas increase request doesn't apply — it lifts via **account maturity** (commonly days → ~2 weeks; re-test) and/or an **AWS Support case** asking to raise the new-account Bedrock daily-token quota. **Open until quota > 0.**
5. **Prevention + Detection:** code must *always* use inference-profile ids (boundary/contract check); a **v0 readiness gate** runs a 1-token `converse` against the chosen `us.` profile and **fails loudly** on `ValidationException` (wrong id) or `ThrottlingException`/quota-0 (billing) *before* the pipeline runs.
- **Blast radius:** the entire scoring + CV-tailoring pipeline (no LLM ⇒ no product) until resolved.
- **Prevention implemented?** No — pre-build; tracked here + in [04-v0-build-plan](../04-v0-build-plan.md) prerequisites.
- **Update (Kimi K2 / model-agnostic):** `moonshot.kimi-k2-thinking` and `moonshotai.kimi-k2.5` are **ACTIVE + `ON_DEMAND`** (no inference-profile gotcha), **but** their daily-token quota also reads **`0.0` / non-adjustable** → the new-account wall is **account-wide, not Anthropic-specific.** The model is now **config-driven** ([ADR-0012](../adr/0012-model-agnostic-llm.md)), so whichever model unblocks first is a one-line swap.
- **✅ RESOLVED open item — Kimi API `converse` test run 2026-06-17** (as `jobfetcher-dev`, us-east-1, model `moonshot.kimi-k2-thinking`, `maxTokens:5`): → **`ThrottlingException: Too many tokens per day, please wait before trying again.`** **Conclusion: switching models does NOT bypass the wall** — the 0/non-adjustable daily-token quota throttles Kimi via the **API** exactly like Anthropic. Where Kimi appeared to "work fine" was the **Bedrock console playground** (separate limits), **not** the `bedrock-runtime` API. So the only real unblock is lifting the account-wide daily-token quota (account maturity / **AWS Support case**) — model choice is irrelevant until then. ERR-001 stays **Open** (the quota, not the diagnosis, is the blocker).
- **Model decision (2026-06-21):** **Kimi K2 Thinking (`moonshot.kimi-k2-thinking`) is the chosen model** ([ADR-0012](../adr/0012-model-agnostic-llm.md)) — no longer waiting on Anthropic. This picks the *model*; it does **not** lift this quota — Kimi stays gated here until the daily-token cap lifts. ERR-001 remains the one blocker on the scoring path.
- **Re-test 2026-06-23** (Tarig believed the quota had cleared — a manual *console* check looked fine): 1-token `bedrock-runtime converse` → `moonshot.kimi-k2-thinking` (jobfetcher-dev, us-east-1) → **still `ThrottlingException: Too many tokens per day`**. The account-wide quota has **not** lifted ~1 week on; the console-vs-API gap holds — the **console playground has separate limits and is not proof for the pipeline** (which calls the `bedrock-runtime` API). ERR-001 stays **Open**; re-test periodically.
- **AWS Support case filed 2026-06-23 — case ID `178220019100382`** (live-chat). Asks AWS to raise the new-account Bedrock on-demand per-day token quotas above 0 — notably **`L-E239925C`** "Model invocation max tokens per day for **Kimi K2 Thinking**" (currently `0.0` / non-adjustable; sibling `L-3587C5E5` = Kimi K2.5) — for account `198592435375`, us-east-1. Billing + model access confirmed; root *and* admin-IAM both throttle. **Re-test trigger:** when AWS confirms the bump, re-run the 1-token `converse` against `moonshot.kimi-k2-thinking` — a completion ⇒ Bedrock is usable again (re-point config; see the mitigation below).
- **✅ MITIGATED 2026-06-24 — routed around via DeepSeek ([ADR-0017](../adr/0017-llm-transport-openai-compatible-deepseek.md)).** We stopped waiting on the Bedrock quota and moved the LLM **transport** to the **OpenAI-compatible API** (v0 provider = **DeepSeek API**, which has no new-account gate), behind the model-agnostic `LlmClient` port ([ADR-0012](../adr/0012-model-agnostic-llm.md)). The Bedrock quota is **still 0** — this is *not Resolved* — but it **no longer blocks** us: Bedrock is now one parked, config-swappable backend. AWS case `178220019100382` stays open as **optional** (flip config back to Bedrock if it ever lifts). The whole pipeline (silver dissection → gold → score) is **live-runnable** once the DeepSeek key lands in Secrets Manager (`jobfetcher/deepseek`). **✅ Verified 2026-06-24** — key stored (rotated) + $2 balance; `scripts/deepseek_smoke.py` returned **HTTP 200** from `deepseek-v4-flash` (prompt=11 / completion=5 tokens). The LLM path is **LIVE**; config model id = `deepseek-v4-flash`. *(One detour en route: DeepSeek's "free signup tokens" did not apply — the API returned `402 Insufficient Balance` until a small top-up; the key + integration were valid throughout.)*

### ERR-002 — Docker Hub anonymous pulls return 403 (local Postgres for storage tests)   [Resolved]
- Stage: dev-infra (the dedicated `jobfetcher-db` local Postgres for C-2 storage tests) · Layer: tooling/connectivity · Type: connectivity / dependency
- Discovered: 2026-06-26 · Resolved: 2026-06-26 · Source: implementation (setting up local-Postgres tests)

1. **What happened?** Every `docker pull` from **Docker Hub** returned `403 Forbidden` on the CloudFront blob CDN — even `hello-world` — while pulls from **MCR / GHCR / mirror.gcr.io** succeeded. So the `jobfetcher-db` docker-compose couldn't fetch `postgres:16-alpine`.
2. **Why?** Docker Hub **anonymous-pull blocking** from this network/CDN — the registry refused unauthenticated blob fetches. **NOT** auth (no login configured/needed), **NOT** a corporate proxy, **NOT** disk/space. The "even hello-world fails, but other registries work" signature is the tell.
3. **How?** C-2's storage tests run against a real local Postgres (not LocalStack — [ADR-0018](../adr/0018-persistence-sqlalchemy-data-api-repository.md)); standing up `jobfetcher-db` required the Postgres image from Docker Hub, which hit the 403 wall.
4. **How fixed?** Added a **registry mirror** — `"registry-mirrors": ["https://mirror.gcr.io"]` in Docker Desktop → Settings → Docker Engine; a fresh `postgres:*-alpine` pull then succeeded. The compose image is also overridable via `${JOBFETCHER_DB_IMAGE:-postgres:16-alpine}`.
5. **Prevention + Detection:** the registry mirror persists in Docker config; `docker-compose.yml` pins the image + allows the env override so a blocked registry is a one-line swap. **Detection:** a `docker pull` failing `403` on the blob CDN *while MCR/GHCR/mirror.gcr.io work* = anonymous-pull blocking → use the mirror (don't chase auth/proxy).
- **Blast radius:** local storage tests (C-2 onward) only; **no production impact** (the deployed store is Aurora, not a container).
- **Prevention implemented?** Yes — registry mirror + the `${JOBFETCHER_DB_IMAGE}` override (commit `90ff53d`).

### ERR-003 — GitGuardian flagged a local-dev example password   [Resolved — false positive, hardened anyway]
- Stage: dev-infra / PR hygiene (C-2 storage PR) · Layer: tooling · Type: config (secret-scan false positive)
- Discovered: 2026-06-26 · Resolved: 2026-06-26 · Source: GitGuardian on the PR

1. **What happened?** GitGuardian raised a **Generic Password** alert on a literal local-dev password (`postgres`/`jobfetcher`) committed in `docker-compose.yml` + an integration-test docstring. (The alert id `#18608761` actually traced to a *separate* `tradesense` repo, but the jobfetcher literal tripped the same heuristic.)
2. **Why?** A **hardcoded credential literal** in a committed file matches the generic-password detector — even when it's a throwaway local-dev value, not a real secret. The scanner can't tell "example" from "real."
3. **How?** The `jobfetcher-db` compose + a test docstring carried a bare local-dev password literal.
4. **How fixed?** **Env-interpolated** the value — `${POSTGRES_PASSWORD:-jobfetcher}` in `docker-compose.yml` + the test docstring; the squash-merge kept the bare literal out of `main`.
5. **Prevention + Detection:** example/local credentials are **env-interpolated with a default**, never bare literals; the per-PR secret scan (GitGuardian + the build's own `git diff` scan) is the **detection**. No real credential was ever exposed.
- **Blast radius:** none — a false positive on a non-secret local-dev value.
- **Prevention implemented?** Yes — the env-interpolation pattern (compose + test docstring).

### ERR-004 — Alembic over the Data API crashes on `%`-encoded ARNs (configparser interpolation)   [Resolved]
- Stage: Step 10 deploy (schema creation on live Aurora via `alembic upgrade head` over the Data API) · Layer: persistence / migrations · Type: config (string interpolation)
- Discovered: 2026-06-29 · Resolved: 2026-06-29 · Source: implementation (live v0.1.0 deploy)

1. **What happened?** Running `alembic upgrade head` against live Aurora over the **RDS Data API** crashed inside `migrations/env.py` when it set the connection URL. The aurora-data-api URL embeds the cluster + secret **ARNs**, which are `%`-encoded (`arn:aws:rds:…` → `…%3A…`); Alembic's `config.set_main_option(...)` routes the value through Python's `configparser`, whose **`%`-interpolation** tried to expand `%3A` / `%2F` as interpolation tokens and raised on the malformed reference.
2. **Why?** `configparser` treats `%` as the interpolation sigil; a literal `%` in a value must be **doubled (`%%`)** to survive. The Data-API URL is the first migration URL to *contain* `%`-encoded ARNs, so the bug had never been reachable before.
3. **How?** Local migrations + tests run **psycopg2** against a container Postgres, whose URL is `postgresql://user:pass@host/db` — **no ARNs, no `%`** — so `configparser` never had anything to interpolate. CI's postgres service has the same psycopg2 URL. The Data-API path that carries the `%`-encoded ARNs is **only exercised on a real deploy**, so the crash first appeared at `alembic upgrade head` on live Aurora.
4. **How fixed?** In `migrations/env.py`, **escape `%`→`%%`** in the resolved Data-API URL before handing it to `config.set_main_option(...)`, so configparser stores the literal ARN characters. Migration then ran clean and created the v0 schema on live Aurora.
5. **Prevention + Detection:** the escape is in `env.py` so any future migration over the Data API is safe. **Detection — the real gap:** *no test exercises the Aurora Data-API path* (local = psycopg2, CI = postgres service), so neither the unit pyramid nor CI could ever surface a Data-API-URL-specific bug — **only the live deploy did.** The honest detection lesson is that a **live smoke run against real Aurora** is the gate that catches Data-API-specific bugs; a future **minimal live-Data-API test** (run only with credentials, skipped in CI) would catch this class earlier.
- **Blast radius:** schema creation on deploy — *blocks every deploy* (no schema ⇒ no pipeline) until fixed; no data impact (caught before any run).
- **Prevention implemented?** Yes — the `%`→`%%` escape in `migrations/env.py` (v0.1.0 deploy fix). Detection gap (a live-Data-API test) noted, not yet built.

### ERR-005 — aurora-data-api dialect rejects `cluster_arn` connect-kwarg (wrong param name)   [Resolved]
- Stage: Step 10 deploy (Lambda `resolve_db_url` building the SQLAlchemy URL) · Layer: persistence / connectivity · Type: config (wrong kwarg name)
- Discovered: 2026-06-29 · Resolved: 2026-06-29 · Source: implementation (live v0.1.0 deploy)

1. **What happened?** The pipeline's first DB call on live Aurora raised **`connect() got an unexpected keyword argument 'cluster_arn'`**. `handlers/pipeline.py` `resolve_db_url` built the SQLAlchemy URL with a `cluster_arn=` query parameter; the **`sqlalchemy-aurora-data-api`** dialect maps URL query params **straight through to its `connect()` kwargs**, where the parameter is named **`aurora_cluster_arn`** — so `cluster_arn` arrived as an unknown kwarg and `connect()` rejected it.
2. **Why?** The dialect's documented connect kwarg is `aurora_cluster_arn` (and `secret_arn`), not the shorter `cluster_arn` the code assumed. The query-param→kwarg pass-through means the name must match the dialect's `connect()` signature **exactly**.
3. **How?** Same structural blind spot as ERR-004: the aurora-data-api dialect's `connect()` is **only invoked on the real Data-API path**. Local psycopg2 and CI's postgres service use a *different dialect entirely*, so they never call `aurora_data_api.connect()` and never validate its kwarg names. The wrong name was reachable only on a live deploy — and it would have broken **every** deploy, not an edge case.
4. **How fixed?** Renamed the query param in `resolve_db_url` to **`aurora_cluster_arn`** (matching the dialect's `connect()` kwarg). The pipeline then connected to live Aurora over the Data API and ran end-to-end (`statusCode 200`, fetched 10 → … → email sent).
5. **Prevention + Detection:** the corrected kwarg name is in `resolve_db_url`. **Detection — the same real gap as ERR-004:** *no test exercises the Aurora Data-API path*, so the dialect's actual `connect()` signature is never validated by local tests or CI; only the **live smoke run against real Aurora** caught it. The detection lesson stands: a **live deploy is the gate** for Data-API-dialect bugs, and a future **minimal live-Data-API test** would pin the kwarg contract earlier.
- **Blast radius:** the Lambda's DB connection — *blocks every deploy / every run* (no connection ⇒ no pipeline) until fixed; no data impact (caught on the first live invocation).
- **Prevention implemented?** Yes — the `aurora_cluster_arn` rename in `handlers/pipeline.py` (v0.1.0 deploy fix). Detection gap (a live-Data-API test) noted, not yet built.

| ID | Severity | Stage | Symptom | Status |
|---|---|---|---|---|
| ERR-001 | Critical | pre-build (Bedrock) | base-id ValidationException + new-account daily token quota = 0 (non-adjustable) | **Mitigated** — routed around via DeepSeek / OpenAI-compatible ([ADR-0017]); quota still 0 but no longer blocking; AWS case `178220019100382` open (optional) |
| ERR-002 | Low | dev-infra (local Postgres) | Docker Hub `403` on anonymous pulls (other registries OK) | **Resolved** — registry mirror `mirror.gcr.io` + `${JOBFETCHER_DB_IMAGE}` override |
| ERR-003 | Info | dev-infra (PR hygiene) | GitGuardian generic-password on a local-dev literal | **Resolved** (false positive) — env-interpolated `${POSTGRES_PASSWORD}` |
| ERR-004 | High | Step 10 deploy (Alembic over Data API) | `configparser` `%`-interpolation choked on `%`-encoded ARNs in the Data-API URL | **Resolved** — escape `%`→`%%` in `migrations/env.py`; caught only by the live deploy (no Data-API test) |
| ERR-005 | High | Step 10 deploy (Lambda DB connect) | `connect() got an unexpected keyword argument 'cluster_arn'` (dialect wants `aurora_cluster_arn`) | **Resolved** — renamed kwarg in `handlers/pipeline.py`; would have broken every deploy; caught only by the live run |
