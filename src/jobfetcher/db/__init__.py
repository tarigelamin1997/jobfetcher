"""Database schema (SQLAlchemy Core) + the engine factory. The schema is defined once here
and is the single source the `PostgresRepository` adapter and Alembic both build from —
local Postgres (tests) and Aurora via the Data API (deployed) are the same code (ADR-0018)."""
