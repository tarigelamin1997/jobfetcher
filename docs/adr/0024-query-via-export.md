# ADR-0024 — Query / filter via export-to-SQLite (not a custom UI)

**Status:** Accepted
**Date:** 2026-07-06

## Context

Tarig wants to **filter, search, and organize** the saved records — all runs, the bronze/silver history, and every score (including the reassess before→after). The data is **SQL-shaped and small** (one user, a few hundred rows). The question is *how to expose it* for casual ad-hoc filtering without over-building.

## Decision

Ship **`scripts/export.py`** — a read-only snapshot of the operational DB into **SQLite + CSV** (a gitignored `export/` dir) — and let the user filter/search/sort in a **purpose-built generic tool**, not a UI we build.

- The star is a **flat `jobs` table** (`posting LEFT JOIN score ON cluster_id LEFT JOIN cluster LEFT JOIN bronze_posting`), one filterable row per posting: role (title/company/seniority/sector), geo (country/city/state), skills (searchable text + raw JSON), status, scoring (`score`/`previous_score`/`fit_category`/…), apply_url, dates. Supporting tables: `bronze` (the full fetch history, no raw blob), `runs`, `profile_current`.
- The script also **prints a summary** (totals, fit-category counts, graduations, top-5) — instant value with no tool.
- **Recommended viewer: Datasette** (`pip install -e '.[query]' && datasette export/jobs.sqlite`) — faceted filters, full-text search, sortable columns, a SQL box. Also fine: DB Browser for SQLite (GUI), Excel/Sheets (the CSV), or raw `sqlite3`.

## Alternatives considered

- **A custom filter UI / filter-flag CLI.** Rejected: reinvents what SQL tools already do superbly — an endless stream of "add a filter for X" flags/widgets. The value is *the data in a filterable form*, not bespoke filtering code.
- **A hosted web dashboard.** Rejected *for now*: auth + hosting + a frontend is a large build. It's the eventual **end-state**, and it would read the same store — this export is the seam to it.
- **Live-query Aurora from a GUI.** Rejected: the operational DB is the **Aurora Data API** (HTTP, not a standard Postgres wire connection) and it **scale-to-0 auto-pauses** — a GUI pointed at it is awkward, resume-laggy, and hits the live DB. A local snapshot is faster, offline, and safe to slice however.
- **Direct SQL only (document how to run queries via the Data API).** Rejected as the *primary* answer: too technical for casual "organize"; but it's still available (`sqlite3` / Datasette's SQL box) for power users.

## Consequences

- Unlimited **filter / search / sort / facet** for free, offline, no Aurora-resume lag; re-run `export.py` to refresh (the snapshot is **point-in-time**).
- No runtime dependency added (Datasette is an *optional* `[query]` extra; SQLite is stdlib).
- The snapshot may contain the real profile / job data → `export/` is gitignored, never committed.
- **Follow-ons (noted):** a `score_history` append table for full time-series ("45→62→78"), and a per-run **input snapshot** (which profile/config produced each score) to fully "search previous inputs" — both small schema adds; today's export covers the full *results* history + the current profile. A hosted **dashboard** remains the end-state.

Full reasoning: [journal](../01-session-decision-journal.md) · plan §42. Related: [ADR-0018](0018-persistence-sqlalchemy-data-api-repository.md) (the DB), [ADR-0023](0023-reassess-replay.md) (the `previous_score` history this surfaces).
