"""Ports (interfaces). Adapters implement these; the core depends only on the port,
never the concrete provider (ADR-0015 — type-replaceable stages)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .models import DissectedPosting
    from .search_spec import SearchSpec


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


class SourceError(Exception):
    """A job-source fetch failed in a way the run cannot recover from: no resolvable API key,
    or an auth/subscription rejection (HTTP 401/403) — a broken credential must fail LOUDLY,
    else a rotated key becomes a silent zero-count "success". Quota/rate-limit (429) and
    network blips are NOT raised — the adapter stops gracefully and yields what it already has.
    Mirrors the `LlmError` style."""


class SourceAdapter(Protocol):
    """A provider-agnostic job source (ADR-0015). The v0 impl is `JSearchSourceAdapter`;
    tests pass a fake. `fetch` is a generator so the caller can land each posting as it
    arrives (bronze-first), and so budget/quota stops are observable mid-stream.
    """

    def fetch(self, spec: "SearchSpec", *, run_id: str) -> "Iterator[dict[str, Any]]":
        """Yield raw posting dicts (the source's untouched per-job JSON), paginated across
        the spec's query matrix under its request/page budget.

        **Never crashes the run on a *transient* condition:** quota/rate-limit (429) or a
        network error stops iteration gracefully, yielding whatever was already fetched. A
        genuine misconfiguration — no resolvable API key, or an auth/subscription rejection
        (HTTP 401/403) — raises `SourceError`, so a broken credential fails loudly.
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
        fingerprint: str | None = None,
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
