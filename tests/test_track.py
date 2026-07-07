"""Outcome-tracking unit tests (no DB): the track.py CLI's argparse validation (VG3/VG4 —
an invalid status or an out-of-range override never reaches the DB), the repository's own
loud validation of the same inputs (defense in depth, asserted to fire BEFORE the engine is
touched), the shared-vocabulary pin (migration 0005's FROZEN status literals ==
`APPLICATION_STATUSES`, read from the migration source — the one copy that can drift), and
the override-context fallbacks. The DB-backed behavior (rows written / rolled back) is in
the integration test."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from jobfetcher.adapters.repository_postgres import PostgresRepository
from jobfetcher.core.models import APPLICATION_STATUSES
from jobfetcher.core.ports import RepositoryError

# load the standalone script as a module (same harness as test_export.py)
_spec = importlib.util.spec_from_file_location(
    "track", Path(__file__).resolve().parents[1] / "scripts" / "track.py"
)
track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(track)


class _UntouchableEngine:
    """A sentinel engine: ANY attribute access fails the test — proving validation raised
    before a single DB call."""

    def __getattr__(self, name: str):
        raise AssertionError(f"engine must not be touched (accessed .{name})")


def _repo() -> PostgresRepository:
    return PostgresRepository.from_engine(_UntouchableEngine())


# --------------------------------------------------------------------------- argparse (VG3/VG4)
def test_every_allowed_status_is_a_subcommand():
    parser = track.build_parser()
    for status in APPLICATION_STATUSES:
        args = parser.parse_args([status, "p1", "--note", "spoke to recruiter"])
        assert args.command == status
        assert args.posting_id == "p1" and args.note == "spoke to recruiter"


def test_invalid_status_rejected_at_argparse():
    # VG3 negative: a made-up status is not a subcommand — argparse exits non-zero.
    with pytest.raises(SystemExit) as exc:
        track.build_parser().parse_args(["ghosted", "p1"])
    assert exc.value.code != 0


def test_override_range_rejected_at_argparse():
    # VG4 negative: 150 and -1 are rejected BEFORE any DB work; 0/75/100 parse (bounds in).
    parser = track.build_parser()
    for bad in ("150", "-1", "101", "abc"):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["override", "p1", bad])
        assert exc.value.code != 0
    for good in ("0", "75", "100"):
        assert parser.parse_args(["override", "p1", good]).score == int(good)


def test_unknown_posting_fails_loud_at_cli(monkeypatch, capsys):
    # VG2 (CLI level): the posting lookup comes back empty → stderr message + exit code 1,
    # and the repository (None here) is never reached — zero rows written by construction.
    monkeypatch.setattr(track, "_fetch", lambda *a, **k: [])
    with pytest.raises(SystemExit) as exc:
        track.cmd_status(object(), None, status="applied", posting_id="nope", note=None)
    assert exc.value.code == 1
    assert "no posting" in capsys.readouterr().err


# --------------------------------------------------------------------------- repository validation
def test_repository_rejects_invalid_status_before_touching_the_db():
    # VG3: the repository validates the same vocabulary independently of the CLI.
    with pytest.raises(RepositoryError, match="invalid status"):
        _repo().track_application_event(posting_id="p1", status="ghosted")


def test_repository_rejects_empty_posting_id():
    with pytest.raises(RepositoryError, match="non-empty posting_id"):
        _repo().track_application_event(posting_id="", status="applied")


def test_repository_rejects_out_of_range_override():
    # VG4: the repository validates 0-100 independently of the CLI (no DB constraint).
    for bad in (150, -1, 101):
        with pytest.raises(RepositoryError, match="0-100"):
            _repo().set_score_override(
                cluster_id="c1", score_override=bad, fit_category="strong_fit",
                profile_hash="ph", previous_score=None,
            )


def test_repository_rejects_empty_cluster_id():
    with pytest.raises(RepositoryError, match="non-empty cluster_id"):
        _repo().set_score_override(
            cluster_id="", score_override=75, fit_category="strong_fit",
            profile_hash="ph", previous_score=None,
        )


# --------------------------------------------------------------------------- shared vocabulary
def test_migration_0005_literals_match_the_shared_vocabulary():
    # The drift that can ACTUALLY happen: `db/tables.py` builds its CHECK from
    # `APPLICATION_STATUSES` (equal by construction — comparing those two proves nothing),
    # but migration 0005's literals are FROZEN in the file, as migrations must be. Pin them
    # to the tuple by reading the migration SOURCE — same members, same order, same count —
    # so an added-but-unmigrated status fails this suite instead of failing in production.
    import re

    source = (
        Path(__file__).resolve().parents[1]
        / "migrations" / "versions" / "0005_application_event.py"
    ).read_text(encoding="utf-8")
    check = re.search(r"status IN \(([^)]*)\)", source)
    assert check, "migration 0005 lost its status CHECK"
    literals = re.findall(r"'([^']*)'", check.group(1))
    assert literals == list(APPLICATION_STATUSES)


# --------------------------------------------------------------------------- override context
def test_profile_context_fallbacks():
    # NULL profile_hash → 'unknown'; NULL knobs → the documented defaults; set values win.
    hash_, thr, floor, band = track._profile_context(
        {"profile_hash": None, "threshold": None, "hard_floor": None, "near_miss_band": None}
    )
    assert (hash_, thr, floor, band) == ("unknown", 60, 50, 10)
    hash_, thr, floor, band = track._profile_context(
        {"profile_hash": "abc", "threshold": 70, "hard_floor": 40, "near_miss_band": 5}
    )
    assert (hash_, thr, floor, band) == ("abc", 70, 40, 5)


def test_profile_context_missing_row_is_loud():
    # negative: no profile row → RepositoryError (main() turns it into stderr + exit 1).
    with pytest.raises(RepositoryError, match="no profile row"):
        track._profile_context(None)
