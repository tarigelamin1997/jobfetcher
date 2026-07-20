"""The capture Lambda (INV-001 Rung 2) — a public AWS Lambda **Function URL** the digest/report
"Mark applied / interview / …" links hit. It records ONE application outcome via the existing
write path (`Repository.track_application_event`), closing the dark-feedback-loop: one click from
the inbox → a row in the append-only `application_event` log (migration 0005), where before the
only capture path was the CLI (`scripts/track.py`) and the outcome log stayed empty.

**Auth = the token, not the network.** The Function URL is `authorization_type = "NONE"` (public);
every request MUST carry a short-lived HMAC-signed token (`core/capture_token.py`) scoped to
exactly `{posting_id, status}` with a TTL. A stray/forged/expired click can never write:
`verify` runs BEFORE any DB touch, so a bad token returns 400 with ZERO rows written. The signing
key lives in Secrets Manager (env-var fallback for tests), mirroring the pipeline's secret reads.

Reuses, never reinvents: `resolve_db_url` + `configure_log_level` from the pipeline handler (the
same reuse `scripts/track.py` makes) and `wait_for_db_resume` for the Aurora scale-to-0 cold
resume. The capture Lambda ships in the SAME zip as the pipeline — a different handler entry point
(`jobfetcher.handlers.capture.handler`), 256 MB / ~30 s (it does one verify + one small write).

**Never echo the token or any secret** — no token value, no key, ever reaches a log line.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from html import escape
from typing import Any, Callable

from ..adapters.repository_postgres import PostgresRepository
from ..core.capture_token import CaptureTokenError, sign, verify
from ..core.ports import RepositoryError
from ..db.engine import wait_for_db_resume
from .pipeline import configure_log_level, resolve_db_url

log = logging.getLogger(__name__)

# Env the capture path reads. `CAPTURE_KEY` is the test/dev fallback (a raw key); in deployment
# the key comes from Secrets Manager under `$CAPTURE_KEY_SECRET_NAME`. `CAPTURE_BASE_URL` is the
# Function URL the pipeline stamps into every capture link (empty ⇒ links degrade off).
_CAPTURE_KEY_ENV = "CAPTURE_KEY"
_CAPTURE_KEY_SECRET_NAME_ENV = "CAPTURE_KEY_SECRET_NAME"
_CAPTURE_BASE_URL_ENV = "CAPTURE_BASE_URL"
_REGION_ENV = "AWS_REGION"  # Lambda sets this automatically at runtime
_DEFAULT_CAPTURE_SECRET_NAME = "jobfetcher/capture-token"
_DEFAULT_REGION = "us-east-1"

# Capture-link lifetime. A "Mark applied" link lives inside an email the user may only act on
# DAYS after it arrives (they apply, then remember to mark it), so a short TTL would break the
# real workflow. 30 days is a sane upper bound: the token is single-status AND posting-scoped, so
# the blast radius of a leaked/expired-but-replayed link is one specific outcome for one posting.
CAPTURE_TOKEN_TTL_S = 30 * 24 * 3600

_HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}
_MSG_INVALID = "This link is invalid or has expired."
_MSG_NOT_FOUND = "This job could not be found — nothing was recorded."
_MSG_ERROR = "Something went wrong. Please try again later."


# --------------------------------------------------------------------------- pure resolvers
def resolve_capture_base_url(env: dict[str, str]) -> str:
    """`$CAPTURE_BASE_URL` (the deployed Function URL) or `""` when unset — an empty base URL
    means capture links are not configured, so the digest/report render NONE (graceful). Pure."""
    return (env.get(_CAPTURE_BASE_URL_ENV) or "").strip()


def resolve_capture_secret_name(env: dict[str, str]) -> str:
    """`$CAPTURE_KEY_SECRET_NAME` or the documented default Secrets Manager id. Pure."""
    return (env.get(_CAPTURE_KEY_SECRET_NAME_ENV) or "").strip() or _DEFAULT_CAPTURE_SECRET_NAME


# --------------------------------------------------------------------------- key resolution (I/O)
# Warm-container cache for the Secrets-Manager-fetched signing key, keyed by secret id. The public
# Function URL resolves the key on every non-blank-token request, so caching bounds Secrets Manager
# reads (cost + throttling) under token spam to ONE per warm container. The env-var path (tests/dev)
# is intentionally NOT cached — each call reflects the current env. The key is Terraform-owned and
# unrotated; a rotation is picked up when the container recycles.
_SECRET_KEY_CACHE: dict[str, bytes] = {}


def _resolve_signing_key(env: dict[str, str]) -> bytes:
    """The HMAC signing key as bytes: `$CAPTURE_KEY` (tests/dev) wins, else Secrets Manager
    (`$CAPTURE_KEY_SECRET_NAME`, cached per warm container). Accepts a raw secret string or a JSON
    blob `{"key": "..."}` / `{"api_key": "..."}` — mirrors `llm_openai._resolve_api_key`. Never logged."""
    raw = (env.get(_CAPTURE_KEY_ENV) or "").strip()
    if raw:
        return raw.encode("utf-8")

    secret_name = resolve_capture_secret_name(env)
    cached = _SECRET_KEY_CACHE.get(secret_name)
    if cached is not None:
        return cached

    import boto3  # lazy: the env-var path (tests) needs no AWS SDK

    region = (env.get(_REGION_ENV) or "").strip() or _DEFAULT_REGION
    blob = (
        boto3.client("secretsmanager", region_name=region)
        .get_secret_value(SecretId=secret_name)
        .get("SecretString")
        or ""
    ).strip()
    try:
        data = json.loads(blob)
        value = str(data.get("key") or data.get("api_key") or "").strip() or blob
    except (json.JSONDecodeError, AttributeError, TypeError):
        value = blob  # not JSON → the whole secret string is the key
    key = value.encode("utf-8")
    if key:  # never cache an empty/misconfigured key — let a corrected secret be picked up
        _SECRET_KEY_CACHE[secret_name] = key
    return key


def build_capture_link(env: dict[str, str]) -> Callable[[str, str], str | None] | None:
    """Build the `capture_link(posting_id, status) -> url | None` callable the notifier/report
    inject, or `None` when capture is not configured (no base URL, or no resolvable key). The key
    is resolved ONCE here (one Secrets Manager read per run), then reused to sign every link.

    Fully guarded (an enhancement, NEVER a run-fatal path — the v0.10.0 report-guard stance): a
    missing base URL or an unreadable key logs a warning and returns `None`, so the digest simply
    renders without capture links; per-link signing failures also degrade to `None`."""
    base_url = resolve_capture_base_url(env)
    if not base_url:
        return None
    try:
        key = _resolve_signing_key(env)
    except Exception as exc:  # noqa: BLE001 — capture links must never fail the run
        log.warning("capture links disabled — signing key unavailable: %s", exc)
        return None
    if not key:
        log.warning("capture links disabled — empty signing key")
        return None
    prefix = base_url.rstrip("/")

    def _link(posting_id: str, status: str) -> str | None:
        try:
            token = sign(
                posting_id=posting_id,
                status=status,
                expires_at=int(time.time()) + CAPTURE_TOKEN_TTL_S,
                key=key,
            )
            return f"{prefix}?t={urllib.parse.quote(token, safe='')}"
        except Exception:  # noqa: BLE001 — a per-link failure just omits that one link
            return None

    return _link


# --------------------------------------------------------------------------- request parsing
def _read_token(event: Any) -> str:
    """Pull the token out of the Function URL request (payload v2.0). The happy path is
    `event["queryStringParameters"]["t"]` (already URL-decoded); fall back to parsing
    `rawQueryString`. Defensive about missing/blank/non-dict shapes → `""` (the handler 400s)."""
    if not isinstance(event, dict):
        return ""
    qs = event.get("queryStringParameters")
    if isinstance(qs, dict):
        t = qs.get("t")
        if isinstance(t, str) and t.strip():
            return t.strip()
    raw = event.get("rawQueryString")
    if isinstance(raw, str) and raw:
        values = urllib.parse.parse_qs(raw).get("t")
        if values and isinstance(values[0], str) and values[0].strip():
            return values[0].strip()
    return ""


def _html(status_code: int, message: str) -> dict[str, Any]:
    """A tiny self-contained HTML response (the click lands in a browser tab)."""
    body = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>JobFetcher</title></head>"
        "<body style=\"font-family:Arial,Helvetica,sans-serif;background:#f4f5f7;"
        "color:#202124;text-align:center;padding:48px 16px;\">"
        f"<p style=\"font-size:18px;\">{escape(message)}</p>"
        "</body></html>"
    )
    return {"statusCode": status_code, "headers": dict(_HTML_HEADERS), "body": body}


def _ok_html(status: str) -> dict[str, Any]:
    return _html(200, f"✅ Recorded: {status}. You can close this tab.")


# --------------------------------------------------------------------------- handler
def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:  # noqa: ARG001
    """The capture Function URL entry point. Verify the token, then record the outcome.

    - missing/blank token → 400 (nothing written)
    - forged / tampered / expired token, or a bad status → 400 (nothing written — `verify` runs
      before any DB touch)
    - a valid token whose posting is unknown → 404 (`track_application_event` rolls back → zero
      rows) — a real backend error also surfaces here as a 4xx, never a silent success
    - a valid token → exactly ONE `track_application_event` write → 200 HTML

    Never echoes the token or any secret; a rejected token logs only that it was rejected."""
    configure_log_level(os.environ)
    env = dict(os.environ)

    token = _read_token(event)
    if not token:
        log.info("capture: missing/blank token — 400")
        return _html(400, _MSG_INVALID)

    try:
        key = _resolve_signing_key(env)
    except Exception:  # noqa: BLE001 — a key-read failure is a server misconfig, not the client's
        log.exception("capture: signing key unavailable — 500")
        return _html(500, _MSG_ERROR)
    if not key:
        log.error("capture: empty signing key — 500")
        return _html(500, _MSG_ERROR)

    try:
        claim = verify(token, key=key, now=int(time.time()))
    except CaptureTokenError as exc:
        # Never log the token — only that (and coarsely why) it was rejected.
        log.info("capture: rejected token (reason=%s) — 400", exc.reason)
        return _html(400, _MSG_INVALID)

    try:
        repo = PostgresRepository(resolve_db_url(env))
        # Aurora scale-to-0 (ERR-009): a click landing on an idle cluster waits out the resume
        # rather than dying on the first DB touch.
        wait_for_db_resume(repo.engine)
        repo.track_application_event(posting_id=claim.posting_id, status=claim.status)
    except RepositoryError as exc:
        # Unknown posting (rolled back → zero rows) or a backend error — a 4xx either way; the
        # posting_id/status are safe to log (they are not secrets), the token is not logged.
        log.warning("capture: not recorded (status=%s) — 404: %s", claim.status, exc)
        return _html(404, _MSG_NOT_FOUND)
    except Exception:  # noqa: BLE001 — any other failure is a server error, surfaced as 500
        log.exception("capture: unexpected failure — 500")
        return _html(500, _MSG_ERROR)

    log.info(
        "capture: recorded status=%s posting=%s — 200", claim.status, claim.posting_id
    )
    return _ok_html(claim.status)
