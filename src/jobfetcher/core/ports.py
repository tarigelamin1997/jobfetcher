"""Ports (interfaces). Adapters implement these; the core depends only on the port,
never the concrete provider (ADR-0015 — type-replaceable stages)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date

    from .models import DissectedPosting
    from .profile import Profile
    from .search_spec import SearchSpec


@dataclass(frozen=True)
class ShortlistItem:
    """One surfaced (score >= threshold) match, carrying exactly what the daily digest renders
    (Step 6). A small dataclass reads cleaner than a wide tuple at the call sites; the
    Repository builds these, the renderer consumes them. `strengths`/`gaps` are the JSONB lists
    read back from the `score` row (list[str] in v0; `Any` tolerates the lossless JSONB shape)."""

    posting_id: str
    title: str  # raw source title
    company: str | None
    apply_url: str | None
    normalized_title: str | None
    score: int
    fit_category: str | None
    strengths: list[Any] = field(default_factory=list)
    gaps: list[Any] = field(default_factory=list)
    strategic_assessment: str | None = None
    city: str | None = None  # for the digest card's "Company · Location"
    country: str | None = None


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


class FilterError(Exception):
    """A `FilterStrategy` could not produce a verdict (e.g. the LLM filter's output was
    unparseable after a retry). Mirrors the `LlmError` style — the gold step catches it and
    **fails open** (includes the posting), so a real fit is never dropped before scoring."""


class FilterStrategy(Protocol):
    """Coarse gold filter (ADR-0015/0016): a per-posting "likely-fit?" verdict over the
    *already-dissected* silver fields + the spec targeting vs the candidate profile.

    Type-replaceable: the v0 default is `DeterministicFilterStrategy` (no LLM — P1 at v0
    volume); `LlmFilterStrategy` is built + selectable. Kept **coarse + permissive** on
    purpose — the Scorer does the fine judgment; this only cuts the obviously-irrelevant.
    """

    def filter(
        self, spec: "SearchSpec", profile: "Profile", posting: "DissectedPosting"
    ) -> bool:
        """Return `True` if the posting is a *likely* fit (keep for scoring), `False` to drop.

        May raise `FilterError` only when no verdict is possible; the caller fails open.
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

    def get_profile(self, user_id: str) -> "dict[str, Any] | None":
        """Read a `profile` row: `{profile, threshold, hard_floor, near_miss_band}` (the
        JSONB payload + the numeric knobs), or `None` if no such user exists. The caller
        builds a `Profile` from the `profile` key (`Profile.from_jsonb`)."""
        ...

    def get_silver_postings(
        self, *, limit: int | None = None
    ) -> "list[tuple[str, DissectedPosting]]":
        """Read postings with `status='silver'` as `(posting_id, DissectedPosting)` pairs —
        the id is needed to mark/cluster each. The gold step's input set."""
        ...

    def mark_gold_candidate(self, posting_id: str) -> None:
        """Promote a posting: set `posting.status = 'gold_candidate'`."""
        ...

    def upsert_cluster(
        self,
        *,
        cluster_id: str,
        representative_posting_id: str,
        posting_count: int = 1,
    ) -> str:
        """Create (or no-op if it exists) a `cluster` row. v0 clusters are trivially 1:1
        (one posting per cluster; real clustering is M2). Idempotent. Returns `cluster_id`."""
        ...

    def set_posting_cluster(self, posting_id: str, cluster_id: str) -> None:
        """Set `posting.cluster_id` — attach a posting to its cluster."""
        ...

    def get_gold_candidates(self) -> "list[tuple[str, str, DissectedPosting]]":
        """Read postings with `status='gold_candidate'` as `(posting_id, cluster_id,
        DissectedPosting)` triples — the Scorer's input set. The `cluster_id` is the key the
        `score` row is written under (1:1 with cluster in v0). **Ordered by `posting_id`** so
        a run is deterministic. Raises `RepositoryError` on a backend failure."""
        ...

    def save_score(
        self,
        *,
        cluster_id: str,
        score: int,
        fit_category: str,
        strengths: list[Any],
        gaps: list[Any],
        strategic_assessment: str,
        poster_type: str,
        legitimacy_verified: bool,
        previous_score: int | None = None,
    ) -> str:
        """Upsert a `score` row keyed on `cluster_id` (1:1 with cluster). Idempotent —
        re-scoring overwrites; the prior `score` is carried into `previous_score` when one
        exists (near-miss re-scoring trail). Returns the `cluster_id`. Raises `RepositoryError`
        on a backend failure."""
        ...

    def mark_scored(self, posting_id: str) -> None:
        """Mark a posting done: set `posting.status = 'scored'`."""
        ...

    def get_scored_shortlist(
        self, *, threshold: int
    ) -> "tuple[list[ShortlistItem], int]":
        """Read the daily digest input (Step 6): JOIN `score` ↔ `posting` on `cluster_id` (1:1
        in v0) and return `(surfaced, count_below_threshold)` where `surfaced` is every match
        with `score >= threshold` as `ShortlistItem`s **ordered by score DESC**, and
        `count_below_threshold` is how many scored matches fell below it (the "+N below" footer).

        The `threshold` is resolved by the caller (`notify()` is the single threshold authority —
        the DB `profile.threshold` with the documented-default fallback) and passed in, so the
        surfaced/below split uses the one config knob — this method does not re-derive it.

        "Surfaced" matches Step 5's `surfaced`/`strong_fit` cut. Raises `RepositoryError` on a
        backend failure."""
        ...

    def upsert_profile(
        self,
        *,
        user_id: str,
        profile: "dict[str, Any]",
        threshold: int,
        hard_floor: int,
        near_miss_band: int,
    ) -> None:
        """Seed (or update) the single-user `profile` row: the JSONB payload + the three
        threshold knobs. Idempotent on `user_id` — the Step-7 handler calls this once to seed
        from the loaded `Profile`/config when no row exists yet. Raises `RepositoryError` on a
        backend failure."""
        ...

    def was_digest_sent(self, *, user_id: str, run_date: "date") -> bool:
        """True if the daily digest has already been sent for `(user_id, run_date)` — the
        send-once guard (VG4). A re-invocation for the same date checks this BEFORE sending so it
        never double-emails. Raises `RepositoryError` on a backend failure."""
        ...

    def mark_digest_sent(self, *, user_id: str, run_date: "date", run_id: str) -> None:
        """Record that the digest was sent for `(user_id, run_date)` (idempotent upsert on the
        composite PK). Called only after a successful send. Raises `RepositoryError` on a
        backend failure."""
        ...


class NotifierError(Exception):
    """The daily digest could not be sent (the provider rejected it, or no sender is
    configured). Mirrors the `LlmError` style — the core raises this, never a raw boto3 error.
    Email is the v0 surface, so a send failure is a run failure (loud), not a silent skip."""


class Notifier(Protocol):
    """Send one rendered digest (ADR-0015 — type-replaceable). The v0 impl is `SesNotifier`
    (AWS SES); tests pass a fake. The renderer (`core/notifier.py`) produces the bodies; this
    port only delivers them, so the transport is swappable without touching the rendering."""

    def send(
        self,
        *,
        subject: str,
        html_body: str,
        text_body: str,
        recipients: list[str],
    ) -> str:
        """Send the digest to `recipients` with both an HTML body and a plaintext fallback.
        Returns the provider message id. Raises `NotifierError` on any failure — never returns
        a silent empty id for a rejected send."""
        ...
