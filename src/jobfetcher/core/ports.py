"""Ports (interfaces). Adapters implement these; the core depends only on the port,
never the concrete provider (ADR-0015 — type-replaceable stages)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .models import DissectedPosting


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


class RepositoryError(Exception):
    """A persistence operation failed (bad data, integrity violation, or a backend error).
    Mirrors the `LlmError` style — the core raises this, never a raw SQLAlchemy error."""


class Repository(Protocol):
    """Persist + read pipeline state (ADR-0015/0018). The v0 surface is deliberately small —
    only what the bronze→silver path needs. The `PostgresRepository` adapter (SQLAlchemy Core
    over the aurora-data-api dialect) implements it; tests can pass an in-memory fake.

    The same code path runs on a local Postgres (tests) and Aurora via the Data API
    (deployed) — the backend is chosen by the connection URL, not by this interface.
    """

    def upsert_bronze(
        self,
        *,
        bronze_id: str,
        source: str,
        source_job_id: str,
        raw_payload: dict[str, Any],
        run_id: str,
        s3_raw_key: str | None = None,
    ) -> str:
        """Land an immutable raw row (idempotent on `bronze_id` — re-fetching the same id the
        same day must not duplicate). Returns the `bronze_id`."""
        ...

    def save_posting(
        self,
        dissected: "DissectedPosting",
        *,
        posting_id: str,
        bronze_id: str,
        source: str,
        source_job_id: str,
        run_id: str,
        company: str | None = None,
        apply_url: str | None = None,
        description: str | None = None,
        state: str | None = None,
        pipeline_version: str | None = None,
        status: str = "silver",
    ) -> str:
        """Map a `DissectedPosting` (+ its lineage/source fields) to a silver `posting` row
        (skills serialized to JSONB). Idempotent on `posting_id`. Returns the `posting_id`.

        Raises `RepositoryError` if a required field is missing or the write fails.
        """
        ...

    def get_posting(self, posting_id: str) -> "DissectedPosting | None":
        """Read a `posting` row back into the `DissectedPosting` contract, or `None` if no
        such id exists (a missing id is not an error)."""
        ...
