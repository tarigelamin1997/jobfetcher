# 03 · Roadmap

> ⚠️ **This roadmap is a living hypothesis, not a contract.** You cannot draw the full sequence before shipping — each stage's *implementation* is the bottleneck that reveals the next capability ([P2](00-design-philosophy.md#p2--bottleneck-driven-evolution)). We commit firmly to only three things: **v0**, the **migratable architecture**, and **release discipline**. Everything past the current release is re-evaluated *after* it ships, using the protocol below. Treat the migration list as *direction*, and re-derive the next step from real usage each time.

---

## What we actually commit to

1. **v0 — the minimal working core** — ✅ **SHIPPED (tag `v0.1.0`, 2026-06-29):** built, deployed to AWS, validated live end-to-end, torn down to ~$0 (see [04-v0-build-plan](04-v0-build-plan.md)). **Everything past here is re-derived from real v0 usage via the protocol below** — the M1–M8 list is direction, not commitment. **Six releases have now shipped past v0.1** (`v0.2.0`→`v0.6.0`, all live-validated on the deployed stack), and what shipped **diverged from the pre-drawn M1–M8** exactly as the P2 protocol predicts — see the table below.
2. **The migratable architecture** — built so migrations stay clean and observable (requirements below).
3. **Release discipline** — every migration is a clean, semver-tagged, documented GitHub release.

Everything else is a *hypothesis about direction*, not a promise.

---

## The migration-decision protocol (how the next step is actually chosen)

This is the **engine**, run *after every release*:

