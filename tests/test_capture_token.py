"""Unit tests for the pure HMAC capture token (INV-001 Rung 2): sign→verify round-trip, and
every rejection path — tampered payload/signature, expired, wrong key, and an out-of-vocabulary
status — each raising the typed `CaptureTokenError` with a GENERIC message. No I/O: the key is a
plain `bytes`, `now`/`exp` are plain ints."""
from __future__ import annotations

import base64
import json

import pytest

from jobfetcher.core.capture_token import (
    CaptureClaim,
    CaptureTokenError,
    sign,
    verify,
)

_KEY = b"a-test-signing-key-0123456789"
_NOW = 1_700_000_000
_FUTURE = _NOW + 3600
_PAST = _NOW - 1


# --------------------------------------------------------------------------- round-trip
def test_sign_verify_round_trip():
    token = sign(posting_id="jsearch:abc123", status="applied", expires_at=_FUTURE, key=_KEY)
    claim = verify(token, key=_KEY, now=_NOW)
    assert claim == CaptureClaim(posting_id="jsearch:abc123", status="applied")


def test_posting_id_with_special_chars_round_trips():
    # a posting_id with ':' and '/' must survive — JSON encodes it, no delimiter to escape
    pid = "jsearch:https://boards.example.com/a:b/c?d=1"
    token = sign(posting_id=pid, status="interview", expires_at=_FUTURE, key=_KEY)
    claim = verify(token, key=_KEY, now=_NOW)
    assert claim.posting_id == pid and claim.status == "interview"


def test_token_is_two_base64url_segments():
    token = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    assert token.count(".") == 1
    # both halves are URL-safe base64 (no '+' or '/' or '=' padding)
    for seg in token.split("."):
        assert "+" not in seg and "/" not in seg and "=" not in seg


# --------------------------------------------------------------------------- negatives
def test_tampered_payload_is_rejected():
    token = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    payload_b64, sig_b64 = token.split(".")
    # forge the payload to a different posting_id, keep the original signature
    forged_payload = {"pid": "p-evil", "st": "offer", "exp": _FUTURE}
    forged_b64 = (
        base64.urlsafe_b64encode(json.dumps(forged_payload).encode()).rstrip(b"=").decode()
    )
    with pytest.raises(CaptureTokenError):
        verify(f"{forged_b64}.{sig_b64}", key=_KEY, now=_NOW)


def test_tampered_signature_is_rejected():
    token = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    payload_b64, sig_b64 = token.split(".")
    bad_sig = ("A" if sig_b64[0] != "A" else "B") + sig_b64[1:]
    with pytest.raises(CaptureTokenError):
        verify(f"{payload_b64}.{bad_sig}", key=_KEY, now=_NOW)


def test_wrong_key_is_rejected():
    token = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    with pytest.raises(CaptureTokenError):
        verify(token, key=b"a-different-key", now=_NOW)


def test_expired_token_is_rejected():
    token = sign(posting_id="p", status="applied", expires_at=_PAST, key=_KEY)
    with pytest.raises(CaptureTokenError) as exc:
        verify(token, key=_KEY, now=_NOW)
    assert exc.value.reason == "expired"
    # the public message never says which check failed
    assert "expired" in str(exc.value) or "invalid" in str(exc.value)
    assert "signature" not in str(exc.value)


def test_status_outside_vocabulary_is_rejected():
    # a validly-SIGNED token (right key) carrying a bogus status is still refused by verify —
    # it can never reach the DB CHECK
    token = sign(posting_id="p", status="hired", expires_at=_FUTURE, key=_KEY)
    with pytest.raises(CaptureTokenError) as exc:
        verify(token, key=_KEY, now=_NOW)
    assert exc.value.reason == "status"


@pytest.mark.parametrize("bad", ["", "no-dot-here", "a.b.c", "!!!.@@@", "onlyonesegment."])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(CaptureTokenError):
        verify(bad, key=_KEY, now=_NOW)


def test_generic_message_does_not_leak_which_check_failed():
    # bad signature and expired both surface the SAME generic message (no oracle for a prober)
    good = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    payload_b64, sig_b64 = good.split(".")
    bad_sig_token = f"{payload_b64}.{('A' if sig_b64[0] != 'A' else 'B') + sig_b64[1:]}"
    expired_token = sign(posting_id="p", status="applied", expires_at=_PAST, key=_KEY)
    with pytest.raises(CaptureTokenError) as e_sig:
        verify(bad_sig_token, key=_KEY, now=_NOW)
    with pytest.raises(CaptureTokenError) as e_exp:
        verify(expired_token, key=_KEY, now=_NOW)
    assert str(e_sig.value) == str(e_exp.value)  # identical public message


def test_sign_rejects_empty_key():
    with pytest.raises(CaptureTokenError):
        sign(posting_id="p", status="applied", expires_at=_FUTURE, key=b"")


def test_verify_rejects_empty_key():
    # Symmetry with sign: an empty key is a misconfiguration, never a basis for trust — verify
    # refuses it up front rather than HMAC-ing against an empty secret.
    token = sign(posting_id="p", status="applied", expires_at=_FUTURE, key=_KEY)
    with pytest.raises(CaptureTokenError):
        verify(token, key=b"", now=_NOW)
