"""Runtime configuration. The LLM provider + model live here, not in code — swapping
provider or model is a config change, never a rewrite (ADR-0012 / ADR-0017)."""
from __future__ import annotations

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
