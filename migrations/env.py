"""Alembic environment, wired to JobFetcher's schema (ADR-0018).

The connection URL comes from `$JOBFETCHER_DB_URL` (via `DbConfig`) — never hardcoded, no
secret in the repo. The URL scheme selects the backend, so `alembic upgrade head` builds the
*same* schema on a local Postgres (tests) and Aurora via the Data API (deployed). The target
metadata is the single Core schema in `jobfetcher.db.tables`, so migrations + the app never
drift.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from jobfetcher.config import DbConfig
from jobfetcher.db.tables import metadata as target_metadata

# the Alembic Config object (access to alembic.ini values)
config = context.config

# Resolve the DB URL from the environment and inject it (so alembic.ini holds no credential).
_db = DbConfig.from_env()
if _db is not None:
    # Escape `%` for configparser interpolation: the Aurora Data API URL carries `%`-encoded
    # ARNs (e.g. `%3A`), which `set_main_option` would otherwise read as interpolation syntax.
    config.set_main_option("sqlalchemy.url", _db.connection_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Emit SQL without a live connection (uses just the URL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection built from the resolved URL."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
