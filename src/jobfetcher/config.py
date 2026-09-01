"""Runtime configuration. The LLM provider + model live here, not in code — swapping
provider or model is a config change, never a rewrite (ADR-0012 / ADR-0017)."""
from __future__ import annotations

import os
from typing import Literal

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

    # --- Reasoning control (ERR-010 follow-up) --------------------------------------------
    # DeepSeek's v4 models are reasoners: `max_tokens` budgets reasoning AND content together,
    # and reasoning runs FIRST. Exhaust it and the API returns HTTP 200 with an EMPTY content
    # — which is how a whole run can silently produce nothing. Measured on a real 5 KB JD:
    #   dissect, reasoning off        →   425 tokens total, clean JSON
    #   score,   effort=low           → 1,224 tokens (886 reasoning + ~338 content)
    #   score,   effort default(high) → 4,096 tokens, ALL reasoning, content EMPTY
    # Note reasoning expands to fill the budget you give it (effort=low used 886 tokens at
    # max_tokens=4096 but 1,412 at 6,144), so a bigger budget is not a free safety margin.
    #
    # `reasoning=False` sends `thinking: {"type": "disabled"}` — right for extraction, which
    # only fills a fixed JSON schema. Keep it on for judgment work like scoring.
    reasoning: bool = True
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    timeout_s: float = Field(default=60.0, gt=0.0)
    # Transient-failure policy (ERR-006): retries apply ONLY to 429/5xx/connection errors —
    # auth (401) and model-not-found (404) always fail fast. 0 disables retrying.
    max_retries: int = Field(default=3, ge=0)
    backoff_base_s: float = Field(default=1.0, gt=0.0)


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
