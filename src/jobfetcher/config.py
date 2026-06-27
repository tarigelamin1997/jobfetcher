"""Runtime configuration. The LLM provider + model live here, not in code — swapping
provider or model is a config change, never a rewrite (ADR-0012 / ADR-0017)."""
from __future__ import annotations

import os

from pydantic import BaseModel, Field


class LlmConfig(BaseModel):
    """An OpenAI-compatible LLM backend. Defaults = v0 (DeepSeek, ADR-0017).

    Per-task models (ADR-0012): the cheap `deepseek-v4-flash` for high-volume dissection;
    a stronger model (e.g. `deepseek-v4-pro`) for scoring. Point `base_url`/`model` at any
    OpenAI-compatible host — DeepSeek, a local Ollama, OpenRouter — and it just works.
    """

    model_config = {"extra": "forbid"}

    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    secret_name: str = "jobfetcher/deepseek"   # Secrets Manager id holding the API key
    aws_region: str = "us-east-1"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    timeout_s: float = Field(default=60.0, gt=0.0)


_DB_URL_ENV = "JOBFETCHER_DB_URL"


class DbConfig(BaseModel):
    """The operational store connection (ADR-0018). One `connection_url` selects the backend:

      - local Postgres for dev/tests — e.g. `postgresql://user:pass@localhost:5432/jobfetcher`
        (the `sqlalchemy-aurora-data-api` dialect is bypassed; a real local Postgres is used).
      - Aurora via the RDS Data API when deployed — `postgresql+auroradataapi://:@/<db>?...`
        carrying the cluster ARN + secret ARN as query params (ADR-0014).

    The SQLAlchemy dialect is chosen by the URL scheme, so the *same* application code +
    Alembic run against both. The URL comes from `$JOBFETCHER_DB_URL` (never hardcoded — no
    secrets in code); pass it explicitly for tests that spin a throwaway Postgres.
    """

    model_config = {"extra": "forbid"}

    connection_url: str = Field(..., min_length=1)

    @classmethod
    def from_env(cls) -> "DbConfig | None":
        """Build from `$JOBFETCHER_DB_URL`. Returns `None` when unset (callers — and the
        DB integration test — treat that as 'no local DB available' and skip cleanly)."""
        url = os.environ.get(_DB_URL_ENV)
        if url and url.strip():
            return cls(connection_url=url.strip())
        return None
