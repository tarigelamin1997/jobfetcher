"""OpenAI-compatible LLM client (ADR-0017) — the v0 transport; default provider DeepSeek.

One adapter serves *any* OpenAI-compatible host: the backend is config (`base_url` +
`model` + `api_key`). Structured output is left to the prompt + Pydantic, not a
provider-specific JSON mode, so this stays portable (ADR-0012). The key comes from
`$DEEPSEEK_API_KEY` (tests) or Secrets Manager (runtime) and is never logged.

Stdlib `urllib` only — no HTTP dependency.

Transient provider failures (429 / 5xx / connection errors) are retried with exponential
backoff + full jitter, per `LlmConfig.max_retries` (ERR-006: one DeepSeek 503 must not kill
a run). Auth and model-not-found errors always fail fast — retrying them is pure waste.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from ..config import LlmConfig
from ..core.ports import LlmAuthError, LlmBillingError, LlmError, LlmModelNotFoundError

log = logging.getLogger(__name__)

_ENV_KEY = "DEEPSEEK_API_KEY"

# HTTP statuses worth retrying: rate limit + server-side/transient. Everything else 4xx is a
# request problem that a retry cannot fix.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


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
