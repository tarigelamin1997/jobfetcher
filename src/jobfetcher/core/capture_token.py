"""HMAC-signed capture token (INV-001 Rung 2) — PURE: no I/O, no secret reading. The signing
key is passed in as `bytes`; the *caller* (the handler/ingest layer) owns fetching it from
Secrets Manager. Mirrors the v0.10.0 presigned-report pattern: a short-lived, tamper-evident
token scoped to exactly `{posting_id, status}` so a "Mark applied" link in an email can drive
ONE outcome write against a public endpoint without an unforgeable click being possible.

Wire format: `base64url(payload) + "." + base64url(hmac_sha256(payload, key))`, where `payload`
is compact, key-sorted JSON `{"pid": …, "st": …, "exp": <unix seconds>}`. JSON encodes the
fields, so a `posting_id` carrying `:` (or any character) round-trips losslessly — no ad-hoc
delimiter to escape. `exp` bounds the blast radius in time; the token is single-status and
posting-scoped, so even a leaked one can only re-assert one specific outcome for one posting.

`verify` is constant-time on the signature (`hmac.compare_digest`) and raises a typed
`CaptureTokenError` for EVERY rejection — bad/missing signature, expired, malformed/tampered,
or a status outside `APPLICATION_STATUSES` — with a GENERIC message (never leaking which check
failed to a probing client); a coarse `reason` code is attached for server-side logs only.
"""
from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass
from hashlib import sha256

from .models import APPLICATION_STATUSES


class CaptureTokenError(Exception):
    """A capture token could not be trusted — bad/missing signature, expired, malformed, or an
    out-of-vocabulary status. The public message is deliberately GENERIC (it never says which
    check failed, so a probing client learns nothing); `reason` is a short code for server-side
    logs ONLY, never surfaced to the caller."""

    def __init__(self, reason: str = "invalid") -> None:
        super().__init__("invalid or expired capture token")
        self.reason = reason


@dataclass(frozen=True)
class CaptureClaim:
    """The trusted contents of a verified token — exactly what the write path needs."""

    posting_id: str
    status: str


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 WITHOUT padding (so the token is one clean query-param value)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    """Inverse of `_b64url_encode` — re-add the stripped `=` padding before decoding."""
    return base64.urlsafe_b64decode(text + ("=" * (-len(text) % 4)))


def _sig(payload: bytes, key: bytes) -> bytes:
    return hmac.new(key, payload, sha256).digest()


def sign(*, posting_id: str, status: str, expires_at: int, key: bytes) -> str:
    """Sign a `{posting_id, status, exp}` claim into `b64url(payload).b64url(hmac)`.

    `expires_at` is unix seconds (the caller adds the TTL to `now`). `key` is the raw HMAC
    secret bytes. Pure — deterministic for a given `(payload, key)`. Raises `CaptureTokenError`
    on an empty key (a misconfiguration must not mint an unsigned-effectively token)."""
    if not key:
        raise CaptureTokenError("no-key")
    payload = json.dumps(
        {"pid": posting_id, "st": status, "exp": int(expires_at)},
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    return f"{_b64url_encode(payload)}.{_b64url_encode(_sig(payload, key))}"


def verify(token: str, *, key: bytes, now: int) -> CaptureClaim:
    """Return the `CaptureClaim` iff `token` is a well-formed, correctly-signed, unexpired token
    whose status is in `APPLICATION_STATUSES`. Otherwise raise `CaptureTokenError` (generic
    message, coarse `reason` for logs). The signature is checked in CONSTANT TIME
    (`hmac.compare_digest`) BEFORE the payload is trusted, so a tampered payload fails at the
    signature step; `now` is unix seconds (the caller passes the current time)."""
    if not isinstance(token, str) or token.count(".") != 1:
        raise CaptureTokenError("malformed")
    payload_b64, sig_b64 = token.split(".")
    try:
        payload = _b64url_decode(payload_b64)
        provided_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        raise CaptureTokenError("malformed") from None

    # Constant-time signature check FIRST — a tampered payload changes the digest and is
    # rejected here, before its contents are ever parsed or trusted.
    if not hmac.compare_digest(provided_sig, _sig(payload, key)):
        raise CaptureTokenError("signature")

    try:
        claim = json.loads(payload)
    except ValueError:
        raise CaptureTokenError("malformed") from None
    if not isinstance(claim, dict):
        raise CaptureTokenError("malformed")

    exp = claim.get("exp")
    pid = claim.get("pid")
    st = claim.get("st")
    # bool is a subclass of int — exclude it so `exp=True` isn't read as `1`.
    if not isinstance(exp, int) or isinstance(exp, bool):
        raise CaptureTokenError("malformed")
    if not isinstance(pid, str) or not pid:
        raise CaptureTokenError("malformed")
    if not isinstance(st, str):
        raise CaptureTokenError("malformed")
    if exp < now:
        raise CaptureTokenError("expired")
    # Defense in depth: `sign` is only ever called with a valid status, but a validly-signed
    # token carrying an out-of-vocabulary status is still refused (never reaches the DB CHECK).
    if st not in APPLICATION_STATUSES:
        raise CaptureTokenError("status")
    return CaptureClaim(posting_id=pid, status=st)
