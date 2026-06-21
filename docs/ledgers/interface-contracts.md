# Ledger · Interface Contracts (Produces → Consumes)

> The single source of truth for what each stage **emits** and who **consumes** it. When a release closes, verify its *Consumes* against already-shipped *Produces*, then append its *Produces* row. This prevents cross-stage drift in a multi-stage pipeline — and doubles as lineage documentation.

**Status:** nothing shipped (tagged) yet. The first contract — the **`SearchSpec`** search input — is **built** (🚧); the rest below are the *planned* v0 contracts (⬜ until v0.1 ships).

| Stage | Status | Produces (exact artifacts) | Consumed by |
|---|---|---|---|
| **v0 · search input** | 🚧 built | validated `SearchSpec` (job_titles, countries, cities, states, knobs, budget) — [scripts/search_spec.py](../../scripts/search_spec.py) | v0 · fetch (query fan-out) + gold (city/state filters) |
| **v0 · fetch** | ⬜ | `posting` rows (status `fetched`, normalized via the data contract) · raw JSON at `s3://…/raw/{source}/{date}/{id}.json` · correlation `run_id` | v0 · score |
| **v0 · score** | ⬜ | `score` rows (score, fit_category, strengths, gaps, strategic_assessment, skills_extracted, sector, poster_type, legitimacy_verified; status `scored`) | v0 · notify; (later) analytics, near-miss |
| **v0 · notify** | ⬜ | one daily SES digest email (matches ≥ threshold + below-threshold count) | Tarig (human) |
| **v0 · schema** | ⬜ | Postgres tables `posting`, `cluster` (1:1 in v0), `score`, `profile` (Alembic-migrated) | all later migrations build on this |

### Appended as migrations ship
*(empty — each future release appends its Produces row here at close, e.g. M1 cv_tailor → `cv` rows + DOCX/PDF S3 keys; M2 dedup → `cluster` grouping + `match_status`; M5 dbt → marts.)*

> Naming conventions (resource names, S3 prefixes, table names) referenced here are defined in [02-architecture](../02-architecture.md). Keep them canonical there; this ledger references, not redefines.
