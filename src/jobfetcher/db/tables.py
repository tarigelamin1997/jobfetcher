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
    Date,
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


score_event = Table(
    "score_event",
    metadata,
    # Append-only scoring-lineage log (migration 0004): one immutable row per scoring event,
    # while `score` stays the 1:1 "current judgment" upsert. `save_score` dual-writes both in
    # ONE transaction; nothing ever updates or deletes a row here, so the full score history
    # (and the model + profile that produced each score) survives every reassess/replay.
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("cluster_id", Text, ForeignKey("cluster.cluster_id"), nullable=False, index=True),
    Column("score", Integer, nullable=False),  # 0-100
    Column("fit_category", Text, nullable=False),
    Column("strengths", JSONB),
    Column("gaps", JSONB),
    Column("strategic_assessment", Text),
    Column("poster_type", Text),
    Column("legitimacy_verified", Boolean),
    Column("previous_score", Integer),  # what save_score received — keeps events self-contained
    Column("scoring_model", Text, nullable=False),  # provenance: which model scored
    Column("profile_hash", Text, nullable=False),  # provenance: which profile+knobs it scored against
    Column("run_id", Text, index=True),  # correlation id
    Column("scored_at", _TS, nullable=False, server_default=text("now()")),
    # No implicit `RETURNING event_id` on INSERT: nothing reads the id back, and this is the
    # schema's first server-generated PK — RETURNING has never been exercised over the Aurora
    # Data API dialect (the ERR-004/005 lesson: dialect divergence only surfaces live).
    implicit_returning=False,
)


profile = Table(
    "profile",
    metadata,
    Column("user_id", Text, primary_key=True),  # single-user now; the multi-user seam
    Column("profile", JSONB, nullable=False),  # skills, certs, projects, prefs
    Column("threshold", Integer),  # default 60 — gates shortlist + CV
    Column("hard_floor", Integer),  # default 50
    Column("near_miss_band", Integer),  # default 10
    Column("profile_hash", Text),  # hash of the profile+knobs last synced (lineage; nullable)
)


run_log = Table(
    "run_log",
    metadata,
    # The send-once guard (Step 7 / VG4): one row per (run_date, user) records that the daily
    # digest was already sent, so a re-invocation for the same date never double-emails. The
    # pipeline's other stages are idempotent via their own upserts; this table guards the email,
    # the one side effect that cannot be made idempotent at the source (SES has no dedup key).
    Column("run_date", Date, primary_key=True),
    Column("user_id", Text, primary_key=True),
    Column("run_id", Text),  # the run that actually sent (correlation/audit)
    Column("digest_sent_at", _TS, nullable=False, server_default=text("now()")),
)


__all__ = [
    "metadata",
    "bronze_posting",
    "posting",
    "cluster",
    "score",
    "score_event",
    "profile",
    "run_log",
]
