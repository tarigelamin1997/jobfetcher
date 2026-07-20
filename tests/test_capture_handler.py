"""Unit tests for the capture Lambda (INV-001 Rung 2) — no DB, no AWS: the repository is faked
and the signing key comes from `$CAPTURE_KEY`. Mirrors the dossier's VG-a/VG-b at the handler
boundary: a valid token writes EXACTLY ONE outcome + returns 200 HTML; a forged/expired/missing
token writes NOTHING + returns 4xx (the repo is never even constructed); an unknown posting
(RepositoryError) surfaces as 4xx. Also covers the `build_capture_link` factory the pipeline uses
to mint links, and the graceful-degrade (no base URL / no key → None)."""
from __future__ import annotations

import time
import urllib.parse

import pytest

import jobfetcher.handlers.capture as capture
from jobfetcher.core.capture_token import sign, verify
from jobfetcher.core.ports import RepositoryError

_KEY_STR = "unit-test-signing-key"
_KEY = _KEY_STR.encode()


def _token(pid: str = "jsearch:123", status: str = "applied", ttl: int = 3600) -> str:
    return sign(posting_id=pid, status=status, expires_at=int(time.time()) + ttl, key=_KEY)


@pytest.fixture
def wired(monkeypatch):
    """Fake the repo + no-op the Aurora resume wait; supply the key + a DB URL via env. `state`
    exposes every constructed repo (to prove zero/one write) and lets a test arm a raise."""
    state: dict = {"repos": [], "raise_exc": None}

    class _FakeRepo:
        def __init__(self, url):
            self.url = url
            self.engine = object()
            self.calls: list[tuple[str, str]] = []
            state["repos"].append(self)

        def track_application_event(self, *, posting_id, status, note=None):
            self.calls.append((posting_id, status))
            if state["raise_exc"] is not None:
                raise state["raise_exc"]

    monkeypatch.setattr(capture, "PostgresRepository", _FakeRepo)
    monkeypatch.setattr(capture, "wait_for_db_resume", lambda engine, **kw: None)
    monkeypatch.setenv("CAPTURE_KEY", _KEY_STR)
    monkeypatch.setenv("JOBFETCHER_DB_URL", "postgresql://u:p@localhost/db")
    return state


# --------------------------------------------------------------------------- VG-a positive
def test_valid_token_records_exactly_once_and_200(wired):
    token = _token()
    resp = capture.handler({"queryStringParameters": {"t": token}})
    assert resp["statusCode"] == 200
    assert resp["headers"]["content-type"] == "text/html; charset=utf-8"
    assert "Recorded" in resp["body"] and "applied" in resp["body"]
    # exactly ONE write, for the right posting + status
    assert len(wired["repos"]) == 1
    assert wired["repos"][0].calls == [("jsearch:123", "applied")]
    # the response never echoes the token
    assert token not in resp["body"]


def test_raw_querystring_fallback_records(wired):
    # some Function URL payload shapes carry only rawQueryString — the handler still finds `t`
    resp = capture.handler({"rawQueryString": f"t={_token()}"})
    assert resp["statusCode"] == 200
    assert wired["repos"][0].calls == [("jsearch:123", "applied")]


def test_later_status_records_a_distinct_outcome(wired):
    # VG-b: a second, later status for the same posting is its own recorded outcome (the
    # append-only log preserves history — the repo never overwrites)
    resp = capture.handler({"queryStringParameters": {"t": _token(status="interview")}})
    assert resp["statusCode"] == 200
    assert wired["repos"][0].calls == [("jsearch:123", "interview")]


# --------------------------------------------------------------------------- VG-a negatives
def test_forged_token_writes_nothing_and_4xx(wired):
    token = _token()
    payload_b64, sig_b64 = token.split(".")
    forged = f"{payload_b64}.{('A' if sig_b64[0] != 'A' else 'B') + sig_b64[1:]}"
    resp = capture.handler({"queryStringParameters": {"t": forged}})
    assert resp["statusCode"] == 400
    assert wired["repos"] == []  # repo never constructed → ZERO writes on a bad token


def test_expired_token_writes_nothing_and_4xx(wired):
    resp = capture.handler({"queryStringParameters": {"t": _token(ttl=-10)}})
    assert resp["statusCode"] == 400
    assert wired["repos"] == []


@pytest.mark.parametrize(
    "event",
    [
        None,
        {},
        {"queryStringParameters": None},
        {"queryStringParameters": {}},
        {"queryStringParameters": {"t": "   "}},
        {"rawQueryString": ""},
    ],
)
def test_missing_or_blank_token_4xx_no_write(wired, event):
    resp = capture.handler(event)
    assert resp["statusCode"] == 400
    assert wired["repos"] == []


def test_unknown_posting_returns_4xx(wired):
    # the repo rejects an unknown posting with RepositoryError (zero rows — its own contract);
    # the handler surfaces that as a 4xx, never a 200
    wired["raise_exc"] = RepositoryError("no posting 'jsearch:nope' — nothing written")
    resp = capture.handler({"queryStringParameters": {"t": _token(pid="jsearch:nope")}})
    assert resp["statusCode"] == 404
    assert len(wired["repos"]) == 1  # attempted exactly once (no retry), then rolled back


def test_empty_signing_key_is_server_error_no_write(wired, monkeypatch):
    # a misconfigured (empty) key is a 500 — a server problem, not the client's — and never writes
    monkeypatch.setattr(capture, "_resolve_signing_key", lambda env: b"")
    resp = capture.handler({"queryStringParameters": {"t": _token()}})
    assert resp["statusCode"] == 500
    assert wired["repos"] == []


# --------------------------------------------------------------------------- build_capture_link
def test_resolve_capture_base_url():
    assert capture.resolve_capture_base_url({}) == ""
    assert capture.resolve_capture_base_url({"CAPTURE_BASE_URL": " https://c/ "}) == "https://c/"


def test_build_capture_link_mints_verifiable_url():
    env = {"CAPTURE_BASE_URL": "https://cap.example.com/c", "CAPTURE_KEY": _KEY_STR}
    link = capture.build_capture_link(env)
    assert link is not None
    url = link("jsearch:9", "applied")
    assert url.startswith("https://cap.example.com/c?t=")
    token = urllib.parse.unquote(url.split("t=", 1)[1])
    claim = verify(token, key=_KEY, now=int(time.time()))
    assert claim.posting_id == "jsearch:9" and claim.status == "applied"


def test_build_capture_link_none_without_base_url():
    # graceful degrade: no base URL configured → no capture links at all
    assert capture.build_capture_link({"CAPTURE_KEY": _KEY_STR}) is None


def test_build_capture_link_none_without_key(monkeypatch):
    monkeypatch.setattr(capture, "_resolve_signing_key", lambda env: b"")
    assert capture.build_capture_link({"CAPTURE_BASE_URL": "https://c"}) is None
