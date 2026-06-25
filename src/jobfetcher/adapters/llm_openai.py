"""OpenAI-compatible LLM client (ADR-0017) — the v0 transport; default provider DeepSeek.

One adapter serves *any* OpenAI-compatible host: the backend is config (`base_url` +
`model` + `api_key`). Structured output is left to the prompt + Pydantic, not a
provider-specific JSON mode, so this stays portable (ADR-0012). The key comes from
`$DEEPSEEK_API_KEY` (tests) or Secrets Manager (runtime) and is never logged.

Stdlib `urllib` only — no HTTP dependency.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from ..config import LlmConfig
from ..core.ports import LlmAuthError, LlmError, LlmModelNotFoundError

_ENV_KEY = "DEEPSEEK_API_KEY"


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

    def _key(self) -> str:
        if not self._api_key:
            self._api_key = _resolve_api_key(self.config)
        if not self._api_key:
            raise LlmAuthError(
                f"no API key found (env ${_ENV_KEY} or Secrets Manager '{self.config.secret_name}')"
            )
        return self._api_key

    def complete(self, *, system: str, user: str) -> str:
        body = json.dumps(
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._key()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            low = detail.lower()
            if e.code == 401:
                raise LlmAuthError(f"401 Unauthorized: {detail}") from e
            if e.code == 404 or ("model" in low and "not" in low):
                raise LlmModelNotFoundError(f"model '{self.config.model}': {detail}") from e
            raise LlmError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:  # connection / timeout
            raise LlmError(f"connection error to {self.config.base_url}: {e}") from e

        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LlmError(f"unexpected response shape: {json.dumps(data)[:300]}") from e
