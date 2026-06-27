"""`PostgresRepository` — the `Repository` port over SQLAlchemy Core (ADR-0018).

Same code, two backends by connection URL: a local Postgres (`postgresql://…`, tests) and
Aurora via the RDS Data API (`postgresql+auroradataapi://…`, deployed). It maps the silver
`DissectedPosting` contract ↔ a `posting` row (skills serialized to JSONB and read back into
the contract) and lands immutable `bronze_posting` rows idempotently.

No secrets here — the engine is built from a config `connection_url` (from env), never a
hardcoded credential.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from ..core.models import DissectedPosting, RequirementLevel, Skill
from ..core.ports import RepositoryError
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
