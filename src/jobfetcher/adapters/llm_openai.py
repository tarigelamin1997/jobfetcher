"""OpenAI-compatible LLM client (ADR-0017) — the v0 transport; default provider DeepSeek.

One adapter serves *any* OpenAI-compatible host: the backend is config (`base_url` +
`model` + `api_key`). Structured output is left to the prompt + Pydantic, not a
provider-specific JSON mode, so this stays portable (ADR-0012). The key comes from
`$DEEPSEEK_API_KEY` (tests) or Secrets Manager (runtime) and is never logged.

Stdlib `urllib` only — no HTTP dependency.

Transient provider failures (429 / 5xx / connection errors) are retried with exponential
backoff + full jitter, per `LlmConfig.max_retries` (ERR-006: one DeepSeek 503 must not kill
a run). Auth and model-not-found errors always fail fast — retrying them is pure waste.

`read_balance()` (INV-005) is an optional capability of THIS adapter, not of the `LlmClient`
port: `/user/balance` is DeepSeek-specific, so the handler reaches it via `getattr` and a
client without it simply reports nothing (ADR-0012/0017).
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any, NamedTuple

from ..config import LlmConfig
from ..core.ports import LlmAuthError, LlmBillingError, LlmError, LlmModelNotFoundError

log = logging.getLogger(__name__)

_ENV_KEY = "DEEPSEEK_API_KEY"

# HTTP statuses worth retrying: rate limit + server-side/transient. Everything else 4xx is a
# request problem that a retry cannot fix.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# The balance read is best-effort and measured at 0.57–0.81 s (INV-005 Evidence 6): one try,
# a short timeout, and a failure costs the digest a forecast — never the run.
_BALANCE_TIMEOUT_S = 5.0


class LlmBalance(NamedTuple):
    """One provider balance reading. `usd is None` means UNKNOWN, and `error` says why, as a
    short code (`timeout`, `http_401`, `bad_json`, `no_usd`, `no_key`, `error:<ExceptionClass>`).

    The code is all a failure ever carries: DeepSeek's 401 body echoes the key's last four
    characters, so a response body or header must never reach a log line or the run summary."""

    usd: float | None
    available: bool | None
    error: str | None


def _resolve_api_key(config: LlmConfig) -> str:
    """Key from `$DEEPSEEK_API_KEY`, else Secrets Manager. Accepts a raw key or a JSON
    blob `{"api_key": "..."}`. Returns "" if nothing is found (the caller raises)."""
    env = os.environ.get(_ENV_KEY)
    if env and env.strip():
        return env.strip()
    import boto3  # lazy import: the env-var path needs no AWS SDK

    raw = (
        boto3.client("secretsmanager", region_name=config.aws_region)
        .get_secret_value(SecretId=config.secret_name)
        .get("SecretString")
        or ""
    ).strip()
    try:
        data = json.loads(raw)
        return str(data.get("api_key") or data.get("apiKey") or "").strip() or raw
    except (json.JSONDecodeError, AttributeError):
        return raw  # not JSON -> the whole secret string is the key


class OpenAICompatLlmClient:
    """`LlmClient` over an OpenAI-compatible `/chat/completions` endpoint."""

    def __init__(self, config: LlmConfig | None = None, *, api_key: str | None = None) -> None:
        self.config = config or LlmConfig()
        self._api_key = api_key  # resolved lazily on first call
        self._key_lock = threading.Lock()  # H-2: first call may race across worker threads

    def _key(self) -> str:
        if not self._api_key:
            with self._key_lock:
                if not self._api_key:  # double-check under the lock
                    self._api_key = _resolve_api_key(self.config)
        if not self._api_key:
            raise LlmAuthError(
                f"no API key found (env ${_ENV_KEY} or Secrets Manager '{self.config.secret_name}')"
            )
        return self._api_key

    def complete(self, *, system: str, user: str) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        # Reasoning control (ERR-010 follow-up). Only ONE of these is ever sent: disabling
        # thinking makes an effort level meaningless, and sending both invites a 400 from a
        # provider that validates the combination. Both keys are omitted entirely on the
        # default config, so a non-reasoning OpenAI-compatible host sees the same body it
        # always did.
        if not self.config.reasoning:
            payload["thinking"] = {"type": "disabled"}
        elif self.config.reasoning_effort is not None:
            payload["reasoning_effort"] = self.config.reasoning_effort
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        data = self._request_with_retries(req)
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LlmError(f"unexpected response shape: {json.dumps(data)[:300]}") from e

        # A truncated completion is NOT a formatting failure — say so (ERR-010 follow-up).
        # DeepSeek's v4 models are reasoners: `max_tokens` budgets reasoning AND content
        # together, and reasoning runs first. When it eats the whole budget the API still
        # returns HTTP 200 with `finish_reason: "length"` and an EMPTY `content`. The old
        # `content or ""` swallowed that, so it surfaced two layers up as
        # `no JSON object in model output: ''` — which blames the model's formatting and
        # sends the caller into a pointless re-prompt, when the actual cause is a token
        # budget that needs raising. Measured: a real JD at max_tokens=512 burns all 512 on
        # reasoning and returns nothing; the same call at 4096 returns clean JSON.
        if choice.get("finish_reason") == "length":
            usage = data.get("usage") or {}
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            raise LlmError(
                f"response truncated at max_tokens={self.config.max_tokens} "
                f"(finish_reason=length, completion_tokens={usage.get('completion_tokens')}"
                + (f", of which reasoning={reasoning}" if reasoning is not None else "")
                + f", content_chars={len(content)}) — raise LlmConfig.max_tokens; "
                "re-prompting at the same budget cannot help"
            )
        return content

    def read_balance(self, *, timeout_s: float = _BALANCE_TIMEOUT_S) -> LlmBalance:
        """The account's USD balance from `GET {base_url}/user/balance` (INV-005). NEVER raises.

        One try, no retry: this only forecasts the digest's low-credit banner, and a retry
        would add latency to the daily run for a reading that can wait a day. Every failure is
        a short code in `error` — never a response body, never a header (see `LlmBalance`).
        A non-DeepSeek host answers 404, recorded as `http_404`."""
        try:
            key = self._key()
        except LlmAuthError:
            return LlmBalance(None, None, "no_key")
        except Exception as exc:  # noqa: BLE001 — e.g. Secrets Manager unreachable
            return LlmBalance(None, None, f"error:{type(exc).__name__}")
        req = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/user/balance",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            with contextlib.suppress(Exception):  # closing is courtesy, never a failure
                e.close()  # release the connection; the body is NOT read
            return LlmBalance(None, None, f"http_{e.code}")
        except TimeoutError:
            return LlmBalance(None, None, "timeout")
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError):
                return LlmBalance(None, None, "timeout")
            return LlmBalance(None, None, f"error:{type(e).__name__}")
        except Exception as exc:  # noqa: BLE001 — best-effort by contract
            return LlmBalance(None, None, f"error:{type(exc).__name__}")
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001 — incl. RecursionError on a deeply nested body
            return LlmBalance(None, None, "bad_json")
        if not isinstance(data, dict):
            return LlmBalance(None, None, "bad_json")
        infos = data.get("balance_infos")
        # Amounts arrive as STRINGS, in a list keyed by currency (Evidence 6).
        for info in infos if isinstance(infos, list) else []:
            if isinstance(info, dict) and info.get("currency") == "USD":
                amount = info.get("total_balance")
                if isinstance(amount, bool):
                    break  # float(True) is 1.0 — a JSON bool is not an amount
                try:
                    usd = float(amount)
                except (TypeError, ValueError):
                    break
                if not math.isfinite(usd):
                    break  # "NaN" parses as a float and would compare False to everything
                available = data.get("is_available")
                return LlmBalance(
                    usd, available if isinstance(available, bool) else None, None
                )
        return LlmBalance(None, None, "no_usd")

    def _request_with_retries(self, req: urllib.request.Request) -> dict:
        """One HTTP round-trip, retrying ONLY transient failures (429/5xx/connection) with
        exponential backoff + full jitter. 401/404/other-4xx fail fast on the first attempt."""
        attempts = self.config.max_retries + 1
        last_transient: LlmError | None = None
        for attempt in range(attempts):
            if attempt:  # back off before every retry (never before the first attempt)
                delay = random.uniform(0, self.config.backoff_base_s * 2 ** (attempt - 1))
                log.warning(
                    "transient LLM failure (%s) — retry %d/%d after %.1fs",
                    last_transient,
                    attempt,
                    self.config.max_retries,
                    delay,
                )
                time.sleep(delay)
            try:
                with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                low = detail.lower()
                if e.code == 401:
                    raise LlmAuthError(f"401 Unauthorized: {detail}") from e
                # Checked BEFORE the model-not-found heuristic below, which matches on body
                # text and could otherwise swallow a 402 whose message mentions a model.
                if e.code == 402:
                    raise LlmBillingError(f"402 Payment Required: {detail}") from e
                if e.code == 404 or ("model" in low and "not" in low):
                    raise LlmModelNotFoundError(f"model '{self.config.model}': {detail}") from e
                err = LlmError(f"HTTP {e.code}: {detail}")
                if e.code not in _RETRYABLE_STATUSES:
                    raise err from e
                last_transient = err
            except urllib.error.URLError as e:  # connection / timeout — transient
                last_transient = LlmError(f"connection error to {self.config.base_url}: {e}")
        raise LlmError(
            f"still failing after {self.config.max_retries} retries: {last_transient}"
        ) from last_transient
