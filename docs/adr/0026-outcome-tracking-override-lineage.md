# ADR-0026 — Append-only `application_event` outcome tracking + human-override lineage

**Status:** Accepted
**Date:** 2026-07-07

## Context

The pipeline scored and emailed — and recorded **nothing afterward**. `posting.status` dead-ends at `'scored'`; no application/outcome table existed; "Applied / Interview / Offer / Rejected" lived nowhere. Unlike bronze — where anything missed can be replayed — **outcome data is unreplayable**: whether Tarig applied and what came back exists only in the moment, so every applied-to job that went unrecorded was **calibration + funnel data lost forever** (Investigator-verified: zero outcome rows anywhere; the digest → apply → *???* loop was open). The same irrecoverable-loss shape [ADR-0025](0025-score-event-lineage.md) just fixed for scores, one layer up.

A second dead end rode along: **`score.score_override`** has sat in the schema since v0 *for exactly this* — the M7 calibration loop needs human corrections to compare the LLM against — and was **wired to nothing**: no code path wrote it.

## Decision

Record outcomes as an **append-only `application_event` log** written by a tiny CLI, and wire `score_override` as a **dual-write into the existing `score_event` lineage** — no pipeline change, no UI. Ships as migration **`0005_application_event`** (chains to `0004_score_event_lineage`); shipped in **v0.7.0** alongside ADR-0025.

- **The `application_event` table** — one immutable row per human status note: `event_id` (autoincrement PK), `posting_id` (Text FK → `posting`, NOT NULL, indexed), `status` (Text NOT NULL, CHECK-limited to the shared vocabulary `'applied'`/`'interview'`/`'offer'`/`'rejected'`/`'withdrawn'`), `noted_at` (timestamptz, default `now()`), `note` (Text, NULL). **No backfill** — no prior outcome data exists anywhere. **Duplicates are allowed by design:** re-recording a status is a *new event*; "latest" is a read-side query (**latest wins**), never an overwrite — the full applied→interview→… trail survives.
- **The vocabulary lives ONCE** — `APPLICATION_STATUSES` in `core/models.py`: `db/tables.py` **builds the CHECK's SQL from the tuple**, the repository re-validates against it, the CLI's subcommands are generated from it, a unit test **pins the migration's frozen literals** to it (same members, order, count — an added-but-unmigrated status fails the suite, not production), and the integration chain test INSERTs **every** status against the real migrated CHECK.
- **`Repository.track_application_event(posting_id, status, note=None)`** — status + posting existence validated **before any write**; the INSERT is inline, Data-API-plain (**no RETURNING, no PK prefetch** — the same ERR-004/005 hardening as `score_event`); one transaction; unknown posting → `RepositoryError` with **zero rows written**.
- **`Repository.set_score_override(cluster_id, score_override, fit_category, profile_hash, previous_score)`** — 0–100 validated, then **ONE transaction**: a **rowcount-checked UPDATE** of `score.score_override` + an **APPEND** of a `score_event` with **`scoring_model='human-override'`** (score = the override, `previous_score` = the pre-override score, LLM-narrative fields honestly empty). **Tarig's explicit design choice: human overrides join the same lineage log as LLM scorings** — a second override moves the column, but **both events survive**. Both methods are declared on the `Repository` Protocol.
- **`scripts/track.py`** (new CLI, `export.py`'s house pattern — stdlib argparse, same DB-URL resolution): `applied|interview|offer|rejected|withdrawn <posting_id> [--note]` · `find "<substring>" [--company]` (prints a copy-pasteable `posting_id` + score + title—company, top 10) · `events <posting_id>` (the outcome trail, newest first) · `override <posting_id> <score>` (derives `fit_category` from the **profile row's runtime knobs** via `derive_fit_category` — VG8's band routing, same as LLM scorings; `profile_hash` falls back to `'unknown'` on a pre-0004 profile row). Argparse rejects a bad status/score **before any DB contact**; every failure → stderr + exit 1.
- **`scripts/export.py`** — the flat `jobs` table gains **`score_override`**, **`latest_application_status`**, **`application_noted_at`** (the latest event per posting via `LEFT JOIN LATERAL`, ordered `noted_at DESC` with an `event_id DESC` tiebreak; event-less postings get NULLs); the SQLite snapshot gains an **`application_events`** table; the CSV stays the flat `jobs` table only.

## Alternatives considered

- **A mutable `status` column on `posting`.** Rejected: pipeline state (`silver`/`gold_candidate`/`scored`) and user action are different concerns on different lifecycles — and an in-place update destroys history, the exact lesson ADR-0025 just paid for; this release exists to remove that pattern, not add another instance.
- **A separate `override_event` table.** Rejected: fragments score lineage across two logs for no reader benefit — "what judged this job, and when" should be one ordered query.
- **Column-only override (write `score_override`, no lineage event).** Rejected: a second override silently erases the first — the in-place-loss pattern again.
- **Build the Notion workspace (M4) now for status tracking.** Rejected: the full M4 surface when a ~60-line CLI + an export column unblocks the data **today** — anti-P1. Notion remains a live hypothesis; it inherits this data if/when it lands.
- **`UNIQUE(posting_id, status)`.** Rejected: forbids legitimate re-recording (a second interview round, re-applied after a repost); append-only + latest-wins is simpler and truer to events.

## Consequences

- **Funnel + calibration data accumulates from today** — applied→interview→offer rates per fit-category/score band (the M5/M6 funnel marts' source) and human-vs-LLM deltas in one ordered log (`'human-override'` rows next to model rows) for M7 calibration. Data that could never be backfilled starts existing now.
- **Two deliberate semantics (stated plainly, not discovered later):** (1) an override does **NOT** change `score.fit_category` — the override's derived category lives only on its `score_event`; the current view keeps the **LLM's** category next to your override number. (2) `save_score`'s upsert **never clears `score_override`** — an override deliberately **survives** later re-scores/reassess; replacing it is a human act (a new override), never a pipeline side effect.
- **⚠️ Deploy sequencing:** the new export SQL and `track.py` reference `application_event` **unconditionally** — run against a DB not migrated to 0005, either fails with `UndefinedTable` (loud, harmless, zero rows). The order is: **`alembic upgrade head` before the first `track.py` / new-export use.** (Registered in the [procedure registry](../ledgers/procedure-registry.md).)
- **Residual live-validation item:** `set_score_override` is the codebase's **first `.rowcount` reliance** — a new Aurora Data API dialect surface (the ERR-004/005 lesson: only provable live). One `track.py override` joins the v0.7.0 release's live smoke.
- Verified: 283 unit green; 36 integration passed + 5 live-key skips (local Postgres window, 2026-07-07); full suite 319 passed, 94.74% coverage (85% floor); `ruff` clean; independent fresh-context adversarial Examiner: **clean pass, zero blocking** (its S-1 finding — a tautological drift pin — fixed before close).

Full reasoning: [journal](../01-session-decision-journal.md). Related: [ADR-0025](0025-score-event-lineage.md) (the `score_event` log human overrides now join + the append-only discipline this extends), [ADR-0024](0024-query-via-export.md) (the export surface it extends), [ADR-0018](0018-persistence-sqlalchemy-data-api-repository.md) (the `Repository` port), [ADR-0023](0023-reassess-replay.md) (the reassess an override now survives).
