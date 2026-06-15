# 03 · Roadmap

> ⚠️ **This roadmap is a living hypothesis, not a contract.** You cannot draw the full sequence before shipping — each stage's *implementation* is the bottleneck that reveals the next capability ([P2](00-design-philosophy.md#p2--bottleneck-driven-evolution)). We commit firmly to only three things: **v0**, the **migratable architecture**, and **release discipline**. Everything past the current release is re-evaluated *after* it ships, using the protocol below. Treat the migration list as *direction*, and re-derive the next step from real usage each time.

---

## What we actually commit to

1. **v0 — the minimal working core** (fully designed; see [04-v0-build-plan](04-v0-build-plan.md)).
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

---

## Directional roadmap (hypothesis)

The order reflects: value-first · dependency-respecting · capability-arrives-when-justified · each release = a coherent story chapter. **It will change** as releases teach us things.

| Release | Adds | Bottleneck it breaks (the *why*) |
|---|---|---|
| **v0.1** | One Lambda: 1 source → S3 + Postgres → Bedrock score → **daily email**. Terraform, Secrets Manager, tests, minimal CI. | "I have no automated scored shortlist at all." The irreducible working loop. |
| **M1 · v0.2** | **CV tailoring** (reliable renderer, one master CV, draft→review). | "I see good matches but still hand-tailor every CV." (Tarig's chosen first migration.) |
| **M2 · v0.3** | **Multi-source + clustering dedup + Suspected-Duplicates.** | "One source misses jobs" → add source #2 (**Adzuna** is the candidate) → *which creates the cross-source duplicate problem* → clustering dedup. Capability + its justification arrive together. (v0 is JSearch-only, so v0 needs only exact-id dedup — [ADR-0010](adr/0010-job-source-jsearch.md).) |
| **M3 · v0.4** | **Single Lambda → Step Functions.** | "The one Lambda now does fetch-multi→dedup→score→CV→email — too big to retry/observe cleanly." Orchestration is *earned*. |
| **M4 · v0.5** | **Notion workspace + near-miss/graduation.** | "Email alone can't track status or watch near-misses." Adds Status/Suspected-Dup/Near-Miss DBs + watch→re-score→graduate; stands up the calibration-correction surface. |
| **M5 · v0.6** | **dbt marts on Postgres** (staging→marts, tests/lineage/incremental). | "I can't answer market/skill questions over accumulated data." DE-depth headliner — after data has accumulated. |
| **M6 · v0.7** | **Skill-Demand tracker + Sector Intelligence** (on the marts). | "I have models but no career-strategy output." Depends on M5. |
| **M7 · v0.8** | **Right-sized observability + scoring calibration loop.** | "I don't know when it silently breaks, or how accurate scoring is." A few real alarms + documented SLOs + calibration from M4's correction data. |
| **M8 · v1.0.0** | **CI/CD hardening + polished README + architecture diagram + demo video + seam-ready stubs.** | "It works but doesn't *present* as production-grade." Feature-complete single-user system. |

**Future migrations (v1.x / v2.0)** — built *only if/when* a real bottleneck justifies each: **Debezium CDC** (batch→streaming, when volume/latency demands), **multi-user** (v2.0, the `user_id` dimension), **feedback hub**, **BI dashboard** over marts, **Snowflake** (if Postgres analytics becomes a real bottleneck), **MWAA/Airflow** (if Step Functions can't express the DAG). Each is a documented scale-path today, not a plan.

**Semver:** v0.x per migration → **v1.0.0 at M8** (feature-complete single-user) → v1.x for additive future migrations, v2.0 for breaking changes (e.g. multi-user). Light CI from v0; CD hardening at M8.

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
