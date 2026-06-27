"""Engine factory. One place builds the SQLAlchemy engine from a `connection_url`; the URL
scheme picks the backend (local Postgres vs Aurora Data API — ADR-0018), so this is the only
seam that differs between tests and deployed."""
from __future__ import annotations

from sqlalchemy import Engine, create_engine


def make_engine(connection_url: str) -> Engine:
    """Build a SQLAlchemy engine from a connection URL.

    `future=True` is the 2.x default; `pool_pre_ping` guards against a paused Aurora
    Serverless connection going stale between daily runs (harmless for local Postgres).
    """
    return create_engine(connection_url, pool_pre_ping=True)
