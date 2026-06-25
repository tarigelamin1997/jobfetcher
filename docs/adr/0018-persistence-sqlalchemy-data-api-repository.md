# ADR-0018 — Persistence access: SQLAlchemy + aurora-data-api dialect, behind a `Repository` port

## Status
Accepted

## Context
[ADR-0014](0014-operational-store-aurora-serverless-data-api.md) put the operational store on **Aurora Serverless v2 + the RDS Data API** (Lambda outside any VPC). That settles *where* data lives and *how the Lambda reaches it* (HTTPS Data API), but leaves two build questions open: (1) **how the application code issues SQL** (raw boto3 `rds-data` vs an abstraction), and (2) **how the storage layer is tested** — and **LocalStack does not meaningfully mock the RDS Data API**, so the build-plan's "LocalStack + local Postgres" testing line needs a concrete mechanism. The choice also interacts with [ADR-0015](0015-type-replaceable-pipeline-stages.md)'s type-replaceability tenet, whose ports list did **not** include a storage seam.

## Decision
The pipeline talks to Postgres through **SQLAlchemy Core + the `sqlalchemy-aurora-data-api` dialect**, wrapped behind a **`Repository` port**:
- **One code path, two backends by connection URL.** The same SQLAlchemy statements run against a **local Postgres** (a `postgresql://…` URL, for fast unit/integration tests) and against **Aurora via the Data API** (an `postgresql+auroradataapi://…` URL, deployed) — the dialect adapts the wire calls; the application code is identical. **Alembic** migrations use the same dialect, so the schema is built the same way locally and in AWS.
- **`Repository` is a port** ([ADR-0015](0015-type-replaceable-pipeline-stages.md)): a thin interface (`save_posting`, `get_posting`, `upsert_bronze`, …) with a `PostgresRepository` adapter. This closes the storage-seam gap in the ports list — persistence becomes swappable by type (any SQL store; an in-memory fake in tests) instead of hardcoded.
- **Local DB tests use a real local Postgres** (testcontainers / a CI Postgres service), *not* a Data-API mock — higher fidelity than a stub, and the dialect guarantees parity with deployed Aurora. **LocalStack/moto stay for S3 + Secrets** only.

## Alternatives Considered
- **Raw boto3 `rds-data` client + hand-written SQL.** Minimal dependencies, but: no local↔cloud parity (every test must *stub* the Data API — there's no real local equivalent), SQL and result-shape handling are hand-rolled and error-prone, and Alembic would need a separate connection path. Rejected — the lost testability and the bespoke SQL plumbing aren't worth the saved dependency.
- **Rely on a LocalStack Data-API mock for integration tests.** LocalStack's `rds-data` coverage is partial/uneven; tests would pass against a mock that doesn't behave like Aurora. Rejected — a high-fidelity local Postgres via the dialect is both simpler and more trustworthy.
- **A full ORM (SQLAlchemy ORM / models layer).** Rejected for v0 as more machinery than the handful of tables need; SQLAlchemy **Core** (explicit tables + statements) keeps it minimal (P1) while still giving the dialect + Alembic benefits. The ORM stays an easy later add behind the same `Repository` port.

## Consequences
- **Easier:** the storage layer is unit/integration-testable on a real local Postgres with zero AWS; deployed code is byte-identical save the connection URL; Alembic works the same everywhere; persistence is now a swappable port, consistent with [ADR-0015](0015-type-replaceable-pipeline-stages.md).
- **Harder:** two more dependencies (`sqlalchemy`, `sqlalchemy-aurora-data-api`) and a thin `Repository` indirection vs inline boto3 — justified by the testability + parity payoff, kept minimal (Core, not ORM).
- **Impact:** realizes [ADR-0014](0014-operational-store-aurora-serverless-data-api.md)'s store with a tested access pattern; adds the `Repository` row to [ADR-0015](0015-type-replaceable-pipeline-stages.md); concretizes build-plan Steps 1–3 (the `migrations/` Alembic setup, the `Repository` port skeleton, and the local-Postgres testing mechanism).
