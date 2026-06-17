# ADR-0012 — Model-agnostic LLM via Bedrock Converse (model id in config)

## Status
Accepted

## Context
The LLM does narrow text work — analyze / dissect / categorize JDs (scoring, skill & section extraction) plus CV tailoring. The originally-assumed model (Anthropic Claude) is currently blocked by a brand-new-account **0 / non-adjustable** Bedrock daily-token quota ([ERR-001](../ledgers/errors.md)). Tarig's requirement: **switching models must never be a bottleneck.**

## Decision
A single provider-agnostic **`LlmClient` port** over **Amazon Bedrock's Converse API** (one request/response shape across providers). The **model id lives in config, per task** (`llm.scoring_model`, `llm.extraction_model`, …) so any model is a one-line swap, and a strong model can be paired with expensive judgment while a cheap model handles high-volume extraction. **Structured output via prompt + Pydantic validation** (portable across models), not provider-specific JSON/tool modes. Current candidate: `moonshot.kimi-k2-thinking` (ACTIVE, `ON_DEMAND`; API invoke pending confirmation — ERR-001); revert to `us.anthropic.claude-sonnet-4-6` when the account quota lifts — a config change either way.

## Alternatives Considered
- **Hardcode one provider (Anthropic) + its SDK.** Rejected: a single quota/availability problem (exactly what happened) becomes a *code* change, and couples us to one vendor.
- **Provider-specific structured-output modes** (e.g. Anthropic tool-use JSON). Rejected as the default: not portable across models; prompt + Pydantic works everywhere and stays model-agnostic.
- **Non-Bedrock** (call providers directly, or a gateway like LiteLLM). Rejected: Bedrock Converse already gives a unified multi-provider API *inside* our AWS/IAM/Secrets boundary; a separate gateway is extra infra we don't need at this scale.

## Consequences
- **Easier:** swap models by editing config; per-task model selection (quality vs cost); routes around vendor-specific quota/availability blocks — the ERR-001 motivation.
- **Harder:** keep to the Converse common feature-set (system prompt + messages + prompt-based JSON); a model with special needs gets a thin per-model shim. Reasoning models (Kimi "thinking") emit extra tokens → cost/latency; fine for now, swap a cheaper model for high-volume steps later.
- **Impact:** scorer + cv_tailor + skill-extraction all depend on the `LlmClient` port; the **active model is a config value, not a code constant** — "model choice is a bottleneck" is designed out.
