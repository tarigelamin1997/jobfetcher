# Changelog

All notable changes to JobFetcher are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); the project ships **semantic-versioned releases** ([roadmap](docs/03-roadmap.md), [ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)): a **minor** bump (`v0.x.0`) per migration / real capability, a **patch** bump (`v0.x.y`) for small fixes + improvements between migrations, and `v1.0.0` at M8. **v0.1.0 shipped 2026-06-29.**

The ***why*** behind every entry is the [session decision journal](docs/01-session-decision-journal.md); formal decisions are [ADRs](docs/adr/); the raw reasoning trail is the [working document](docs/session-log/working-document.md).

## [Unreleased]

### Added — digest truthfulness (rides v0.8.0 — "the digest tells the truth")

- **[ADR-0027] New/still-open split — the daily digest stops re-presenting old matches as news.** Before: `get_scored_shortlist` was deliberately global (its own SCOPE CAVEAT), so **every above-threshold job ever scored re-appeared in every digest** — newest buried, stale never expiring — and reassess graduations existed only in the invoke-response JSON, never in any email. Now **"New since last digest"** leads with full cards — NEW iff `since is None` (the first-ever digest) or `scored_at is None` (defensive) or a **fresh judgment that is actually news** (`scored_at > since` AND: a first scoring, or a graduation `previous_score < threshold <= score`) — and everything else above threshold compacts into **"still open"** (a count line + the top-5 one-liners + "…and n more — see your export", [querying.md](docs/querying.md)). A **graduation gets a green `↑ old→new` badge** (ADR-0023's graduations finally reach the email); a zero-new day honestly says **"no new matches since {date}"** and still sends (VG5 spirit). The split/grouping are pure render-side functions (`split_new_and_still_open`/`collapse_duplicates`), **no migration** — `since` = the new `Repository.get_last_digest_sent_at` (`MAX(run_log.digest_sent_at)`, NULL-safe), resolved once by `notify()`.
- **Render-time duplicate collapse** — within-JSearch duplicates measured at **17.8% of scored postings** (32/180 in 14 fingerprint groups; worst: "Data Platform Engineer @ Blackstone EIT" scored **5×**, 35–88, each dup paying the pro model). Same-fingerprint groups now collapse to **one card** (representative = highest score; footnote `seen n× — scores lo–hi`; None/empty fingerprints never merge), each group renders in exactly **one** section (NEW iff any member is new — a straddling group never renders twice), and the subject counts collapsed groups. **Cluster-attach dedup — stopping the duplicate scoring *spend* at the source — is explicitly deferred to M2** (Tarig's scope call: it belongs to the multi-source clustering redesign, ADR-0005).
- **Digest age bound** — new **REQUIRED** `SearchSpec` knob **`digest_max_age_days`** (0–365; 0 = keep forever; sample recommends **90**): still-open matches older than N days drop out of the digest entirely — aged by `COALESCE(posting.fetched_at, bronze_posting.fetched_at)` (the ADR-0025 reassess pattern), unknown-age rows always kept, bound-param cutoff. **Documented semantics change:** the "+N below threshold" count is now scoped to the same age window. `get_scored_shortlist` gains `since`/`max_age_days` (+ `previous_score`/`fingerprint`/`scored_at`/effective fetched-at on `ShortlistItem`, all on the Protocol); the handler threads the knob; `scripts/preview_digest.py` shows all three new states.
- **Process win (recorded honestly):** the **first auto-pilot unit** under the severity-gated policy adopted 2026-07-07 (Claude merges on a clean Examiner pass + green CI). The original spec's `previous_score`-only newness would have re-presented every old job as "New" every day in daily-only operation (jobs are scored exactly once, so `previous_score` stays NULL) — the independent fresh-context Examiner **caught it** (a contested finding, adjudicated fix-now, fixed in `0eca0f7`) and the re-verify came back **CLEAN PASS**.
- Verified: **311 unit + 39 integration green** (+5 live-key skips; full suite **350 passed, 95.02% coverage**, 85% floor), `ruff` clean.

### ⚠️ Deploy sequencing (v0.8.0) — push config before the next invoke

**`digest_max_age_days` is REQUIRED ([ADR-0027]) — the same class of breaking config change as v0.7.0's `reassess_max_age_days` below:** the deployed S3 config must be re-pushed before the next invoke after this deploys, or **every run fails loudly** (`SearchSpec` ValidationError). The order is: **update the local config YML → `python scripts/push_config.py` → deploy/invoke.** (Registered in the [procedure registry](docs/ledgers/procedure-registry.md).)

### Added — score history + lineage (rides v0.7.0)

- **[ADR-0025] Append-only `score_event` log — re-scores never erase judgments again.** Before: `save_score` upserted the `score` row in place, so every re-score destroyed the prior strengths/gaps/assessment/`scored_at` keeping only one `previous_score` generation — and DeepSeek scores are non-reproducible even at temp 0, so overwritten history was *irrecoverable* (measured: after the single 2026-07-06 reassess, **100% of the 180 scored postings** had the original narrative already gone and the original score one invoke from permanent loss). Now migration **`0004_score_event_lineage`** adds an immutable `score_event` table (score/fit + `strengths`/`gaps`/assessment + **lineage: `scoring_model` · `profile_hash` · `run_id`**, `scored_at` default `now()`, indexed on cluster/run) and `save_score` **dual-writes** the (unchanged) `score` upsert + the event INSERT in **one transaction** — either failure rolls back both. A **baseline backfill** rescues the 180 pre-0004 scores into the log (`scoring_model`/`profile_hash` = `'pre-0004'`). The event INSERT is a plain no-RETURNING/no-prefetch statement (Data-API hardening per ERR-004/005); the handler stamps every event with a **`profile_hash`** (sha256 of profile + the 3 knobs, also stored on the new nullable `profile.profile_hash`), so any score joins to the exact profile that produced it. M7 calibration + the "45→62→78" history charts (ADR-0023/0024 follow-ons) now have their data source.
- **Reassess age bound** — `get_scored_for_reassess(max_age_days=...)` ages postings by `COALESCE(posting.fetched_at, bronze_posting.fetched_at)` (bronze is the effective live source), **includes** unknown-age rows (safe default), `0`/`None` = unbounded (query string-identical to before); new **REQUIRED** `SearchSpec` field **`reassess_max_age_days`** (0–365; 0 = no cutoff; sample recommends **45**) — reassess stops paying LLM tokens to re-score months-old, likely-filled postings forever. The method is now declared on the `Repository` Protocol (closing a v0.4.0 omission).
- **`scripts/export.py`** — the SQLite snapshot gains a **`score_events`** table (the score-delta/provenance index next to the flat `jobs` current view).
- Honest asymmetry (recorded in the ADR): an event's `previous_score` is what *that* `save_score` call received — a fresh scoring writes event `previous_score=NULL` even when the score-row upsert carries an old value; deltas are recoverable from **event order** regardless.
- Verified: **272 unit + 31 integration green** (+5 live-key skips), **94.76% full-suite coverage** (85% floor), `ruff` clean; independent fresh-context adversarial + integration review — zero blocking defects. Residual live item: watch the **first `save_score` over the Aurora Data API** in the release's live smoke (the inline INSERT is compile-verified; the Data-API path is only provable live).

### Added — outcome tracking + human-override lineage (rides v0.7.0)

- **[ADR-0026] Append-only `application_event` outcome log + `scripts/track.py` — the pipeline finally records what happens *after* the digest.** Before: `posting.status` dead-ended at `'scored'` — "Applied / Interview / Offer / Rejected" lived nowhere, and unlike bronze (replayable), outcome data is *unreplayable*: every applied-to job not recorded was **calibration + funnel data lost forever**. Now migration **`0005_application_event`** (chains to 0004) adds an immutable outcome log (`posting_id` FK · `status` CHECK-limited to `applied`/`interview`/`offer`/`rejected`/`withdrawn` · `noted_at` · optional `note`; **no backfill** — no prior outcome data exists); re-recording is a **new event, latest wins** (append-only, deliberately no `UNIQUE`). The vocabulary lives **once** (`APPLICATION_STATUSES`, `core/models.py`) — the table CHECK is built from it, the repository re-validates it, the CLI subcommands are generated from it, and a unit test pins the migration's frozen literals to it. Usage: `python scripts/track.py applied|interview|offer|rejected|withdrawn <posting_id> [--note]` + `find` (copy-pasteable posting_id lookup) + `events` (the outcome trail); failures are loud (stderr + exit 1, zero rows).
- **Human score overrides land in the lineage log** — `score.score_override` (in the schema since v0 for M7 calibration) was wired to nothing; now `track.py override <posting_id> <score>` calls `Repository.set_score_override`: **ONE transaction** that UPDATEs `score.score_override` (rowcount-checked) **and APPENDs a `score_event` with `scoring_model='human-override'`** — human corrections join the same append-only history as LLM scorings; a second override moves the column but **both events survive**. Two semantics to know: an override does **not** change `score.fit_category` (the override's derived category lives on its event — the current view keeps the LLM's category next to your number), and `save_score`'s upsert never clears `score_override` — **an override survives later re-scores/reassess**.
- **`scripts/export.py`** — the flat `jobs` table gains **`score_override` · `latest_application_status` · `application_noted_at`** (latest event per posting; event-less postings stay NULL) and the SQLite snapshot gains an **`application_events`** table; the CSV stays the flat jobs table (now with the new columns).
- Verified: **283 unit + 36 integration green** (+5 live-key skips; full suite **319 passed, 94.74% coverage**, 85% floor), `ruff` clean; independent fresh-context adversarial Examiner: **clean pass, zero blocking** (its S-1 drift-pin finding fixed in `a6dd24a`). Residual live item: `set_score_override` is the codebase's **first `.rowcount` reliance** — a new Aurora Data API dialect surface (ERR-004/005 lesson) — so one `track.py override` joins the release's live smoke.

### ⚠️ Deploy sequencing — read before deploying the above

**`reassess_max_age_days` is REQUIRED and the runtime config lives in S3 ([ADR-0022]) — deploying this code without pushing config first makes every subsequent run fail loudly (`SearchSpec` ValidationError) until the config is pushed.** The deploy order is: **update the local config YML → `python scripts/push_config.py` → deploy/invoke.** (Registered in the [procedure registry](docs/ledgers/procedure-registry.md).)

**Migrate before first use of the outcome tools ([ADR-0026]):** the new export SQL and `scripts/track.py` reference `application_event` unconditionally — against a DB not migrated to **0005** they fail with `UndefinedTable` (loud, harmless, zero rows). Run **`alembic upgrade head`** before the first `track.py` / new-export use. (Registered in the [procedure registry](docs/ledgers/procedure-registry.md).)

### Milestones

- **Milestone `milestone/pre-agentic-workflow-2026-07-07`** — the complete-documentation baseline (v0.1.0→v0.6.0 shipped + docs/diagrams swept current, 257 tests green). The clean checkpoint before the build phase switches to an **agentic workflow**.

*Next candidate (P2): the "fold graduations into the email" candidate shipped as part of digest truthfulness ([ADR-0027], rides v0.8.0) — re-run the bottleneck protocol from real use once v0.7.0/v0.8.0 tag.*

## [v0.6.0] — 2026-07-06 — email UX: a scannable digest with prominent apply-links

### Changed
- **Redesigned the daily digest** (`core/notifier.py` `render_digest`) — Tarig's feedback was "poor format, apply-links buried." The old dense 5-column table (with a tiny `Apply` text in the last column) is now **one clean card per job**: a colored **score badge** · bold **title** · **Company · Location** · a **why** line + an honest **gap** · and a **prominent, button-styled `Apply →`** (one obvious call-to-action per card). Email-client-safe (table layout + inline styles only, no external CSS/images/JS); escaping + the `javascript:`/`data:` scheme allowlist preserved; the plaintext fallback puts the full apply URL on its own line per job; the zero-matches email stays first-class.

### Added
- **Location on the digest** — `ShortlistItem` + `get_scored_shortlist` now carry `city`/`country`, so each card shows *where* the job is.
- **`scripts/preview_digest.py`** — render the digest with sample data to `export/digest_preview.html` (open in a browser) so the email design is reviewable without sending one.
- Tests: every surfaced job renders a prominent Apply **button** (a styled `<a href>`, not buried text — the "links are visible" gate) + badge/location/gap assertions, a missing-link "no link" state, and the existing security/zero-match negatives. 257 unit green.

## [v0.5.0] — 2026-07-06 — query / filter access (export → open in a generic tool)

### Added
- **[ADR-0024] `scripts/export.py`** — snapshot the operational DB to a portable **SQLite + CSV** (gitignored `export/`) you filter/search/sort/organize in a purpose-built tool (**Datasette** recommended — faceted filters + full-text search; or DB Browser / Excel / raw `sqlite3`) — no custom UI. The star is a flat **`jobs`** table (one filterable row per posting: role · geo · skills-as-text · status · `score`/`previous_score`/`fit_category` · apply_url · dates), plus `bronze` (full fetch history), `runs`, and `profile_current`. It also **prints a summary** (totals · fit-category counts · graduations · top-5). Datasette is an optional `[query]` extra (no runtime dep); SQLite is stdlib. Docs: [querying.md](docs/querying.md).

## [v0.4.0] — 2026-07-06 — reassess / replay (re-score on an updated profile, no re-fetch)

### Added
- **[ADR-0023] A `{"mode":"reassess"}` handler mode** — re-scores your already-scored postings against the **current** profile with **zero JSearch calls** (the medallion's immutable-bronze → replay). When your profile improves (a new skill), a job that was a `stretch` can **graduate** to `strong_fit`. `save_score` already carried the old score into `previous_score`; this wires the replay that uses it. Flow: edit `profile.local.yml` → `push_config.py` → invoke `{"mode":"reassess"}`. New `Repository.get_scored_for_reassess()` + `core.ingest.reassess()` (same concurrency/deadline/retry as scoring) + a pure `resolve_mode`. Reports `{reassessed, graduated, downgraded, unchanged, …}` + a `graduations` list. Realizes the graduation half of the old M4, re-derived from real use (P2). *(The "what graduated" email rides the email-UX unit; a query/filter surface is the next capability.)*

## [v0.3.1] — 2026-07-03 — employment_types fix (first patch release)

### Fixed
- **`employment_types` was a silent no-op with no validation** — it was typed `list[str]` (any string accepted) **and** never actually passed to the JSearch query. Now it's an **`EmploymentType` enum** (`FULLTIME`/`PARTTIME`/`CONTRACTOR`/`INTERN`) — a typo fails **loudly at config-load** (like `date_posted`/`remote`) — **and** it's wired into the `/search` request, so setting it actually narrows results. `[]` still means no filter.

## [v0.3.0] — 2026-07-03 — user-customizable settings (no redeploy)

**Toward "fully customizable per user."** The job-seeker settings became genuinely user-owned + editable without a rebuild — a settings change is now one command, not a deploy.

### Added
- **The 3 shortlist-strictness knobs are now user config** — `threshold` + `hard_floor` + `near_miss_band` are all fields on the `SearchSpec` (before, only `threshold` was; floor/band were code constants), validated `hard_floor <= threshold`. ([#18])
- **[ADR-0022] Runtime config in S3** — the two config YAMLs moved out of the Lambda zip; the handler reads them from **S3 at runtime**. **Change a setting = edit the YAML + `python scripts/push_config.py`** (validates then uploads) → the next run uses it, **no rebuild/redeploy**. `SearchSpec`/`Profile` gain `from_yaml_text`; new `adapters/s3_config.py` (`S3ConfigStore` + `read_config_text` s3://-or-local dispatch); Terraform seeds the config to S3 with `ignore_changes` (never clobbers a runtime edit); the build no longer bundles config. ([#19])

### Fixed
- **The write-once trap** — the handler seeded the `profile` row only on the first run, freezing the entire profile + knobs; it now **re-syncs from the config every run**, so editing a config file actually takes effect. ([#18])

### Validation (live, 2026-07-03)
- **Filter-only change:** threshold **60 → 95 → 60** via `push_config.py` only — the DB `profile` row re-synced each time and the shortlist tracked it (**21 → 1 → 27** jobs), two digests delivered. **Zero redeploy.**
- **Fetch-driving change:** `countries` GCC → **Egypt** → **8 real Egyptian Data-Engineer jobs fetched** (a country never in the DB before), proving `job_titles`/`countries` drive the live JSearch query from S3, not just a re-filter. **Zero redeploy.**

## [v0.2.0] — 2026-07-02 — M1: pipeline hardening

**The bottleneck-driven first migration.** The P2 protocol ran the tool live on the full 18-query GCC sweep and measured three real bottlenecks (overruling the pre-drawn *M1 = CV tailoring* hypothesis): serial throughput that timed out, a single provider `503` that killed a whole run, and a gold filter loose enough to pay the pro-model to reject obvious junk — plus AWS blind-retrying the dead run. All fixed and **re-validated live on the exact workload that failed** ([ADR-0021](docs/adr/0021-m1-pipeline-hardening.md)).

### Changed
- **Throughput (H-2):** silver dissection + scoring now run their LLM calls on a bounded **`ThreadPoolExecutor`** (default 8, `$PIPELINE_MAX_WORKERS`); **all DB writes stay on the main thread**. A **deadline guard** (`context.get_remaining_time_in_millis()` − 60s) stops starting new work before the timeout, returns `partial: true` with a `deferred` count, and skips notify so the idempotent re-run sends the digest. **Measured ~13× faster (~1.1→~14–15 dissections/min); a run can no longer time out.**
- **Precision (H-3):** the deterministic gold filter now requires **all** of a target title's tokens in the posting title ("Data Architect" → `data`+`architect`), not any single shared token; the built `LlmFilterStrategy` is now selectable via `$GOLD_FILTER_STRATEGY`. **The six live junk titles (Alliances Manager, Computer Vision Engineer, …) are eliminated.**
- **Lambda infra:** `memory_size` 512→**1024 MB** (CPU scales with memory for the worker threads); **`aws_lambda_function_event_invoke_config { maximum_retry_attempts = 0 }`** codifies the fix for the async zombie-retry.

### Added
- **Retry + jitter (H-1):** `OpenAICompatLlmClient.complete()` retries only transient failures (429/5xx + connection/timeout) with exponential backoff + full jitter (`LlmConfig.max_retries`, `backoff_base_s`); auth/model-not-found fail fast. `land_silver` now isolates `LlmError` symmetrically with `score_gold` — one blip skips one posting, never the run.
- **[ADR-0021]** (M1 pipeline hardening, with the measured before/after table + the honest M3 caveat); **ERR-006** (503 no-retry crash) + **ERR-007** (async auto-retry re-fetch) in the error log; new unit tests (retry policy, failure isolation, concurrency wall-clock, deadline deferral, subset-title, strategy resolution) — **212 unit + integration green**.

### Live validation (2026-07-02)
- Re-ran the ~132-posting backlog the pre-fix code died on: `statusCode 200`, backlog fully dissected + scored, **0 run-fatal errors** (15 dissect + 0 score failures isolated), and a **populated 21-job digest sent** — real GCC Data-Engineer roles scored 60–95 across all six countries. (Bonus finding: the market is *not* thin; the earlier "no matches" was the tiny Oman/Architect sample.)

## [v0.1.1] — 2026-06-29 — documentation refresh

An all-round documentation update over **[v0.1.0]** reflecting the deployed reality — **no pipeline change** (v0.1.0's code, including the two Data-API deploy fixes, stands).

### Changed
- **README** rewritten for the deployed + live-validated v0.1.0 (as-built flow, tech-stack table, how-to-deploy/run + the local test pyramid, the live-validation proof, the evolutionary roadmap).
- **`docs/02-architecture.md`** — the as-built deployed v0 (14-resource stack, RDS Data API, deterministic gold-filter default, the `run_log` send-once guard, the scale finding); **`docs/03-roadmap.md`** — v0 shipped, **M3 now evidence-backed** (the single-Lambda full-backfill limit), M1 = a hypothesis re-derived via the bottleneck protocol.
- **ADRs** — status touches on 0008 / 0014 / 0018 (validated live v0.1.0) + **NEW [ADR-0020]** (Lambda deployment packaging — Linux wheels via `pip --platform`, no Docker, boto3 pruned), indexed.
- **Ledgers** — interface-contracts → **shipped**; decisions-locked + the Aurora Data-API connect-param live-only contract + ADR-0020 + a v0.1.0-deployed row; procedure-registry + the Lambda-packaging/deploy procedure; build-plan Step 10 → the *actual* deploy (build-lambda · apply/migrate/invoke/validate/destroy · the 2 bugs · the tag).
- **Diagrams** — `docs/diagrams.md` reflects the as-built + shipped v0; a fresh **Eraser** v0.1.0 architecture diagram (personal/portfolio view, not committed).

## [v0.1.0] — 2026-06-29 — v0 SHIPPED

**The minimal working core is built, deployed, and live-validated end-to-end** — then torn down to ~$0. EventBridge → one Lambda: **JSearch fetch → S3 + Postgres (Aurora Serverless v2, RDS Data API) → DeepSeek dissect + 7-factor ATS score → SES daily digest**, with Terraform infra, Secrets Manager, the v0 test pyramid, and minimal CI. This is the whole v0 arc: **Steps 0–10** (probe · silver `Dissector` · schema + `Repository` · Terraform infra · fetch/silver landing · gold filter + `Profile` · Scorer · Notifier · single-Lambda handler · test round-up · CI · deploy + live run).

### 🚀 Shipped — the live validation (2026-06-29, the Step-10 deploy)

- **`terraform apply` → the 14-resource stack** (Aurora SLv2 + Data API, S3, Lambda, EventBridge, SES sender, least-privilege IAM); SES sender + recipient verified; schema created on Aurora via **`alembic upgrade head` over the Data API**.
- **The full pipeline ran end-to-end on real AWS:** invoke → `statusCode 200` → **fetched 10 → bronzed 10 → silvered 8 → gold 8 → scored 8 → notify sent**. Real UAE Data-Engineer postings scored (GSSTech, Contango, Michael Page, …). **Two emails delivered, SES 0 bounces:** a no-matches digest (threshold 60, all 8 below — **VG5 zero-path**) and, on an **idempotent re-run** (`already: 8` skipped — **VG4 live**), a populated 7-job shortlist (threshold lowered to 20 — **VG5 matches-path**). The `run_log` send-once guard recorded both runs.
- **`terraform destroy` → 14 destroyed**, independently verified GONE (Aurora / Lambda / S3); the two Secrets Manager keys preserved (reusable). Back to ~$0.

### Fixed — 2 deploy-only bugs the live run caught (PR #13)

Invisible to local psycopg2 tests + CI's `postgres` service — neither exercises the Aurora **RDS Data API** path:

- **`migrations/env.py`** — configparser choked on the `%`-encoded ARNs; escape `%` → `%%`.
- **`handlers/pipeline.py`** — the Data-API dialect's connect kwarg is **`aurora_cluster_arn`**, not `cluster_arn`; would break **every** deploy.
- **Lambda timeout 300 → 900s** — each posting ≈30s (an LLM call + a Data-API write).

### 📈 Scale finding (a genuine P2 bottleneck → reinforces M3)

The single Lambda **can't** run the **full** 18-query × 30-day backfill inside the 15-min max (throughput ≈30s/posting × hundreds of jobs); the daily **incremental** run (~10–30 new jobs) fits comfortably. The smoke run used a minimal config (1 country, 3 days). This is a real future migration signal → **M3 (Step Functions)**.

### 🏆 Milestones

- **2026-06-27 — v0 DATA PATH CODE-COMPLETE.** The full v0 pipeline now exists as code — **fetch → bronze → silver (LLM dissect) → gold (filter) → score → notify**. On `main`: Step 0 probe, C-1 silver `Dissector`, C-2 schema + `Repository`, C-3 Terraform infra, Step 4 fetch + bronze→silver, Step 4b gold filter + `Profile`, Step 5 Scorer. Step 6 (SES Notifier) is built and in **PR #8** (not yet merged). Remaining: Step 7 (single-Lambda wiring) · Step 8 (test round-up) · Step 9 (minimal CI) · Step 10 (deploy + first live run → tag `v0.1.0`). **153 unit + ~22 integration tests green; `ruff` clean.**
- **2026-06-26 — v0 DESIGN COMPLETE.** The v0 implementation plan, **19 ADRs**, and the storage-layer design are fully documented; the M1–M8 foundations are laid (directional, a living hypothesis). The first build unit is **live** (C-1 silver `Dissector`). Tagged `milestone/v0-design-complete-2026-06-26`. Next: the **agentic per-unit build** ([ADR-0019](docs/adr/0019-agentic-build-orchestration.md)), starting with C-2 (schema + `Repository`). `v0.1.0` is reserved for when the pipeline ships end-to-end.
- **2026-06-24 — THE LLM IS LIVE.** After weeks blocked by a Bedrock new-account daily-token quota of **0** ([ERR-001](docs/ledgers/errors.md)), the scoring/dissection path is unblocked. We stopped waiting on AWS and **routed around it** — the LLM transport now runs on the **OpenAI-compatible API with DeepSeek** (`deepseek-v4-flash`), verified end-to-end (`scripts/deepseek_smoke.py` → HTTP 200). The whole pipeline (bronze → silver dissection → gold → score) is now **live-runnable**, not blocked work. Full arc + reasoning: [journal §18](docs/01-session-decision-journal.md). ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md))

### Added

- **Step 10 — deploy + first live run (this release):** the 14-resource Terraform stack applied to real AWS, schema migrated over the Data API, the full pipeline run + validated end-to-end (see the live-validation block above), then destroyed to ~$0. Tagged **`v0.1.0`**.
- **Step 9 — minimal CI:** GitHub Actions on PR→main + push→main — 3 jobs: `ruff` + `alembic upgrade head` + `pytest --cov --cov-fail-under=85` (against `postgres:16-alpine`; live DeepSeek/JSearch tests skip without keys) · `terraform validate` (AWS-free) · **gitleaks** secret-scan (**VG7** — pre-commit + CI, blocks a planted fake key). Green CI → `main`'s required status checks.
- **Step 8 — test round-up:** the v0 pyramid complete + green — **180 unit + ~26 integration + ~3 live**, `ruff` clean, **89% full-suite coverage**; closed the two CI-invisible gaps (a **VG3 offline negative** — temp-≠-0 caught without a key — and **`SearchSpec` contract negatives**); added [`tests/README.md`](tests/README.md) (the VG1–VG8 → test traceability map) + `pytest-cov` tooling.
- **Step 7 — single Lambda `handler`:** `handlers/pipeline.py` wires `ingest → apply_gold_filter → score_gold → notify` behind one entry point — seeds the `profile` row, threads the correlation `run_id`, env-resolves the DB engine (local URL vs Aurora Data API) + config paths, `{statusCode, run_id, …counts}` summary, a stage failure → `500` → next run resumes. **VG4 idempotent** via upserts + a **`run_log` send-once guard** (migration 0003, PK `(run_date, user_id)`); the email is **at-least-once** (the SES↔`run_log` dual-write can't be atomic).
- **Step 6 — Notifier (SES daily digest):** HTML + plaintext digest of the day's scored matches; a zero-matches "no matches today" email (VG5). Apply-URL `href` is **scheme-allowlisted to http/https** as an email-injection guard, verified un-bypassable (the fresh verifier's should-fix).
- **Step 5 — Scorer (7-factor ATS):** explainable scoring at temp 0 on `deepseek-v4-pro`; `UNIQUE(score.cluster_id)` enforced (migration 0002); VG8 (threshold-is-config) proven.
- **Step 4b — gold filter + `Profile` contract:** v0 default is a **deterministic** `FilterStrategy` (an LLM gold-filter is redundant with the Scorer at 10–30 jobs/day — built and config-selectable, but off by default); **1:1 clusters** (`cluster_id = posting_id`) close the score-keys-on-`cluster_id` gap until real clustering arrives (M2). New `Profile` contract + sanitized sample.
- **Step 4 — fetch + bronze→silver landing:** the source fetch and the bronze→silver landing path, wired through to the `Dissector` and `Repository`.
- **C-3 — Terraform infra:** Aurora SLv2 (scale-to-0) + RDS Data API, S3, least-privilege IAM, SES, EventBridge — **apply-validated end-to-end, then destroyed to ~$0**.
- **C-2 — schema + `Repository`:** the v0 schema (Alembic migrations) behind the `Repository` port, on SQLAlchemy Core + the `aurora-data-api` dialect; **live-validated on real Postgres** (5/5 round-trip).
- **C-1 — silver `Dissector` (first application code, LIVE):** `OpenAICompatLlmClient` (the `LlmClient` port) + a grounded, evidence-required JD dissection (`DissectedPosting` contract + `grounding_check`) — proven on real probe JDs (the thin JD invented **0** tools; the detailed JD returned 17 grounded skills). `src/jobfetcher/`.
- **[ADR-0019] (ran for real, amended)** — agentic build orchestration: each unit runs the gate trio as a per-unit pipeline, fanned out across *independent* units; **executed across C-2…Step 6**. The pipeline gained an **independent adversarial verifier (fresh context, not pre-framed)** — distinct from the in-build Reviewer, added mid-phase on Tarig's shared-blind-spot insight (an orchestrator-spun reviewer shares the builder's framing). It **validated**: on Step 4 the in-build Reviewer reported 0 must-fixes but the fresh verifier found **3 crash blockers**; on gold/scorer/notifier it confirmed clean (and caught the notifier's apply-URL allowlist should-fix). Three independent eyes = verifier + CodeRabbit + human.
- **[ADR-0018]** — persistence access: SQLAlchemy Core + the `aurora-data-api` dialect behind a `Repository` port; local Postgres for DB tests (LocalStack can't mock the Data API).
- **Storage layer designed** (plan §31): Aurora SLv2 **scale-to-0**; dissected output = **JSONB + scalar columns on `posting`** (the `dim_skill`/`fct_job_skill` bridge stays at M5); `score` reconciled (`skills`/`sector`/`seniority` are silver-derived, not re-extracted).
- **[ADR-0017]** — LLM transport = OpenAI-compatible API; v0 provider = **DeepSeek**; Bedrock parked. The model-agnostic port now swaps *provider*, not just model, via config (`base_url`/`api_key`/`model`) — one `OpenAICompatLlmClient` serves DeepSeek, Ollama, Anthropic-direct, or Bedrock.
- `scripts/deepseek_smoke.py` — key-free smoke test that proves the DeepSeek unblock.
- DeepSeek API key in **Secrets Manager** (`jobfetcher/deepseek`).
- **[ADR-0016]** — LLM dissection at the silver layer on *every* posting → populates the market-wide dimensional model.
- **[ADR-0015]** — type-replaceability as a first-class tenet (P3): every stage a config-selected strategy behind a port (`SourceAdapter`/`Dissector`/`FilterStrategy`/`Embedder`/`Scorer`).
- **[ADR-0014]** — operational store = Aurora Serverless v2 + RDS Data API, Lambda outside any VPC (resolves D-v0-1).
- **[ADR-0013]** — enforcement gate-trio (`/start-step` · `/review-step` · `/close-step`) + branch/PR workflow.
- First code: validated `SearchSpec` + JSearch coverage probe (`scripts/`).
- Full context-survival docs: journal Part 2 (build-phase reasoning) + the verbatim working-document archive.

### Changed

- **LLM transport: Bedrock Converse → OpenAI-compatible API** ([ADR-0012] retitled) — supersedes the interim Kimi-on-Bedrock choice (Kimi was only picked because it was *on* Bedrock).
- **Silver: `lingua` language-detect → LLM dissection on every posting** ([ADR-0016]) — "only gold reaches the LLM" reversed (market-wide analytics need all postings).
- **ERR-001: Open → Mitigated** — worked around via DeepSeek; the Bedrock quota is still 0 but no longer blocks. AWS case `178220019100382` stays open as *optional*.
- **Dev infra: dedicated local Postgres** (`jobfetcher-db`, docker-compose) for storage tests — LocalStack can't mock the RDS Data API ([ADR-0018]).

### Notes — honest tradeoffs (read before changing the LLM)

- **Scorer VG3 (determinism) → best-effort.** `deepseek-v4-pro` is non-deterministic *even at temp 0* (MoE / FP non-determinism), so byte-identical re-scores aren't guaranteed. Temp 0 is still sent (the guaranteed invariant); the v0 score is a **triage signal**, with precise calibration deferred to **M7**.

- DeepSeek is **China-hosted** and its ToS permits **training on API inputs** — accepted for *public JD text*; the `Scorer` (which sends the CV/profile) can flip to local Ollama or Anthropic-direct via config ([ADR-0017]).
- The DeepSeek API needs a **funded balance** — its "free signup tokens" did not apply (returned `402` until a small top-up).
