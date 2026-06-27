# Ledger · Interface Contracts (Produces → Consumes)

> The single source of truth for what each stage **emits** and who **consumes** it. When a release closes, verify its *Consumes* against already-shipped *Produces*, then append its *Produces* row. This prevents cross-stage drift in a multi-stage pipeline — and doubles as lineage documentation.

**Status:** nothing shipped (tagged) yet. Two contracts are **built** (🚧): the **`SearchSpec`** search input, and the **`DissectedPosting`** silver dissection contract (build unit C-1). The rest below are the *planned* v0 contracts (⬜ until v0.1 ships).

| Stage | Status | Produces (exact artifacts) | Consumed by |
|---|---|---|---|
| **v0 · search input** | 🚧 built | validated `SearchSpec` (job_titles, countries, cities, states, knobs, budget) — [core/search_spec.py](../../src/jobfetcher/core/search_spec.py) | v0 · fetch (query fan-out) + gold (city/state filters) |
| **v0 · fetch** | 🚧 built (Step 4) | immutable `bronze_posting` rows + raw JSON at `s3://…/raw/{source}/{date}/{id}.json`, then silver `posting` rows (clean → dissect → fingerprint, status `silver`) · correlation `run_id` · exact-id dedup — [adapters/jsearch_source.py](../../src/jobfetcher/adapters/jsearch_source.py) + [core/ingest.py](../../src/jobfetcher/core/ingest.py) | v0 · gold filter + score |
| **v0 · silver dissect** | 🚧 built (C-1) | `DissectedPosting` — grounded `skills[]` (name / level `{must\|nice\|implied}` / evidence), sector, normalized_title + carried metadata (location, seniority, language) — [src/jobfetcher/core/models.py](../../src/jobfetcher/core/models.py) via the `Dissector` (ADR-0016) | v0 · gold filter + score — **persisted as JSONB + scalar columns on `posting`** ([ADR-0018](../adr/0018-persistence-sqlalchemy-data-api-repository.md)); (later) `fct_job_skill` / `dim_skill` at M5 |
| **v0 · score** | ⬜ | `score` rows (score, fit_category, strengths, gaps, strategic_assessment, poster_type, legitimacy_verified; status `scored`) — reads `skills`/`sector` from the silver dissection on `posting` | v0 · notify; (later) analytics, near-miss |
| **v0 · notify** | ⬜ | one daily SES digest email (matches ≥ threshold + below-threshold count) | Tarig (human) |
| **v0 · schema** | 🚧 built (C-2) | Postgres tables `bronze_posting`, `posting` (silver + dissected columns — `skills jsonb`, sector, normalized_title, seniority, language, …), `cluster` (1:1 in v0), `score` (reconciled — no `skills_extracted`/`sector`/`seniority`), `profile` — [src/jobfetcher/db/tables.py](../../src/jobfetcher/db/tables.py) + Alembic [0001](../../migrations/versions/0001_v0_initial_schema.py); reached via the `Repository` port ([adapters/repository_postgres.py](../../src/jobfetcher/adapters/repository_postgres.py) · [ADR-0018](../adr/0018-persistence-sqlalchemy-data-api-repository.md)) | all later migrations build on this |

### Appended as migrations ship
*(empty — each future release appends its Produces row here at close, e.g. M1 cv_tailor → `cv` rows + DOCX/PDF S3 keys; M2 dedup → `cluster` grouping + `match_status`; M5 dbt → marts.)*

> Naming conventions (resource names, S3 prefixes, table names) referenced here are defined in [02-architecture](../02-architecture.md). Keep them canonical there; this ledger references, not redefines.
