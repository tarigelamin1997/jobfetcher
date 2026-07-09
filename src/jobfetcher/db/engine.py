"""Engine factory. One place builds the SQLAlchemy engine from a `connection_url`; the URL
scheme picks the backend (local Postgres vs Aurora Data API — ADR-0018), so this is the only
seam that differs between tests and deployed. Also home to `wait_for_db_resume` (ERR-009):
the one place that knows how Aurora's scale-to-zero resume signal looks from inside SQLAlchemy.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import Engine, create_engine, text

log = logging.getLogger(__name__)

# Aurora Serverless v2's scale-to-zero resume signal (ERR-009). botocore GENERATES the
# exception class from the RDS Data API service model at runtime, so it cannot be imported —
# match it by class NAME (+ the documented message substring as belt) anywhere in the chain.
_RESUME_EXCEPTION_NAME = "DatabaseResumingException"
_RESUME_MESSAGE = "resuming after being auto-paused"


def make_engine(connection_url: str) -> Engine:
    """Build a SQLAlchemy engine from a connection URL.

    `future=True` is the 2.x default; `pool_pre_ping` guards against a paused Aurora
    Serverless connection going stale between daily runs (harmless for local Postgres).
    """
    return create_engine(connection_url, pool_pre_ping=True)


def _is_aurora_resuming(exc: BaseException) -> bool:
    """True iff Aurora's resume signal is the ROOT CAUSE of `exc`.

    SQLAlchemy wraps the driver error (`StatementError.orig`) and plain `raise … from …`
    chains use `__cause__`, so walk both. Matched by class name (the botocore class is
    dynamically generated — see above) OR the message substring (belt, in case a wrapper
    flattens the chain into a string). Anything else is a real failure — never retried."""
    seen: set[int] = set()
    node: BaseException | None = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        if type(node).__name__ == _RESUME_EXCEPTION_NAME or _RESUME_MESSAGE in str(node):
            return True
        node = getattr(node, "orig", None) or node.__cause__
    return False


def wait_for_db_resume(engine: Engine, *, budget_s: float = 90.0, interval_s: float = 5.0) -> None:
    """Block until the DB answers a trivial `SELECT 1`, absorbing ONLY Aurora Serverless v2's
    scale-to-zero resume window (ERR-009 — a run that catches the cluster asleep died at its
    first DB touch; resume takes ~15–30s, and `maximum_retry_attempts=0` means a dead run
    stays dead). Retries the resume signal every `interval_s` until `budget_s` of waiting is
    spent, then re-raises the last resume error; ANY other exception re-raises IMMEDIATELY —
    this must never mask a real failure. On a warm cluster (and always on local Postgres,
    where the exception doesn't exist) the first `SELECT 1` succeeds in ~ms. Each wait is
    logged at INFO so cold starts are visible in CloudWatch."""
    waited = 0.0
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:
            if not _is_aurora_resuming(exc):
                raise  # a real failure — the resume-retry must never swallow it
            if waited + interval_s > budget_s:
                raise  # budget exhausted — surface the resume error loudly
            log.info(
                "Aurora resuming — waiting %.0fs (attempt %d, %.0fs budget left): %s",
                interval_s,
                attempt,
                budget_s - waited,
                exc,
            )
            time.sleep(interval_s)
            waited += interval_s
