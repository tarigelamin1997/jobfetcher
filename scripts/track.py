#!/usr/bin/env python3
"""track.py — record what happens AFTER the digest: application outcomes + score overrides.

The pipeline scores jobs and emails a shortlist, but knew nothing about the outcome — did
Tarig apply, get an interview, an offer, a rejection? This CLI writes that trail into the
append-only `application_event` log (migration 0005) and records human score corrections
into `score.score_override` + the `score_event` lineage log (`scoring_model=
'human-override'` — overrides join the same history as LLM scorings; nothing is erased):

    python scripts/track.py find "data engineer" [--company acme]   # posting_id lookup
    python scripts/track.py applied <posting_id> [--note "..."]     # also: interview |
                                                                    # offer | rejected |
                                                                    # withdrawn
    python scripts/track.py events <posting_id>                     # the outcome trail
    python scripts/track.py override <posting_id> <score 0-100>     # human score correction

Same DB-URL resolution as `scripts/export.py` ($JOBFETCHER_DB_URL, else the deployed Aurora
stack via terraform outputs). All failures are LOUD: message to stderr, exit code 1, zero
rows written.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import text  # noqa: E402

from jobfetcher.adapters.repository_postgres import PostgresRepository  # noqa: E402
from jobfetcher.core.ingest import (  # noqa: E402
    DEFAULT_USER_ID,
    _DEFAULT_HARD_FLOOR,
    _DEFAULT_NEAR_MISS_BAND,
    _DEFAULT_THRESHOLD,
    derive_fit_category,
)
from jobfetcher.core.models import APPLICATION_STATUSES  # noqa: E402
from jobfetcher.core.ports import RepositoryError  # noqa: E402
from jobfetcher.db.engine import make_engine  # noqa: E402
from jobfetcher.handlers.pipeline import resolve_db_url  # noqa: E402 (reuse the URL builder)

# Threshold-knob fallbacks when the profile row leaves one NULL — IMPORTED from
# core/ingest.py (the single definition of the documented defaults, 02-architecture
# "Threshold"), never a re-hardcoded copy. The names are module-private by underscore, but
# a first-party script duplicating the literals would be the worse sin (drift on a knob
# change). The row's values win whenever set.
_DEFAULT_KNOBS = {
    "threshold": _DEFAULT_THRESHOLD,
    "hard_floor": _DEFAULT_HARD_FLOOR,
    "near_miss_band": _DEFAULT_NEAR_MISS_BAND,
}


# --------------------------------------------------------------------------- pure helpers
def _score_0_100(value: str) -> int:
    """argparse type: an int in 0-100 — an out-of-range override is rejected BEFORE any DB
    work (the repository validates the same range again; no DB constraint by design)."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"score must be an integer, got {value!r}") from None
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError(f"score must be 0-100, got {n}")
    return n


def _profile_context(prof: dict[str, Any] | None) -> tuple[str, int, int, int]:
    """The override's scoring context from the profile row: `(profile_hash, threshold,
    hard_floor, near_miss_band)`. A NULL `profile_hash` falls back to `'unknown'` (the
    lineage row must never be blocked by a pre-0004 profile row); NULL knobs fall back to
    the documented defaults. A MISSING profile row is the caller's loud failure."""
    if prof is None:
        raise RepositoryError(
            "no profile row — run the pipeline once so the profile is seeded"
        )
    hash_ = prof.get("profile_hash") or "unknown"
    knobs = {
        k: (prof[k] if prof.get(k) is not None else default)
        for k, default in _DEFAULT_KNOBS.items()
    }
    return hash_, knobs["threshold"], knobs["hard_floor"], knobs["near_miss_band"]


def build_parser() -> argparse.ArgumentParser:
    """One subcommand per allowed status (generated from `APPLICATION_STATUSES` — the CLI
    can never accept a status the DB CHECK would reject) + find/events/override."""
    ap = argparse.ArgumentParser(
        description="Track application outcomes + human score overrides (append-only)."
    )
    sub = ap.add_subparsers(dest="command", required=True)
    for status in APPLICATION_STATUSES:
        sp = sub.add_parser(status, help=f"record '{status}' for a posting")
        sp.add_argument("posting_id")
        sp.add_argument("--note", default=None, help="optional free-text note")
    f = sub.add_parser("find", help="find posting ids by title/company substring")
    f.add_argument("query")
    f.add_argument("--company", default=None, help="additionally filter by company substring")
    e = sub.add_parser("events", help="list a posting's application events (newest first)")
    e.add_argument("posting_id")
    o = sub.add_parser("override", help="record a human score override (0-100)")
    o.add_argument("posting_id")
    o.add_argument("score", type=_score_0_100)
    return ap


