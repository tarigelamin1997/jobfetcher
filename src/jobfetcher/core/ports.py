"""Ports (interfaces). Adapters implement these; the core depends only on the port,
never the concrete provider (ADR-0015 — type-replaceable stages)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import date, datetime

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
    # Digest truthfulness: `scored_at` (when the CURRENT judgment was written) vs the last
    # digest send time is the new/still-open signal — daily runs score a posting exactly once,
    # so a repeat's scored_at predates the last digest; `previous_score` decides whether a
    # fresh re-score is a graduation (badge) or a non-event; `fingerprint` powers the
    # render-time dup collapse; `fetched_at` is the effective age the digest age-cutoff
    # filtered on — `COALESCE(posting.fetched_at, bronze.fetched_at)`, None = age unknown.
    previous_score: int | None = None
    fingerprint: str | None = None
    fetched_at: "datetime | None" = None
    scored_at: "datetime | None" = None


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

    def get_scored_for_reassess(
        self,
        *,
        max_age_days: int | None = None,
    ) -> "list[tuple[str, str, DissectedPosting, int, str]]":
        """Read the reassess set (ADR-0023): every `status='scored'` posting + its CURRENT
        score and fit_category as `(posting_id, cluster_id, dissected, current_score,
        current_fit_category)` — the replay's input, so `reassess()` can re-score against the
        updated profile and report the old→new delta. **Ordered by `posting_id`** so a run is
        deterministic.

        `max_age_days` bounds the replay by posting age (LLM-token thrift): when set and > 0,
        only postings fetched within the last N days are returned — a posting whose age is
        unknown (no fetched timestamp resolvable) is INCLUDED, never silently dropped from
        replay forever. `None` or `0` = unbounded (every scored posting). Raises
        `RepositoryError` on a backend failure."""
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
        scoring_model: str,
        profile_hash: str,
        run_id: str | None = None,
        previous_score: int | None = None,
        subscores: dict[str, Any] | None = None,
    ) -> str:
        """Upsert a `score` row keyed on `cluster_id` (1:1 with cluster). Idempotent —
        re-scoring overwrites; the prior `score` is carried into `previous_score` when one
        exists (near-miss re-scoring trail). In the SAME transaction, APPEND an immutable
        `score_event` row (migration 0004) carrying the score + its lineage — `scoring_model`
        and `profile_hash` are required (an event is never written without its provenance),
        `run_id` is the correlation id when the caller has one. A failure of either write
        rolls back both. Returns the `cluster_id`. Raises `RepositoryError` on a backend
        failure.

        `subscores` (migration 0006, ADR-0028-to-be) is the per-factor breakdown blob the
        caller built via `core.scorer.subscores_payload` — `{7 factors, code_total,
        llm_total}` — written to BOTH the `score` upsert and the `score_event` append (each
        event self-contained). `None` (the LLM omitted subscores, or a pre-0006 caller)
        leaves the column NULL — never a partial dict. SHADOW data only: `score` remains
        the product number."""
        ...

    def mark_scored(self, posting_id: str) -> None:
        """Mark a posting done: set `posting.status = 'scored'`."""
        ...

    def track_application_event(
        self, *, posting_id: str, status: str, note: str | None = None
    ) -> None:
        """APPEND one immutable `application_event` row (migration 0005) — a human outcome
        note (`applied`/`interview`/`offer`/`rejected`/`withdrawn`, the shared
        `APPLICATION_STATUSES` vocabulary) against a posting, written by `scripts/track.py`.
        Append-only: latest-status is a read-side query, never an overwrite, so the full
        outcome trail survives. Raises `RepositoryError` — with ZERO rows written — for an
        invalid status, an empty/unknown `posting_id`, or a backend failure."""
        ...

    def set_score_override(
        self,
        *,
        cluster_id: str,
        score_override: int,
        fit_category: str,
        profile_hash: str,
        previous_score: int | None,
    ) -> None:
        """Record a human score correction: ONE transaction that UPDATEs
        `score.score_override` for the cluster AND APPENDs a `score_event` lineage row with
        `scoring_model='human-override'` (score = the override, `previous_score` = the
        pre-override score, LLM-judgment fields honestly empty) — human overrides join the
        same append-only log as LLM scorings, so a second override never erases the first.
        `score_override` is validated 0-100 here and at the CLI (no DB constraint —
        additive-only). Raises `RepositoryError` — with ZERO rows written — for an
        out-of-range score, an empty/unknown `cluster_id`, or a backend failure."""
        ...

    def get_scored_shortlist(
        self,
        *,
        threshold: int,
        since: "datetime | None" = None,
        max_age_days: int | None = None,
    ) -> "tuple[list[ShortlistItem], int]":
        """Read the daily digest input (Step 6): JOIN `score` ↔ `posting` on `cluster_id` (1:1
        in v0) and return `(surfaced, count_below_threshold)` where `surfaced` is every match
        with `score >= threshold` as `ShortlistItem`s **ordered by score DESC**, and
        `count_below_threshold` is how many scored matches fell below it (the "+N below" footer).

        The `threshold` is resolved by the caller (`notify()` is the single threshold authority —
        the DB `profile.threshold` with the documented-default fallback) and passed in, so the
        surfaced/below split uses the one config knob — this method does not re-derive it.

        Digest truthfulness: each item also carries `previous_score`, `scored_at` (when the
        current judgment was written), `fingerprint`, and the effective age `fetched_at`
        (`COALESCE(posting.fetched_at, bronze.fetched_at)` — the reassess age source;
        `posting.fetched_at` is NULL on live rows). The new/still-open split (`scored_at` vs
        the last digest time, with `previous_score` for the graduation call) and the dup
        grouping are computed RENDER-SIDE (pure functions in `core/notifier.py`), never here —
        `since` (the last digest send time) is accepted on the port for that caller but does
        not filter this query in v0.

        `max_age_days` bounds the digest by posting age: when set and > 0, a row whose
        effective fetched-at is older than N days is DROPPED (from both the surfaced list and
        the below count — an aged-out job vanishes from the digest entirely), while an
        unknown-age row (COALESCE'd NULL) is INCLUDED, never silently dropped. `None`/`0` =
        no age cutoff.

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
        profile_hash: str | None = None,
    ) -> None:
        """Seed (or update) the single-user `profile` row: the JSONB payload + the three
        threshold knobs. Idempotent on `user_id` — the Step-7 handler calls this once to seed
        from the loaded `Profile`/config when no row exists yet. `profile_hash` (nullable,
        migration 0004) records which profile+knobs content the row was synced from — the same
        hash stamped on every `score_event`. Raises `RepositoryError` on a backend failure."""
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

    def get_last_digest_sent_at(self, *, user_id: str) -> "datetime | None":
        """`MAX(run_log.digest_sent_at)` for the user — when the last digest actually went out,
        or `None` when no digest was ever sent (no `run_log` rows — NULL-safe, not an error).
        `notify()` passes it as `since` to the renderer: `None` = the first-ever digest, so
        EVERYTHING is new. Raises `RepositoryError` on a backend failure."""
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
