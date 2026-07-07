#!/usr/bin/env python3
"""export.py — snapshot the operational DB to a portable SQLite + CSV you can filter/search/
organize in a generic tool (ADR-0024). The data is SQL-shaped + tiny, so rather than a custom
filter UI we export and open in a purpose-built viewer:

    python scripts/export.py                 # -> export/jobs.sqlite + export/jobs.csv + a summary
    datasette export/jobs.sqlite             # faceted filter/search/sort in a browser (recommended)
    # or open export/jobs.csv in Excel/Sheets, or `sqlite3 export/jobs.sqlite`

The star is a flat `jobs` table (one filterable row per posting: role · geo · skills · status ·
score/previous_score/fit_category · score_override · the latest application status · apply_url ·
dates), plus `bronze` (the full fetch history), `runs`, `score_events` (the append-only score
history + lineage, migration 0004), `application_events` (the append-only outcome trail,
migration 0005), and the current `profile`. Read-only; a point-in-time snapshot — re-run to
refresh.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobfetcher.db.engine import make_engine  # noqa: E402
from jobfetcher.handlers.pipeline import resolve_db_url  # noqa: E402 (reuse the URL builder)

# ── the flat `jobs` table: posting ⨝ score ⨝ cluster ⨝ bronze (LEFT so un-scored silver shows) ──
_JOBS_SQL = """
SELECT
  p.posting_id, p.source, b.run_id, p.status,
  p.normalized_title, p.title AS raw_title, p.company, p.seniority, p.sector, p.employment_type,
  p.country, p.city, p.state, p.location,
  p.skills,
  s.score, s.score_override, s.previous_score, s.fit_category, s.poster_type,
  s.legitimacy_verified, s.scored_at,
  s.strengths, s.gaps, s.strategic_assessment,
  ae.status AS latest_application_status, ae.noted_at AS application_noted_at,
  p.apply_url, p.fetched_at, c.posting_count
