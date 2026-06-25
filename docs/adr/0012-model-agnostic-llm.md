# ADR-0012 — Model-agnostic LLM via OpenAI-compatible API (model + base_url in config)

## Status
Accepted *(transport updated by [ADR-0017](0017-llm-transport-openai-compatible-deepseek.md): Bedrock Converse → OpenAI-compatible API)*

## Context
The LLM does narrow text work — analyze / dissect / categorize JDs (scoring, skill & section extraction) plus CV tailoring. The originally-assumed model (Anthropic Claude) is currently blocked by a brand-new-account **0 / non-adjustable** Bedrock daily-token quota ([ERR-001](../ledgers/errors.md)). Tarig's requirement: **switching models must never be a bottleneck.**

## Decision
A single provider-agnostic **`LlmClient` port**. The **model id lives in config, per task** (`llm.scoring_model`, `llm.extraction_model`, …) so any model is a one-line swap, and a strong model can be paired with expensive judgment while a cheap model handles high-volume extraction. **Structured output via prompt + Pydantic validation** (portable across models), not provider-specific JSON/tool modes. **Transport = the OpenAI-compatible Chat Completions API** ([ADR-0017](0017-llm-transport-openai-compatible-deepseek.md)) — the backend is config (`base_url` + `api_key` + `model`), so the *provider* is swappable too, not just the model. **v0 backend = the DeepSeek API** (`deepseek-v4-flash` cheap / `deepseek-v4-pro` strong); Anthropic-direct, local Ollama, and Bedrock-when-unblocked are config swaps.

## Alternatives Considered
- **Hardcode one provider (Anthropic) + its SDK.** Rejected: a single quota/availability problem (exactly what happened) becomes a *code* change, and couples us to one vendor.
- **Provider-specific structured-output modes** (e.g. Anthropic tool-use JSON). Rejected as the default: not portable across models; prompt + Pydantic works everywhere and stays model-agnostic.
- **Bedrock Converse as the transport** (the original choice here). **Superseded by [ADR-0017](0017-llm-transport-openai-compatible-deepseek.md):** the Bedrock new-account quota ([ERR-001](../ledgers/errors.md)) blocked *every* model for weeks, so the transport moved to the OpenAI-compatible API (DeepSeek default). The model-agnostic *principle* is unchanged — only the wire protocol + default provider.

## Consequences
- **Easier:** swap models by editing config; per-task model selection (quality vs cost); routes around vendor-specific quota/availability blocks — the ERR-001 motivation.
- **Harder:** keep to the common chat feature-set (system prompt + messages + prompt-based JSON); a model with special needs gets a thin per-model shim. v0 uses a cheap model (`deepseek-v4-flash`) for high-volume dissection and the strong model (`deepseek-v4-pro`) for scoring.
- **Impact:** scorer + cv_tailor + skill-extraction all depend on the `LlmClient` port; the **active model is a config value, not a code constant** — "model choice is a bottleneck" is designed out.
