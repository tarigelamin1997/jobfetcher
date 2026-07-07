"""Unit tests for the export snapshot (no DB): the JSONB→text transforms (robust to both a
Python list and a JSON string, as the local psycopg2 vs Aurora Data API dialects return) and
the SQLite/CSV writer (given in-memory rows → the four tables + the flat CSV). The DB read is
covered by the integration test."""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

# load the standalone script as a module
_spec = importlib.util.spec_from_file_location(
    "export", Path(__file__).resolve().parents[1] / "scripts" / "export.py"
)
export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export)


# --------------------------------------------------------------------------- transforms
def test_skills_text_from_list_and_json_string():
    # local psycopg2 returns a list; the Aurora Data API returns a JSON string — both → text
    as_list = [{"name": "Python", "level": "must"}, {"name": "SQL"}, {"name": ""}]
    assert export.skills_text(as_list) == "Python, SQL"
    assert export.skills_text('[{"name": "Airflow"}]') == "Airflow"
    assert export.skills_text(None) == ""


def test_list_text_and_bool01():
    assert export.list_text(["strong python", "fintech bg"]) == "strong python\nfintech bg"
    assert export.list_text('["a", "b"]') == "a\nb"
    assert export.list_text(None) == ""
    assert export._bool01(True) == 1 and export._bool01(False) == 0 and export._bool01(None) is None


def test_as_list_handles_garbage_string():
    # a non-JSON string must not crash the export — it degrades to []
    assert export._as_list("not json") == []


def test_row_to_job_flattens_jsonb_and_timestamps():
    from datetime import datetime, timezone

    row = {
        "posting_id": "p1", "score": 85, "previous_score": 45, "fit_category": "strong_fit",
        "skills": [{"name": "Python"}, {"name": "SQL"}],
        "strengths": ["strong python"], "gaps": ["no spark"],
        "legitimacy_verified": True,
        "scored_at": datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc),
    }
    job = export._row_to_job(row)
    assert job["skills"] == "Python, SQL"
    assert '"name": "Python"' in job["skills_json"]  # raw JSON kept for Datasette
    assert job["strengths"] == "strong python" and job["gaps"] == "no spark"
    assert job["legitimacy_verified"] == 1
    assert job["scored_at"] == "2026-07-06T12:00:00+00:00"  # timestamp → ISO string


# --------------------------------------------------------------------------- writer
def test_write_snapshot_creates_all_tables_and_csv(tmp_path):
    jobs = [
        {"posting_id": "p1", "normalized_title": "Data Engineer", "company": "Acme",
         "country": "sa", "status": "scored", "score": 85, "score_override": 75,
         "previous_score": 45, "fit_category": "strong_fit", "skills": "Python, SQL",
         "latest_application_status": "interview",
         "application_noted_at": "2026-07-07T09:00:00+00:00", "apply_url": "http://x"},
        {"posting_id": "p2", "normalized_title": "Data Architect", "company": "Beta",
         "country": "ae", "status": "silver", "score": None, "score_override": None,
         "previous_score": None, "fit_category": None, "skills": "",
         "latest_application_status": None, "application_noted_at": None,
         "apply_url": "http://y"},
    ]
    sp, cp = export.write_snapshot(
        jobs=jobs,
        bronze=[{"bronze_id": "b1", "run_id": "r1"}],
        runs=[{"run_date": "2026-07-06", "run_id": "r1"}],
        profile=[{"user_id": "default", "threshold": 60, "profile": "{}"}],
        events=[{"event_id": 1, "cluster_id": "p1", "score": 45, "previous_score": None,
                 "scoring_model": "pre-0004", "profile_hash": "pre-0004", "run_id": None},
                {"event_id": 2, "cluster_id": "p1", "score": 85, "previous_score": 45,
                 "scoring_model": "deepseek-v4-pro", "profile_hash": "abc123", "run_id": "r1"}],
        application_events=[
            {"event_id": 1, "posting_id": "p1", "status": "applied",
             "noted_at": "2026-07-06T09:00:00+00:00", "note": None},
            {"event_id": 2, "posting_id": "p1", "status": "interview",
             "noted_at": "2026-07-07T09:00:00+00:00", "note": "phone screen"},
        ],
        out_dir=tmp_path,
    )
    con = sqlite3.connect(sp)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "bronze", "runs", "profile_current", "score_events",
            "application_events"} <= tables
    rows = con.execute("SELECT posting_id, score, fit_category FROM jobs ORDER BY posting_id").fetchall()
    assert rows == [("p1", 85, "strong_fit"), ("p2", None, None)]  # scored + un-scored silver both present
    # VG6: the flat jobs table carries the new outcome columns (populated + NULL both land)
    outcome = con.execute(
        "SELECT score_override, latest_application_status, application_noted_at "
        "FROM jobs ORDER BY posting_id"
    ).fetchall()
    assert outcome == [(75, "interview", "2026-07-07T09:00:00+00:00"), (None, None, None)]
    # the score history rides along: both events for p1, in order, with their lineage
    ev = con.execute(
        "SELECT score, previous_score, profile_hash FROM score_events ORDER BY event_id"
    ).fetchall()
    assert ev == [(45, None, "pre-0004"), (85, 45, "abc123")]
    # VG6: the application-outcome trail rides along too (both rows, in order)
    app_ev = con.execute(
        "SELECT posting_id, status, note FROM application_events ORDER BY event_id"
    ).fetchall()
    assert app_ev == [("p1", "applied", None), ("p1", "interview", "phone screen")]
    # the flat CSV exists + has a header (incl. the new columns) + both rows
    text = cp.read_text(encoding="utf-8")
    header = text.splitlines()[0]
    assert "posting_id" in header
    assert "score_override" in header and "latest_application_status" in header
    assert len(text.strip().splitlines()) == 3  # header + 2 jobs


def test_write_snapshot_empty_is_safe(tmp_path):
    # negative: an empty DB must not crash — the tables exist, the CSV is empty
    sp, cp = export.write_snapshot(
        jobs=[], bronze=[], runs=[], profile=[], events=[], application_events=[],
        out_dir=tmp_path,
    )
    assert sp.exists() and cp.exists()
    con = sqlite3.connect(sp)
    assert con.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_event_row_normalizes_bool_and_timestamps():
    from datetime import datetime, timezone

    row = {"event_id": 7, "legitimacy_verified": True,
           "scored_at": datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc)}
    out = export._event_row(row)
    assert out["legitimacy_verified"] == 1
    assert out["scored_at"] == "2026-07-07T09:00:00+00:00"
    assert export._event_row({"legitimacy_verified": None})["legitimacy_verified"] is None
