"""The v0 operational schema in SQLAlchemy **Core** (ADR-0018 — Core, not ORM, for P1
minimalism). The tables mirror the `docs/02-architecture.md` ERD exactly; this `MetaData`
is what both the `PostgresRepository` adapter and the Alembic migration build from, so the
schema can never drift between the app and the migration.

`skills` / `raw_payload` / `strengths` / `gaps` / `profile` are JSONB (lossless — the
`dim_skill`/`fct_job_skill` bridge is a retroactive M5 model over `skills`, not an early
bridge). `posting.jd_embedding` (pgvector) is **M2** and deliberately omitted here — adding
it is an additive Alembic migration plus the `pgvector` dependency, neither of which v0 needs.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

metadata = MetaData()

_TS = TIMESTAMP(timezone=True)  # timestamptz, matching the ERD


bronze_posting = Table(
    "bronze_posting",
    metadata,
    Column("bronze_id", Text, primary_key=True),
    Column("source", Text, nullable=False),
    Column("source_job_id", Text, nullable=False),
    Column("raw_payload", JSONB, nullable=False),  # untouched API JSON
    Column("s3_raw_key", Text),  # mirror in S3 raw/
    Column("run_id", Text),  # correlation id
    Column("fetched_at", _TS, nullable=False, server_default=text("now()")),
)


posting = Table(
    "posting",
    metadata,
    Column("posting_id", Text, primary_key=True),
    Column("bronze_id", Text, ForeignKey("bronze_posting.bronze_id")),  # lineage
    Column("source", Text, nullable=False),
    Column("source_job_id", Text, nullable=False),
    Column("cluster_id", Text, ForeignKey("cluster.cluster_id")),
    # raw source fields (lossless silver)
    Column("title", Text),  # raw source title
    Column("company", Text),
    Column("location", Text),
    Column("city", Text),
    Column("state", Text),
    Column("country", Text),
    Column("apply_url", Text),
    Column("description", Text),
    # LLM-dissected (silver)
    Column("normalized_title", Text),
    Column("sector", Text),
    Column("seniority", Text),
    Column("employment_type", Text),
    Column("language", Text),
    Column("skills", JSONB),  # [{name, level must|nice|implied, evidence}]
    Column("dissection_model", Text),  # provenance
    Column("dropped_skill_count", Integer),  # skills cut by grounding
    Column("pipeline_version", Text),  # lineage
    # M2: jd_embedding (pgvector) — omitted in v0, added by an additive migration at M2.
    Column("fingerprint", Text),  # normalized title|company|location hash
    Column("match_status", Text),  # confirmed | suspected | rejected_by_user
    Column("match_confidence", Float),
    Column("fetched_at", _TS),
    Column("status", Text),  # silver | gold_candidate | scored
)


cluster = Table(
    "cluster",
    metadata,
    Column("cluster_id", Text, primary_key=True),
    # representative_posting_id references posting, but posting.cluster_id references cluster:
    # a mutual FK. Declared without a DB-level FK here to avoid a circular CREATE-order
    # constraint (trivial 1:1 in v0); the relationship is enforced in app logic.
    Column("representative_posting_id", Text),
    Column("posting_count", Integer),  # found-on-N-platforms = hot signal
    Column("first_seen", _TS),
    Column("last_seen", _TS),
)


score = Table(
    "score",
    metadata,
    # No PK in v0: score is 1:1 with cluster, so cluster_id is the natural key (matches the ERD).
    # No skills_extracted / sector / seniority here — those are silver-derived, on `posting`
    # (ADR-0016). The scorer reads them from posting, it does not re-extract.
    Column("cluster_id", Text, ForeignKey("cluster.cluster_id")),
    Column("score", Integer),  # 0-100
    Column("fit_category", Text),  # strong_fit | stretch | misaligned | near_miss
    Column("strengths", JSONB),
    Column("gaps", JSONB),
    Column("strategic_assessment", Text),
    Column("poster_type", Text),
    Column("legitimacy_verified", Boolean),
    Column("previous_score", Integer),  # for near-miss re-scoring
    Column("scored_at", _TS),
    Column("score_override", Integer),  # human correction → calibration data
    # 1:1 with cluster on the natural key — the unique key the upsert conflict-targets.
    UniqueConstraint("cluster_id", name="uq_score_cluster_id"),
)


profile = Table(
    "profile",
    metadata,
    Column("user_id", Text, primary_key=True),  # single-user now; the multi-user seam
    Column("profile", JSONB, nullable=False),  # skills, certs, projects, prefs
    Column("threshold", Integer),  # default 60 — gates shortlist + CV
    Column("hard_floor", Integer),  # default 50
    Column("near_miss_band", Integer),  # default 10
)


__all__ = [
    "metadata",
    "bronze_posting",
    "posting",
    "cluster",
    "score",
    "profile",
]
