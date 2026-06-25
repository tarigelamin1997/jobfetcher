"""Ports (interfaces). Adapters implement these; the core depends only on the port,
never the concrete provider (ADR-0015 — type-replaceable stages)."""
from __future__ import annotations

from typing import Protocol


class LlmError(Exception):
    """Base for LLM transport failures."""


class LlmAuthError(LlmError):
    """Auth rejected (HTTP 401) — missing, wrong, or revoked key."""


class LlmModelNotFoundError(LlmError):
    """The model id is unknown to the provider (HTTP 404 / 'model not found')."""


class LlmClient(Protocol):
    """A provider-agnostic single-turn chat completion.

    Implementations carry their own config (model, temperature, max_tokens), so the
    caller just supplies the prompt. v0 impl: `OpenAICompatLlmClient` (DeepSeek); tests
    pass a fake.
    """

    def complete(self, *, system: str, user: str) -> str:
        """Return the assistant message text for a system+user prompt.

        Raises `LlmAuthError` / `LlmModelNotFoundError` / `LlmError` on failure — never
        returns a silent empty string for an error.
        """
        ...
