# Querying your records — filter, search, organize

Your job history lives in the operational DB (Aurora). To **filter / search / sort / organize** it,
export a portable snapshot and open it in a tool built for exactly that ([ADR-0024](adr/0024-query-via-export.md)) —
no custom UI, full "basic operations" for free, offline.
*(As of `v0.10.0` the daily digest's still-open overflow + below-threshold footer carry a **presigned link to a one-click full-list report page** — the primary path ([ADR-0030](adr/0030-reachable-full-list-from-digest.md)); the *"…see your export"* text is now the **degraded fallback** shown only when that report can't be built. This export is the **richer, self-service** surface — full history, lineage, SQL — for digging past the report; [ADR-0027](adr/0027-digest-truthfulness.md).)*
>
> **Live surface (`v0.12.0`):** for a live view — *and* to curate — use the **local control panel** `scripts/panel.py` (`pip install -e '.[panel]'` → `streamlit run scripts/panel.py`): browse/filter every scored job, override a score / record an outcome, and edit your search config → push to S3 — all against the live DB, no snapshot. This `export.py` path stays the **portable, offline** snapshot; the panel is the live view ([ADR-0033](adr/0033-local-control-panel.md)).

## 1. Export a snapshot

```bash
python scripts/export.py
```

This reads the DB (Aurora resumes from idle — a few seconds) and writes, into a gitignored `export/`:

- **`export/jobs.sqlite`** — the snapshot (primary), and
- **`export/jobs.csv`** — the flat `jobs` table (for spreadsheets).

It also prints a **summary** (totals · fit-category counts · graduations · top-5) so you get a quick
read without opening anything. Re-run it any time to refresh (it's a point-in-time snapshot).

*(DB connection: it uses `$JOBFETCHER_DB_URL` if set, otherwise the deployed Aurora via `terraform output`.)*

## 2. Open it and filter

**Datasette (recommended)** — a browser UI with faceted filters, full-text search, sortable columns, and a SQL box:

```bash
pip install -e '.[query]'      # one-time; Datasette is an optional extra, not a runtime dep
datasette export/jobs.sqlite   # opens http://localhost:8001
```

Other options: **DB Browser for SQLite** (desktop GUI → Browse Data → filter/sort per column) · **Excel / Google Sheets** (open `jobs.csv`, AutoFilter) · **raw SQL** (`sqlite3 export/jobs.sqlite`).

## What's in the snapshot

- **`jobs`** — one row per posting (the table you filter): `posting_id`, `run_id`, `status` (silver/gold_candidate/scored), `normalized_title`, `raw_title`, `company`, `seniority`, `sector`, `employment_type`, `country`/`city`/`state`/`location`, `skills` (text) + `skills_json`, `score`, `score_override` (your human correction, if any), `previous_score`, `fit_category` (strong_fit/near_miss/stretch/misaligned), `poster_type`, `legitimacy_verified`, `strengths`, `gaps`, `latest_application_status` + `application_noted_at` (the newest `track.py` event per posting; NULL if you haven't recorded one), `apply_url`, `scored_at`, `fetched_at`, `posting_count`.
- **`bronze`** — the full fetch history (ids, source, run_id, S3 key, fetched_at; the raw JSON stays in S3).
- **`runs`** — the digest send log · **`profile_current`** — your current profile + thresholds.
- **`score_events`** — the append-only score history + lineage (one row per scoring/reassess/override: score, `previous_score`, `scoring_model` — `'human-override'` marks your corrections — `profile_hash`, `run_id`, `scored_at`; [ADR-0025](adr/0025-score-event-lineage.md)).
- **`application_events`** — the full outcome trail written by `scripts/track.py` (every applied/interview/offer/rejected/withdrawn note, with `noted_at` + your free-text `note`; the `jobs` columns above show only the *latest*; [ADR-0026](adr/0026-outcome-tracking-override-lineage.md)).

> **Caveat — override vs. category:** `score_override` sits next to the **LLM's** `fit_category` — an override never rewrites `score.fit_category`. The override's own derived category lives on its `score_event` row (`scoring_model='human-override'`) in `score_events`.

## Example filters

| Goal | SQL (Datasette SQL box / `sqlite3`) |
|---|---|
| All strong fits in Saudi | `SELECT * FROM jobs WHERE fit_category='strong_fit' AND country='sa' ORDER BY score DESC` |
| Jobs that **graduated** on the last reassess | `SELECT normalized_title, company, previous_score, score FROM jobs WHERE previous_score < 60 AND score >= 60` |
| "Architect" roles scoring 50–70 | `SELECT * FROM jobs WHERE normalized_title LIKE '%Architect%' AND score BETWEEN 50 AND 70` |
| Everything from one run | `SELECT * FROM jobs WHERE run_id = '<run_id>'` |
| Your application funnel | `SELECT latest_application_status, count(*) FROM jobs WHERE latest_application_status IS NOT NULL GROUP BY 1` |
| Strong fits you haven't applied to yet | `SELECT * FROM jobs WHERE fit_category='strong_fit' AND latest_application_status IS NULL ORDER BY score DESC` |

In Datasette you can do all of these by clicking facets (country, fit_category, status) and typing in the search box — no SQL needed.
