"""`PostgresRepository` — the `Repository` port over SQLAlchemy Core (ADR-0018).

Same code, two backends by connection URL: a local Postgres (`postgresql://…`, tests) and
Aurora via the RDS Data API (`postgresql+auroradataapi://…`, deployed). It maps the silver
`DissectedPosting` contract ↔ a `posting` row (skills serialized to JSONB and read back into
the contract) and lands immutable `bronze_posting` rows idempotently.

No secrets here — the engine is built from a config `connection_url` (from env), never a
hardcoded credential.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from ..core.models import APPLICATION_STATUSES, DissectedPosting, RequirementLevel, Skill
from ..core.ports import RepositoryError, ShortlistItem
from ..db import tables
from ..db.engine import make_engine


def _skills_to_json(skills: list[Skill]) -> list[dict[str, Any]]:
    """Serialize skills to the JSONB shape `[{name, level, evidence}]` (level as its string
    value, not the Enum member)."""
    return [{"name": s.name, "level": s.level.value, "evidence": s.evidence} for s in skills]


def _skills_from_json(raw: Any) -> list[Skill]:
    """Rebuild `Skill`s from the JSONB column (tolerates `None` → empty list)."""
    if not raw:
        return []
    return [
        Skill(name=s["name"], level=RequirementLevel(s["level"]), evidence=s["evidence"])
        for s in raw
    ]


def _dissected_from_row(row: Any) -> DissectedPosting:
    """Rebuild the `DissectedPosting` contract from a `posting` row mapping (the single place
    the silver column→contract mapping lives, shared by `get_posting`/`get_silver_postings`)."""
    return DissectedPosting(
        raw_title=row["title"],
        language=row["language"],
        location=row["location"],
        city=row["city"],
        country=row["country"],
        employment_type=row["employment_type"],
        seniority=row["seniority"],
        normalized_title=row["normalized_title"],
        sector=row["sector"],
        skills=_skills_from_json(row["skills"]),
        model=row["dissection_model"],
        dropped_skill_count=row["dropped_skill_count"] or 0,
    )


class PostgresRepository:
    """`Repository` adapter. Construct from a connection URL (one engine, reused)."""

    def __init__(self, connection_url: str) -> None:
        if not connection_url or not connection_url.strip():
            raise RepositoryError("PostgresRepository needs a non-empty connection_url")
        self.engine: Engine = make_engine(connection_url)

    @classmethod
    def from_engine(cls, engine: Engine) -> "PostgresRepository":
        """Build around an already-created engine (used by tests that share one engine)."""
        self = cls.__new__(cls)
        self.engine = engine
        return self

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
        stmt = (
            pg_insert(tables.bronze_posting)
            .values(
                bronze_id=bronze_id,
                source=source,
                source_job_id=source_job_id,
                raw_payload=raw_payload,
                run_id=run_id,
                s3_raw_key=s3_raw_key,
            )
            .on_conflict_do_nothing(index_elements=["bronze_id"])  # immutable, append-only
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"upsert_bronze failed for {bronze_id!r}: {e}") from e
        return bronze_id

    def save_posting(
        self,
        dissected: DissectedPosting,
        *,
        posting_id: str,
        bronze_id: str,
        source: str,
        source_job_id: str,
        run_id: str,  # noqa: ARG002 — accepted for the port; not a posting column in v0
        company: str | None = None,
        apply_url: str | None = None,
        description: str | None = None,
        state: str | None = None,
        pipeline_version: str | None = None,
        fingerprint: str | None = None,
        status: str = "silver",
    ) -> str:
        if not posting_id:
            raise RepositoryError("save_posting requires a non-empty posting_id")

        values = {
            "posting_id": posting_id,
            "bronze_id": bronze_id,
            "source": source,
            "source_job_id": source_job_id,
            "title": dissected.raw_title,
            "company": company,
            "location": dissected.location,
            "city": dissected.city,
            "state": state,
            "country": dissected.country,
            "apply_url": apply_url,
            "description": description,
            "normalized_title": dissected.normalized_title,
            "sector": dissected.sector,
            "seniority": dissected.seniority,
            "employment_type": dissected.employment_type,
            "language": dissected.language,
            "skills": _skills_to_json(dissected.skills),
            "dissection_model": dissected.model,
            "dropped_skill_count": dissected.dropped_skill_count,
            "pipeline_version": pipeline_version,
            "fingerprint": fingerprint,
            "status": status,
        }
        # Upsert: re-running the same day must not duplicate the silver row (idempotent).
        update_cols = {k: v for k, v in values.items() if k != "posting_id"}
        stmt = (
            pg_insert(tables.posting)
            .values(**values)
            .on_conflict_do_update(index_elements=["posting_id"], set_=update_cols)
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"save_posting failed for {posting_id!r}: {e}") from e
        return posting_id

    def get_posting(self, posting_id: str) -> DissectedPosting | None:
        stmt = select(tables.posting).where(tables.posting.c.posting_id == posting_id)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_posting failed for {posting_id!r}: {e}") from e
        if row is None:
            return None
        return _dissected_from_row(row)

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        stmt = select(tables.profile).where(tables.profile.c.user_id == user_id)
        try:
            with self.engine.connect() as conn:
                row = conn.execute(stmt).mappings().first()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_profile failed for {user_id!r}: {e}") from e
        if row is None:
            return None
        return {
            "profile": row["profile"],
            "threshold": row["threshold"],
            "hard_floor": row["hard_floor"],
            "near_miss_band": row["near_miss_band"],
        }

    def upsert_profile(
        self,
        *,
        user_id: str,
        profile: dict[str, Any],
        threshold: int,
        hard_floor: int,
        near_miss_band: int,
        profile_hash: str | None = None,
    ) -> None:
        # Idempotent on `user_id`: the handler seeds the single-user row once (when none exists);
        # a re-run overwrites with the same values — never duplicates. Mirrors the inline upsert
        # the integration tests use, lifted onto the port so the handler doesn't reach into
        # SQLAlchemy directly. `profile_hash` (nullable, migration 0004) records which
        # profile+knobs content the row was synced from — the same hash `save_score` stamps on
        # every `score_event`, tying each score to the profile it judged against.
        stmt = (
            pg_insert(tables.profile)
            .values(
                user_id=user_id,
                profile=profile,
                threshold=threshold,
                hard_floor=hard_floor,
                near_miss_band=near_miss_band,
                profile_hash=profile_hash,
            )
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={
                    "profile": profile,
                    "threshold": threshold,
                    "hard_floor": hard_floor,
                    "near_miss_band": near_miss_band,
                    "profile_hash": profile_hash,
                },
            )
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"upsert_profile failed for {user_id!r}: {e}") from e

    def get_silver_postings(
        self, *, limit: int | None = None
    ) -> list[tuple[str, DissectedPosting]]:
        stmt = select(tables.posting).where(tables.posting.c.status == "silver")
        if limit is not None:
            stmt = stmt.limit(limit)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_silver_postings failed: {e}") from e
        return [(row["posting_id"], _dissected_from_row(row)) for row in rows]

    def mark_gold_candidate(self, posting_id: str) -> None:
        stmt = (
            update(tables.posting)
            .where(tables.posting.c.posting_id == posting_id)
            .values(status="gold_candidate")
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"mark_gold_candidate failed for {posting_id!r}: {e}") from e

    def upsert_cluster(
        self,
        *,
        cluster_id: str,
        representative_posting_id: str,
        posting_count: int = 1,
    ) -> str:
        # Idempotent: a re-run over the same gold candidate must not error or duplicate the
        # 1:1 cluster (v0 clusters are trivial; real clustering is M2).
        stmt = (
            pg_insert(tables.cluster)
            .values(
                cluster_id=cluster_id,
                representative_posting_id=representative_posting_id,
                posting_count=posting_count,
            )
            .on_conflict_do_nothing(index_elements=["cluster_id"])
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"upsert_cluster failed for {cluster_id!r}: {e}") from e
        return cluster_id

    def set_posting_cluster(self, posting_id: str, cluster_id: str) -> None:
        stmt = (
            update(tables.posting)
            .where(tables.posting.c.posting_id == posting_id)
            .values(cluster_id=cluster_id)
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"set_posting_cluster failed for {posting_id!r}: {e}"
            ) from e

    def get_gold_candidates(self) -> list[tuple[str, str, DissectedPosting]]:
        # Ordered by posting_id so a scoring run is deterministic (re-runs visit the same order).
        stmt = (
            select(tables.posting)
            .where(tables.posting.c.status == "gold_candidate")
            .order_by(tables.posting.c.posting_id)
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_gold_candidates failed: {e}") from e
        return [
            (row["posting_id"], row["cluster_id"], _dissected_from_row(row)) for row in rows
        ]

    def get_scored_for_reassess(
        self,
        *,
        max_age_days: int | None = None,
    ) -> list[tuple[str, str, DissectedPosting, int, str]]:
        """The reassess set (ADR-0023): every already-scored posting + its CURRENT score and
        fit_category, so a replay can re-score against the updated profile and report the
        old→new delta. Returns `(posting_id, cluster_id, dissected, current_score,
        current_fit_category)`. Ordered by posting_id for a deterministic run.

        Only `status='scored'` postings are returned — these are the ones with a prior score to
        graduate. (A skill change doesn't alter gold membership, so re-scoring the scored set is
        the right scope; re-running gold for a targeting/avoid change is a separate concern.)

        **`max_age_days` bounds the replay by posting age** (LLM-token thrift: a months-old
        posting is likely filled — don't pay to re-score it forever). When set and > 0, only
        postings fetched within the last `max_age_days` are returned. The age source is
        `COALESCE(posting.fetched_at, bronze_posting.fetched_at)` (LEFT JOIN on the existing
        `posting.bronze_id` lineage FK): `posting.fetched_at` takes precedence but is nullable
        AND never populated by `save_posting`, while the bronze row's `fetched_at` is NOT NULL
        with a `now()` default — so every real posting gets a real age and the bound actually
        bites on live data. A posting whose COALESCE'd age is STILL NULL (a NULL `bronze_id` /
        LEFT-JOIN miss — pathological) is **INCLUDED**: NULL-age is treated as unknown, not
        old, so a naive `>= cutoff` can never silently drop a row from reassess FOREVER. The
        cutoff is computed in Python and passed as a bound parameter — never interpolated SQL.

        **`max_age_days=None` or `0` = UNBOUNDED** (the pre-0004 behavior: every scored
        posting is reassessed, no age cut)."""
        s, p = tables.score, tables.posting
        joined = p.join(s, p.c.cluster_id == s.c.cluster_id)
        stmt = select(p, s.c.score, s.c.fit_category)
        if max_age_days is not None and max_age_days > 0:
            b = tables.bronze_posting
            cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            # posting.fetched_at wins when set; the bronze landing time is the live fallback.
            age_source = func.coalesce(p.c.fetched_at, b.c.fetched_at)
            joined = joined.outerjoin(b, p.c.bronze_id == b.c.bronze_id)
            # A COALESCE'd NULL is INCLUDED (see docstring) — the OR IS NULL is load-bearing.
            stmt = stmt.where(age_source.is_(None) | (age_source >= cutoff))
        stmt = stmt.select_from(joined).where(p.c.status == "scored").order_by(p.c.posting_id)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).mappings().all()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_scored_for_reassess failed: {e}") from e
        return [
            (
                row["posting_id"],
                row["cluster_id"],
                _dissected_from_row(row),
                row["score"],
                row["fit_category"],
            )
            for row in rows
        ]

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
    ) -> str:
        if not cluster_id:
            raise RepositoryError("save_score requires a non-empty cluster_id")
        # `score` is 1:1 with cluster on the natural key `cluster_id` (uq_score_cluster_id —
        # see db/tables.py), so a re-score is an upsert on that key: idempotent, never
        # duplicates the cluster's row. On conflict, carry the existing row's score into
        # `previous_score` (the pre-update value via `tables.score.c.score`, NOT excluded) so
        # the old score moves into previous_score in one statement — unless the caller passed
        # an explicit `previous_score`.
        #
        # DUAL-WRITE (migration 0004): the same transaction also APPENDS an immutable
        # `score_event` row carrying the lineage (`scoring_model`/`profile_hash`/`run_id` —
        # both hashes are REQUIRED, so an event can never be written without its provenance).
        # One `engine.begin()` block = one transaction: if either statement fails, BOTH roll
        # back — the current-judgment upsert and the history log can never diverge.
        stmt = pg_insert(tables.score).values(
            cluster_id=cluster_id,
            score=score,
            fit_category=fit_category,
            strengths=strengths,
            gaps=gaps,
            strategic_assessment=strategic_assessment,
            poster_type=poster_type,
            legitimacy_verified=legitimacy_verified,
            previous_score=previous_score,
            scored_at=text("now()"),
        )
        carried_previous = (
            previous_score if previous_score is not None else tables.score.c.score
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cluster_id"],
            set_={
                "score": stmt.excluded.score,
                "fit_category": stmt.excluded.fit_category,
                "strengths": stmt.excluded.strengths,
                "gaps": stmt.excluded.gaps,
                "strategic_assessment": stmt.excluded.strategic_assessment,
                "poster_type": stmt.excluded.poster_type,
                "legitimacy_verified": stmt.excluded.legitimacy_verified,
                "previous_score": carried_previous,
                "scored_at": stmt.excluded.scored_at,
            },
        )
        # The event's `previous_score` is exactly what THIS call received (an explicit old
        # score on reassess, None on first scoring) — self-contained, never re-derived.
        # `scored_at` is deliberately omitted — the column's server_default (now()) stamps it.
        # `.inline()` + the table's implicit_returning=False keep this INSERT a plain
        # single-statement write: no RETURNING and no PK prefetch (`select nextval(...)`) —
        # both are Data-API-untested paths (see db/tables.py); the server assigns `event_id`.
        event_stmt = tables.score_event.insert().inline().values(
            cluster_id=cluster_id,
            score=score,
            fit_category=fit_category,
            strengths=strengths,
            gaps=gaps,
            strategic_assessment=strategic_assessment,
            poster_type=poster_type,
            legitimacy_verified=legitimacy_verified,
            previous_score=previous_score,
            scoring_model=scoring_model,
            profile_hash=profile_hash,
            run_id=run_id,
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
                conn.execute(event_stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"save_score failed for {cluster_id!r}: {e}") from e
        return cluster_id

    def track_application_event(
        self, *, posting_id: str, status: str, note: str | None = None
    ) -> None:
        # Validate BEFORE touching the DB: an invalid status fails loudly here (the same
        # vocabulary the table's CHECK enforces — `APPLICATION_STATUSES`, core/models.py),
        # never as an opaque constraint error, and never with a row half-written.
        if status not in APPLICATION_STATUSES:
            raise RepositoryError(
                f"track_application_event: invalid status {status!r} — "
                f"allowed: {', '.join(APPLICATION_STATUSES)}"
            )
        if not posting_id:
            raise RepositoryError("track_application_event requires a non-empty posting_id")
        exists_stmt = select(tables.posting.c.posting_id).where(
            tables.posting.c.posting_id == posting_id
        )
        # `.inline()` + the table's implicit_returning=False keep this a plain single-statement
        # INSERT — no RETURNING, no PK prefetch (the score_event Data-API hardening, see
        # db/tables.py); the server assigns `event_id` and stamps `noted_at` (default now()).
        insert_stmt = tables.application_event.insert().inline().values(
            posting_id=posting_id, status=status, note=note
        )
        try:
            with self.engine.begin() as conn:
                if conn.execute(exists_stmt).first() is None:
                    # Raising inside the block rolls the transaction back — zero rows written.
                    raise RepositoryError(
                        f"track_application_event: no posting {posting_id!r} — "
                        "nothing written (find the id with `scripts/track.py find`)"
                    )
                conn.execute(insert_stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"track_application_event failed for {posting_id!r}: {e}"
            ) from e

    def set_score_override(
        self,
        *,
        cluster_id: str,
        score_override: int,
        fit_category: str,
        profile_hash: str,
        previous_score: int | None,
    ) -> None:
        if not cluster_id:
            raise RepositoryError("set_score_override requires a non-empty cluster_id")
        # Range-validated here AND at the CLI (scripts/track.py argparse) — no DB constraint
        # (additive-only: nothing retrofitted onto the existing `score`/`score_event` DDL).
        if not 0 <= score_override <= 100:
            raise RepositoryError(
                f"set_score_override: score_override must be 0-100, got {score_override}"
            )
        upd = (
            update(tables.score)
            .where(tables.score.c.cluster_id == cluster_id)
            .values(score_override=score_override)
        )
        # The override joins the SAME lineage log as LLM scorings (`score_event`, migration
        # 0004): scoring_model='human-override' marks provenance, `score` carries the human
        # number, `previous_score` the pre-override score, and the LLM-judgment fields are
        # honestly empty ([] / NULL — a human override has no strengths/gaps narrative).
        # Append-only: a second override never erases the first. Same plain-INSERT hardening
        # as every server-generated-PK write (.inline() + implicit_returning=False).
        event_stmt = tables.score_event.insert().inline().values(
            cluster_id=cluster_id,
            score=score_override,
            fit_category=fit_category,
            strengths=[],
            gaps=[],
            strategic_assessment=None,
            poster_type=None,
            legitimacy_verified=None,
            previous_score=previous_score,
            scoring_model="human-override",
            profile_hash=profile_hash,
            run_id=None,
        )
        try:
            # ONE transaction: the `score.score_override` UPDATE and the lineage APPEND
            # commit together or not at all (the save_score dual-write discipline).
            with self.engine.begin() as conn:
                result = conn.execute(upd)
                if result.rowcount == 0:
                    # Raising inside the block rolls back — no event without a score row.
                    raise RepositoryError(
                        f"set_score_override: no score row for cluster {cluster_id!r} — "
                        "nothing written"
                    )
                conn.execute(event_stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"set_score_override failed for {cluster_id!r}: {e}") from e

    def mark_scored(self, posting_id: str) -> None:
        stmt = (
            update(tables.posting)
            .where(tables.posting.c.posting_id == posting_id)
            .values(status="scored")
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(f"mark_scored failed for {posting_id!r}: {e}") from e

    def get_scored_shortlist(
        self, *, threshold: int
    ) -> tuple[list[ShortlistItem], int]:
        # `threshold` is the one config knob, resolved by the caller (`notify()` — the single
        # authority); the surfaced/below split is computed against it, never a re-derived
        # constant (VG8: the gate is config, not a hardcoded value).
        #
        # SCOPE CAVEAT: the score↔posting join is GLOBAL / single-user v0 — there is no user/run
        # scope on the join (every scored posting is read); the multi-user/M2 seam must close it.
        #
        # JOIN score ↔ posting on cluster_id (1:1 in v0). Carry the raw title + apply_url from
        # posting and the LLM judgment from score; order by score DESC so the digest leads with
        # the best match.
        s, p = tables.score, tables.posting
        joined = (
            select(
                p.c.posting_id,
                p.c.title,
                p.c.company,
                p.c.apply_url,
                p.c.normalized_title,
                p.c.city,
                p.c.country,
                s.c.score,
                s.c.fit_category,
                s.c.strengths,
                s.c.gaps,
                s.c.strategic_assessment,
            )
            .select_from(s.join(p, s.c.cluster_id == p.c.cluster_id))
            .order_by(s.c.score.desc())
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(joined).mappings().all()
        except SQLAlchemyError as e:
            raise RepositoryError(f"get_scored_shortlist failed: {e}") from e

        surfaced: list[ShortlistItem] = []
        below = 0
        for row in rows:
            score = row["score"]
            if score is None:
                continue  # an un-scored join artifact never surfaces or counts as below
            if score >= threshold:
                surfaced.append(
                    ShortlistItem(
                        posting_id=row["posting_id"],
                        title=row["title"],
                        company=row["company"],
                        apply_url=row["apply_url"],
                        normalized_title=row["normalized_title"],
                        score=score,
                        fit_category=row["fit_category"],
                        strengths=list(row["strengths"] or []),
                        gaps=list(row["gaps"] or []),
                        strategic_assessment=row["strategic_assessment"],
                        city=row["city"],
                        country=row["country"],
                    )
                )
            else:
                below += 1
        return surfaced, below

    def was_digest_sent(self, *, user_id: str, run_date: date) -> bool:
        stmt = select(tables.run_log.c.run_id).where(
            (tables.run_log.c.user_id == user_id)
            & (tables.run_log.c.run_date == run_date)
        )
        try:
            with self.engine.connect() as conn:
                row = conn.execute(stmt).first()
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"was_digest_sent failed for {user_id!r}/{run_date}: {e}"
            ) from e
        return row is not None

    def mark_digest_sent(self, *, user_id: str, run_date: date, run_id: str) -> None:
        # Idempotent upsert on the composite PK (run_date, user_id): recording the same day twice
        # refreshes the run_id rather than erroring, so a benign re-mark never crashes a re-run.
        stmt = (
            pg_insert(tables.run_log)
            .values(run_date=run_date, user_id=user_id, run_id=run_id)
            .on_conflict_do_update(
                index_elements=["run_date", "user_id"],
                set_={"run_id": run_id},
            )
        )
        try:
            with self.engine.begin() as conn:
                conn.execute(stmt)
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"mark_digest_sent failed for {user_id!r}/{run_date}: {e}"
            ) from e