1. **Ship the stage. Use it. Observe.** Real usage > speculation.
2. **Surface the top-3 bottlenecks** blocking the next *real capability* (a capability — not polish, not "would be nice").
3. **Rank by leverage** = `capability unlocked ÷ complexity added`.
4. **Design the minimal migration** that breaks the highest-leverage bottleneck (apply [tool-minimalism](00-design-philosophy.md#tool-minimalism-wins-the-gate--de-depth-is-the-tiebreaker): does a real *tool* need justify it? DE-depth only breaks ties).
5. **Ship as a clean, labeled release** — an [ADR](adr/) records *bottleneck → capability unlocked → minimal solution*; CHANGELOG + migration notes.
6. **Repeat.**

> Every migration's ADR must be honestly labeled **tool-bottleneck-driven** or, if it's a deliberate portfolio showcase, **portfolio-capability-driven (minimal version)** — never disguised as a need it isn't.

> **Where candidates come from:** friction found by *using the tool* accumulates in the [backlog ledger](ledgers/backlog.md) — the raw input to **step 2** above. Open items (2026-07-10): **B-1** reachable full-list-from-the-digest (the "…and 55 more / +225 below threshold" lines are dead text, not links) · **B-2** digest deliverability (landed in Gmail Spam). These are ranked alongside the directional candidates when the next migration is chosen.

---

## Directional roadmap (hypothesis)

The order reflects: value-first · dependency-respecting · capability-arrives-when-justified · each release = a coherent story chapter. **It will change** as releases teach us things.

| Release | Adds | Bottleneck it breaks (the *why*) |
|---|---|---|
| **v0.1** ✅ **shipped (`v0.1.0`, 2026-06-29)** | One Lambda: 1 source → S3 + Postgres → LLM score (DeepSeek) → **daily email**. Terraform (14-resource stack: Aurora SLv2 + Data API · S3 · least-priv IAM · EventBridge · SES), Secrets Manager, tests, minimal CI. **Deployed to AWS, validated live end-to-end, then torn down to ~$0.** | "I have no automated scored shortlist at all." The irreducible working loop — **done**. |
| **M1 · v0.2** ✅ **shipped (`v0.2.0`, 2026-07-02)** | **Pipeline hardening** — LLM retry+jitter & symmetric failure isolation · in-Lambda concurrency + deadline guard + `retry=0` IaC (mem 1024) · subset-title gold filter + selectable LLM filter ([ADR-0021](adr/0021-m1-pipeline-hardening.md)). | **The P2 protocol overrode the CV-tailoring hypothesis.** Running v0.1 live on the full sweep, the *actual* first bottleneck wasn't "I hand-tailor CVs" — it was **the pipeline can't complete a full run**: serial dissect timed out (silver ~17), one `503` killed a run, the filter wasted pro-model calls on junk, and AWS blind-retried the dead run. Re-validated live: **~13× throughput, 0 crashes, junk eliminated, 21-job digest sent.** *(Exactly the P2 mechanism working: the roadmap is a hypothesis; usage re-ranks it. CV tailoring is re-queued as a later candidate.)* |
| **v0.3.0** ✅ **shipped (`v0.3.0`, 2026-07-03)** | **User-customizable settings + runtime config in S3** — the 3 strictness knobs became user config (the `profile` row re-syncs from config **every run**, fixing the write-once trap); config YAMLs moved out of the Lambda zip into S3, read at runtime → change settings via `scripts/push_config.py`, **no rebuild/redeploy** ([ADR-0022](adr/0022-runtime-config-in-s3.md)). | Real daily-use friction, re-ranked above the pre-drawn migrations: every settings tweak needed a redeploy, and the profile was write-once (couldn't be changed after first seed). Same P2 mechanism — usage surfaces the true next bottleneck. |
| **v0.3.1** ✅ **shipped (`v0.3.1`, 2026-07-03)** | **`employment_types`** — validated as an enum + actually wired through to JSearch. | A search knob that wasn't reaching the source. **First patch release** under the newly-adopted patch-versioning rule (see Semver below). |
| **v0.4.0** ✅ **shipped (`v0.4.0`, 2026-07-06)** — *delivers the old M4 graduation half, early* | **Reassess / replay** — `{"mode":"reassess"}` re-scores existing jobs against the **updated profile** with **zero re-fetch** (immutable-bronze replay); jobs **graduate** (e.g. stretch → strong_fit) as skills grow; `previous_score` tracks before→after ([ADR-0023](adr/0023-reassess-replay.md)). | "When my profile changes, yesterday's jobs keep a stale score." **Realizes the graduation half of the old M4** — re-derived from real use, years ahead of the Notion workspace it was bundled with. Live-proven: **180 reassessed, 15 graduated, bronze unchanged.** |
| **v0.5.0** ✅ **shipped (`v0.5.0`, 2026-07-06)** | **Query / filter access** — `scripts/export.py` → a SQLite/CSV snapshot (flat jobs table + bronze/runs/profile) opened in Datasette / DB-Browser / Excel; **no custom UI** ([ADR-0024](adr/0024-query-via-export.md)). | "I can't slice/query my own scored jobs." The minimal answer to the query bottleneck; a hosted dashboard stays the end-state (M5+). |
| **v0.6.0** ✅ **shipped (`v0.6.0`, 2026-07-06)** | **Email UX** — the daily digest redesigned into scannable cards with a prominent **Apply** button + location per job; `scripts/preview_digest.py` local preview. | Tarig's live-v0.2 feedback: "the format is poor and the links need to be visible." The digest's daily usefulness — the email-UX candidate, **now shipped.** |
| **M2 · vNext?** | **Multi-source + clustering dedup + Suspected-Duplicates.** | "One source misses jobs" → add source #2 (**Adzuna** is the candidate) → *which creates the cross-source duplicate problem* → clustering dedup. Capability + its justification arrive together. (JSearch-only today needs only exact-id dedup — [ADR-0010](adr/0010-job-source-jsearch.md).) |
| **M3** | **Single Lambda → Step Functions / chunking.** | "The one Lambda now does fetch-multi→dedup→score→CV→email — too big to retry/observe cleanly." Orchestration is *earned*. **Now-evidenced bottleneck (v0.1 finding):** the single Lambda fits the daily incremental run but **can't run the full 18-query × 30-day backfill inside the 15-min Lambda max** — so M3 already has real usage data behind it. |
| **M4** *(partly shipped)* | **Notion workspace + near-miss surfacing.** *(The graduation half already shipped early as v0.4.0 reassess — [ADR-0023](adr/0023-reassess-replay.md).)* | "Email alone can't track status or watch near-misses." Adds Status/Suspected-Dup/Near-Miss surfaces; stands up the calibration-correction surface. |
| **M5** | **dbt marts on Postgres** — the [constellation model](adr/0011-dimensional-analytical-model.md): `dim_skill` + `fct_job_skill` bridge **first**, then point-in-time profile + score facts, then `dim_sector` (tests/lineage/incremental; grown per question). | "I can't answer market/skill questions over accumulated data." DE-depth headliner — after data has accumulated. |
| **M6** | **Skill-Demand tracker + Sector Intelligence** (on the marts). | "I have models but no career-strategy output." Depends on M5. |
| **M7** | **Right-sized observability + scoring calibration loop.** | "I don't know when it silently breaks, or how accurate scoring is." A few real alarms + documented SLOs + calibration from M4's correction data. |
| **M8 · v1.0.0** | **CI/CD hardening + polished README + architecture diagram + demo video + seam-ready stubs.** | "It works but doesn't *present* as production-grade." Feature-complete single-user system. |

> **Versions past M1 are unpinned on purpose.** The pre-drawn `M2 = v0.3`, `M3 = v0.4` … mapping is void: real usage re-ranked the queue, so `v0.3`–`v0.6` were spent on settings/config, reassess, query-access, and email-UX instead. The M-labels below record *direction*; the next actual release number is assigned when the P2 protocol picks the next migration. CV tailoring (the pre-drawn M1) is likewise re-queued as a later candidate, not dropped.

**Future migrations (v1.x / v2.0)** — built *only if/when* a real bottleneck justifies each: **Debezium CDC** (batch→streaming, when volume/latency demands), **multi-user** (v2.0, the `user_id` dimension), **feedback hub**, **BI dashboard** over marts, **Snowflake** (if Postgres analytics becomes a real bottleneck), **MWAA/Airflow** (if Step Functions can't express the DAG). Each is a documented scale-path today, not a plan.

**Semver:** a **minor** bump (`v0.x.0`) per migration / real capability; a **patch** bump (`v0.x.y`) for small fixes + improvements between migrations (a bug fix or a tiny wiring doesn't need a whole minor). **This patch-versioning rule was formally adopted at `v0.3.0` and first exercised by `v0.3.1`** (the `employment_types` fix). **`v1.0.0` at M8** (feature-complete single-user) → v1.x for additive future migrations, v2.0 for breaking changes (e.g. multi-user). Light CI from v0; CD hardening at M8.

---

## Migratability requirements (build v0 so the above stays cheap)

These are *foundational* — they go into v0 so every later migration is clean and observable:

- **Ports-&-adapters boundaries** — sources, storage, notifier, scorer behind interfaces, so you add/swap without rewrites.
- **Config-driven feature flags** — a migration becomes "enable + deploy."
- **First-class schema/data migrations** — Alembic (Postgres) + versioned S3 layout + dbt migrations; every release ships its migration.
- **Release discipline** — semver + git tag + CHANGELOG entry + one ADR + UPGRADING note + a migration script when data changes; backwards-compatible by default, breaking changes flagged.
- **Additive Terraform modules** — clean `terraform plan` diffs per migration.
- **Migration tests** — prove: old data preserved + new capability works + old capability still works.
- **Each release documents** a before/after architecture diagram + roll-forward/back notes.

---

## End-state vision (directional)

The destination — *reached by migration, never built at once* — is the [target architecture](02-architecture.md): a two-plane, self-hosted, reproducible system demonstrating LLM scoring + warehouse/dbt modeling + AWS-serverless/IaC + (eventually) streaming/CDC, fully documented, that an interviewer can clone and run. But the **point of this project is the journey**: a public, legible sequence of releases where each step is the *minimal justified response to a real bottleneck*. The evolution is the portfolio.