FROM posting p
LEFT JOIN score s ON p.cluster_id = s.cluster_id
LEFT JOIN cluster c ON p.cluster_id = c.cluster_id
LEFT JOIN bronze_posting b ON p.bronze_id = b.bronze_id
LEFT JOIN LATERAL (
  SELECT status, noted_at FROM application_event
  WHERE posting_id = p.posting_id
  ORDER BY noted_at DESC, event_id DESC
  LIMIT 1
) ae ON TRUE
ORDER BY s.score DESC NULLS LAST, p.posting_id
"""

# no raw_payload — it's large + already in S3; this is the fetch-history index
_BRONZE_SQL = "SELECT bronze_id, source, source_job_id, run_id, s3_raw_key, fetched_at FROM bronze_posting ORDER BY fetched_at"
_RUNS_SQL = "SELECT run_date, user_id, run_id FROM run_log ORDER BY run_date"
_PROFILE_SQL = "SELECT user_id, threshold, hard_floor, near_miss_band, profile FROM profile"
# the append-only score history + lineage (migration 0004); no strengths/gaps JSONB — the
# current judgment lives on `jobs`, this is the score-delta/provenance index
_EVENTS_SQL = (
    "SELECT event_id, cluster_id, score, fit_category, previous_score, poster_type, "
    "legitimacy_verified, scoring_model, profile_hash, run_id, scored_at "
    "FROM score_event ORDER BY event_id"
)
# the append-only application-outcome trail (migration 0005), written by scripts/track.py
_APP_EVENTS_SQL = (
    "SELECT event_id, posting_id, status, noted_at, note "
    "FROM application_event ORDER BY event_id"
)


# --------------------------------------------------------------------------- transforms (pure)
def _as_list(value: Any) -> list:
    """A JSONB column arrives as a Python list (local psycopg2) OR a JSON string (Data API) OR
    None. Normalize to a list."""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            return json.loads(value) or []
        except json.JSONDecodeError:
            return []
    return list(value)


def skills_text(skills: Any) -> str:
    """`[{name, level, evidence}, …]` → a comma-joined, searchable `"Python, SQL, Airflow"`."""
    return ", ".join(str(s.get("name", "")).strip() for s in _as_list(skills) if s.get("name"))


def list_text(items: Any) -> str:
    """A JSON list of short phrases (strengths/gaps) → newline-joined plain text."""
    return "\n".join(str(x).strip() for x in _as_list(items) if str(x).strip())


def _row_to_job(row: dict) -> dict:
    """Flatten one `jobs` row: JSONB → readable text (+ keep raw skills JSON for Datasette)."""
    out = dict(row)
    out["skills"] = skills_text(row.get("skills"))
    out["skills_json"] = json.dumps(_as_list(row.get("skills")), ensure_ascii=False)
    out["strengths"] = list_text(row.get("strengths"))
    out["gaps"] = list_text(row.get("gaps"))
    out["legitimacy_verified"] = _bool01(row.get("legitimacy_verified"))
    for k, v in list(out.items()):  # timestamps/dates → ISO strings (sqlite/csv friendly)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


def _bool01(v: Any) -> Any:
    return None if v is None else (1 if v else 0)


# --------------------------------------------------------------------------- sqlite/csv writer
def write_snapshot(
    *, jobs: list[dict], bronze: list[dict], runs: list[dict], profile: list[dict],
    events: list[dict], application_events: list[dict], out_dir: Path
) -> tuple[Path, Path]:
    """Write `jobs.sqlite` (jobs + bronze + runs + profile_current + score_events +
    application_events) and `jobs.csv` (the flat jobs table only — the existing CSV shape).
    Pure over in-memory lists (unit-testable, no DB)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = out_dir / "jobs.sqlite"
    csv_path = out_dir / "jobs.csv"

    if sqlite_path.exists():
        sqlite_path.unlink()  # a fresh snapshot each run
    conn = sqlite3.connect(sqlite_path)
    try:
        _write_table(conn, "jobs", jobs)
        _write_table(conn, "bronze", bronze)
        _write_table(conn, "runs", runs)
        _write_table(conn, "profile_current", profile)
        _write_table(conn, "score_events", events)
        _write_table(conn, "application_events", application_events)
        conn.commit()
    finally:
        conn.close()

    # the flat jobs table as CSV (Excel/Sheets)
    if jobs:
        cols = list(jobs[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(jobs)
    else:
        csv_path.write_text("", encoding="utf-8")
    return sqlite_path, csv_path


def _write_table(conn: sqlite3.Connection, name: str, rows: list[dict]) -> None:
    if not rows:
        conn.execute(f'CREATE TABLE "{name}" (_empty TEXT)')
        return
    cols = list(rows[0].keys())
    col_defs = ", ".join(f'"{c}"' for c in cols)
    conn.execute(f'CREATE TABLE "{name}" ({col_defs})')
    placeholders = ", ".join("?" for _ in cols)
    conn.executemany(
        f'INSERT INTO "{name}" VALUES ({placeholders})',
        [[r.get(c) for c in cols] for r in rows],
    )


# --------------------------------------------------------------------------- DB read
def _fetch(engine, sql: str) -> list[dict]:
    from sqlalchemy import text

    for attempt in range(6):  # Aurora scale-to-0 resumes on a cold call → retry a few times
        try:
            with engine.connect() as conn:
                return [dict(r) for r in conn.execute(text(sql)).mappings().all()]
        except Exception as exc:  # noqa: BLE001
            if "resuming" in str(exc).lower() and attempt < 5:
                time.sleep(8)
                continue
            raise
    return []


def read_data(engine) -> dict[str, list[dict]]:
    jobs = [_row_to_job(r) for r in _fetch(engine, _JOBS_SQL)]
    bronze = [_iso_row(r) for r in _fetch(engine, _BRONZE_SQL)]
    runs = [_iso_row(r) for r in _fetch(engine, _RUNS_SQL)]
    profile = [_profile_row(r) for r in _fetch(engine, _PROFILE_SQL)]
    events = [_event_row(r) for r in _fetch(engine, _EVENTS_SQL)]
    application_events = [_iso_row(r) for r in _fetch(engine, _APP_EVENTS_SQL)]
    return {"jobs": jobs, "bronze": bronze, "runs": runs, "profile": profile,
            "events": events, "application_events": application_events}


def _iso_row(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def _event_row(row: dict) -> dict:
    out = _iso_row(row)
    out["legitimacy_verified"] = _bool01(out.get("legitimacy_verified"))
    return out


def _profile_row(row: dict) -> dict:
    out = _iso_row(row)
    if "profile" in out:  # the JSONB profile blob → a compact JSON string
        out["profile"] = json.dumps(
            out["profile"] if isinstance(out["profile"], (dict, list)) else _as_list(out["profile"]),
            ensure_ascii=False,
        )
    return out


# --------------------------------------------------------------------------- summary (terminal)
def print_summary(data: dict[str, list[dict]]) -> None:
    jobs = data["jobs"]
    scored = [j for j in jobs if j.get("score") is not None]
    thr = (data["profile"][0]["threshold"] if data["profile"] and data["profile"][0].get("threshold")
           is not None else 60)
    cats: dict[str, int] = {}
    for j in scored:
        cats[j.get("fit_category") or "?"] = cats.get(j.get("fit_category") or "?", 0) + 1
    grads = [j for j in scored if j.get("previous_score") is not None
             and j["previous_score"] < thr <= (j.get("score") or 0)]
    print(f"\n  Snapshot: {len(data['bronze'])} bronze · {len(jobs)} silver · {len(scored)} scored"
          f" · threshold {thr}")
    print(f"  Fit categories: {cats}")
    print(f"  Graduated (prev < {thr} <= score): {len(grads)}")
    print("  Top 5 by score:")
    for j in scored[:5]:
        print(f"    [{j.get('score')}] {j.get('normalized_title')} @ {j.get('company')} "
              f"({j.get('country')})")


# --------------------------------------------------------------------------- db url + main
def _resolve_db_url() -> str:
    explicit = os.environ.get("JOBFETCHER_DB_URL")
    if explicit and explicit.strip():
        return explicit.strip()
    env = dict(os.environ)
    env.setdefault("DB_CLUSTER_ARN", _tf_output("aurora_cluster_arn"))
    env.setdefault("DB_SECRET_ARN", _tf_output("db_master_secret_arn"))
    env.setdefault("DB_NAME", os.environ.get("DB_NAME", "jobfetcher"))
    return resolve_db_url(env)  # reuse the handler's URL builder (the aurora_cluster_arn param, etc.)


def _tf_output(name: str) -> str:
    try:
        out = subprocess.run(
            ["terraform", f"-chdir={ROOT / 'terraform'}", "output", "-raw", name],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> None:
    ap = argparse.ArgumentParser(description="Export the DB to a filterable SQLite + CSV snapshot.")
    ap.add_argument("--out", default=str(ROOT / "export"), help="output dir (default: export/)")
    args = ap.parse_args()

    engine = make_engine(_resolve_db_url())
    print("reading the operational DB (Aurora may resume from idle — a few seconds)…")
    data = read_data(engine)
    sqlite_path, csv_path = write_snapshot(
        jobs=data["jobs"], bronze=data["bronze"], runs=data["runs"], profile=data["profile"],
        events=data["events"], application_events=data["application_events"],
        out_dir=Path(args.out),
    )
    print_summary(data)
    print(f"\n  wrote {sqlite_path}  +  {csv_path}")
    print(f"  filter/search it:  datasette {sqlite_path}   (or open the CSV in Excel/Sheets)")


if __name__ == "__main__":
    main()
