# ADR-0033 — Local operator control panel (Streamlit)

**Status:** Accepted · **✅ shipped v0.12.0 U2 (merged PR #33, 2026-07-17)** · the second of v0.12.0's two units ([ADR-0032](0032-full-s3-audit-persistence.md) = full S3 audit persistence) · built by the agentic squad (implement → Examiner **CLEAN PASS**) · **NON-CRUCIAL** (local tool, no deploy)

## Context
The operator's only surfaces onto the data were CLIs + snapshots: `scripts/export.py` → a read-only SQLite/Datasette **snapshot** ([ADR-0024](0024-query-via-export.md)); the v0.10.0 presigned static HTML **report** (read-only, same-day, [ADR-0030](0030-reachable-full-list-from-digest.md)); and `scripts/track.py` → **CLI curation** (override a score, record an outcome — append-only, one posting at a time). None was a live, browsable, editable view — the operator "cannot curate the database." Separately, the search params (threshold/location/titles) were runtime-editable ([ADR-0022](0022-runtime-config-in-s3.md), v0.3.0) but only by hand-editing YAML + running a CLI, not through a friendly form. Tarig asked for a *display* that browses + curates the records and edits the config manually before the API calls.

## Decision
Ship a **local operator control panel** — `scripts/panel.py`, run with `streamlit run scripts/panel.py`. It runs on the operator's own machine against the **live** Aurora + S3: **nothing hosted, no auth, no ongoing cost, no deploy** (the deliberately-deferred "hosted dashboard end-state" realized locally + minimally, P1). Three tabs:

- **Browse** — every scored job in a filterable/searchable/sortable grid, reading Aurora live (reuses `scripts/export.py::read_data` for the flat `jobs` table + `db/engine.py::wait_for_db_resume` for the scale-to-0 cold-start spinner).
- **Curate** — override a score / record an application outcome, reusing the **same validated write paths** as the CLI (the extracted `track.apply_override` → `set_score_override` + `human-override` lineage event, [ADR-0026](0026-outcome-tracking-override-lineage.md); `repo.track_application_event`). The panel can do nothing the CLI couldn't.
- **Config** — a form over the `SearchSpec` fields (threshold/floor/band · titles/countries/cities · date_posted/remote/employment_types); on save it starts from the **full** `model_dump(mode="json")` (so the required fields not shown — `source`/`secret_name`/`aws_region`/`language`/`states`/`budget`/age-bounds — are preserved), overlays the edits, and runs `push_config.validate_config_text` **before** writing the local YAML + uploading to S3 (`push_config.push_config_text`) — a bad edit is blocked at the gate, never reaching S3.

**Framework = Streamlit**, added as an **optional `[panel]` extra** (like the existing `[query]`/datasette extra) — **never** in the Lambda zip (`build_lambda.py` copies only `src/jobfetcher` with an explicit `RUNTIME_DEPS`). **Reuse-first:** two small behavior-preserving extractions (`track.apply_override`; `push_config.validate_config_text`/`push_config_text`) let the panel share the CLIs' exact logic; the CLIs stay thin wrappers with their tests green. **No deploy, no migration, no infra, no runtime dependency.**

## Alternatives Considered
- **A hosted web dashboard** (API Gateway + Lambda + frontend + auth) — the [ADR-0024](0024-query-via-export.md) end-state / backlog **B-1 rung 3**. Rejected for now: a large build with ongoing cost + auth/security + the multi-tenant/PII liability the project deliberately avoids (self-hosted, [journal §7](../01-session-decision-journal.md)); a local tool delivers the same operator value at a fraction of the complexity. Kept as the documented future.
- **FastAPI + HTMX / Flask.** Rejected: multiples more code (hand-written templates + table + form handlers) for a single-operator, no-auth, localhost tool — Streamlit gives the grid + edit widgets + a validated form with the least code, Python-native so it imports the package + the CLIs directly.
- **Enhance the static export/report + keep CLI-only curation.** Rejected: a static page can't edit (curate) or write config; the ask was a live, editable surface.
- **Full field editing / row deletion in the panel.** Rejected: fights the append-only lineage design; curation stays to score overrides + outcome events (the existing, auditable writes).

## Consequences
- **Easier:** the operator browses + curates the whole scored set and edits the search config in one local surface — no YAML/CLI for the common loop; the config form can never push an invalid spec (shared validation gate).
- **Bounded:** it's a *local* tool (the operator's machine, live DB) — no hosting/auth/cost, and it can't do anything the CLIs couldn't (same write paths). The Streamlit dependency never touches the Lambda.
- **Reconciles:** [ADR-0024](0024-query-via-export.md) (export is now the lightweight portable snapshot; the panel is the live view) + [ADR-0022](0022-runtime-config-in-s3.md) (the config form is a front-end over the S3-config mechanism).
- **Verification:** Examiner CLEAN PASS — behavior-preservation of the extracted override path (byte-identical CLI output + exit codes), the all-required `SearchSpec` contract preserved through the form, `validate_config_text` confirmed as the genuine gate, no Lambda coupling, the test monkeypatch seam intact. The extracted functions are unit-tested; the Streamlit UI is manual/smoke.