# --------------------------------------------------------------------------- DB reads
def _fetch(engine, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    for attempt in range(6):  # Aurora scale-to-0 resumes on a cold call → retry a few times
        try:
            with engine.connect() as conn:
                return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
        except Exception as exc:  # noqa: BLE001
            if "resuming" in str(exc).lower() and attempt < 5:
                time.sleep(8)
                continue
            raise
    return []


def _posting_row(engine, posting_id: str) -> dict | None:
    rows = _fetch(
        engine,
        "SELECT posting_id, cluster_id, title, normalized_title, company "
        "FROM posting WHERE posting_id = :p",
        {"p": posting_id},
    )
    return rows[0] if rows else None


def _fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _label(row: dict) -> str:
    title = row.get("normalized_title") or row.get("title") or "?"
    company = row.get("company") or "?"
    return f"{title} — {company}"


# --------------------------------------------------------------------------- commands
def cmd_status(engine, repo, *, status: str, posting_id: str, note: str | None) -> None:
    row = _posting_row(engine, posting_id)
    if row is None:
        _fail(
            f"no posting {posting_id!r} — nothing written. "
            'Find the id with: python scripts/track.py find "<title substring>"'
        )
    repo.track_application_event(posting_id=posting_id, status=status, note=note)
    suffix = f'  (note: "{note}")' if note else ""
    print(f"  {status} recorded for {posting_id}: {_label(row)}{suffix}")


def cmd_find(engine, *, query: str, company: str | None) -> None:
    sql = (
        "SELECT p.posting_id, s.score, "
        "COALESCE(p.normalized_title, p.title) AS title, p.company "
        "FROM posting p LEFT JOIN score s ON p.cluster_id = s.cluster_id "
        "WHERE (p.normalized_title ILIKE :q OR p.title ILIKE :q OR p.company ILIKE :q)"
    )
    params: dict[str, Any] = {"q": f"%{query}%"}
    if company:
        sql += " AND p.company ILIKE :c"
        params["c"] = f"%{company}%"
    sql += " ORDER BY s.score DESC NULLS LAST, p.posting_id LIMIT 10"
    rows = _fetch(engine, sql, params)
    if not rows:
        print(f"  no postings match {query!r}")
        return
    for r in rows:  # posting_id first, so it is copy-pasteable into the other subcommands
        score = r["score"] if r["score"] is not None else "-"
        print(f"  {r['posting_id']}  {score}  {r['title']} — {r['company'] or '?'}")


def cmd_events(engine, *, posting_id: str) -> None:
    row = _posting_row(engine, posting_id)
    if row is None:
        _fail(f"no posting {posting_id!r}")
    events = _fetch(
        engine,
        "SELECT status, noted_at, note FROM application_event "
        "WHERE posting_id = :p ORDER BY noted_at DESC, event_id DESC",
        {"p": posting_id},
    )
    print(f"  {posting_id}: {_label(row)}")
    if not events:
        print("  no application events yet")
        return
    for ev in events:
        note = f'  — "{ev["note"]}"' if ev["note"] else ""
        print(f"  {ev['noted_at']}  {ev['status']}{note}")


def apply_override(engine, repo, *, posting_id: str, score: int) -> dict[str, Any]:
    """The pure override path — reused by the `override` CLI command AND the control panel
    (scripts/panel.py). Looks up the posting → its cluster + current score → the profile's
    scoring context → derives `fit_category` from the runtime knobs (VG8) → `set_score_override`
    (updates `score.score_override` + appends a `human-override` `score_event`, ADR-0026).

    Raises `RepositoryError` on any precondition failure (no posting / no cluster / no score /
    no profile row) — the caller decides how to surface it (the CLI `main()` turns it into a
    stderr message + exit 1; the panel shows it in the UI). Returns a small result dict to render."""
    row = _posting_row(engine, posting_id)
    if row is None:
        raise RepositoryError(f"no posting {posting_id!r} — nothing written")
    if row["cluster_id"] is None:
        raise RepositoryError(
            f"posting {posting_id!r} has no cluster yet — only scored postings can be overridden"
        )
    score_rows = _fetch(
        engine, "SELECT score FROM score WHERE cluster_id = :c", {"c": row["cluster_id"]}
    )
    if not score_rows:
        raise RepositoryError(f"posting {posting_id!r} has no score row yet — nothing to override")
    previous_score = score_rows[0]["score"]
    prof_rows = _fetch(
        engine,
        "SELECT profile_hash, threshold, hard_floor, near_miss_band "
        "FROM profile WHERE user_id = :u",
        {"u": DEFAULT_USER_ID},
    )
    profile_hash, threshold, hard_floor, near_miss_band = _profile_context(
        prof_rows[0] if prof_rows else None
    )
    # The band routing stays in code, from the runtime knobs (VG8) — same as LLM scorings.
    fit_category = derive_fit_category(
        score, threshold=threshold, hard_floor=hard_floor, near_miss_band=near_miss_band
    )
    repo.set_score_override(
        cluster_id=row["cluster_id"],
        score_override=score,
        fit_category=fit_category,
        profile_hash=profile_hash,
        previous_score=previous_score,
    )
    return {
        "posting_id": posting_id,
        "label": _label(row),
        "score": score,
        "fit_category": fit_category,
        "previous_score": previous_score,
    }


def cmd_override(engine, repo, *, posting_id: str, score: int) -> None:
    # RepositoryError (a precondition failure or a DB error) propagates to main()'s handler.
    r = apply_override(engine, repo, posting_id=posting_id, score=score)
    print(
        f"  override {r['score']} ({r['fit_category']}) recorded for {r['posting_id']}: "
        f"{r['label']}  (was {r['previous_score']})"
    )


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
    args = build_parser().parse_args()
    engine = make_engine(_resolve_db_url())
    repo = PostgresRepository.from_engine(engine)
    try:
        if args.command in APPLICATION_STATUSES:
            cmd_status(
                engine, repo, status=args.command, posting_id=args.posting_id, note=args.note
            )
        elif args.command == "find":
            cmd_find(engine, query=args.query, company=args.company)
        elif args.command == "events":
            cmd_events(engine, posting_id=args.posting_id)
        else:  # override
            cmd_override(engine, repo, posting_id=args.posting_id, score=args.score)
    except RepositoryError as e:
        _fail(str(e))


if __name__ == "__main__":
    main()
