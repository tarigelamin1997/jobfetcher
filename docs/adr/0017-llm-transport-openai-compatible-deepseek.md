# ADR-0017 — LLM transport: OpenAI-compatible API; v0 provider = DeepSeek (Bedrock parked)

## Status
Accepted (supersedes the Bedrock-Converse *transport* of [ADR-0012](0012-model-agnostic-llm.md)) · **✅ Verified live 2026-06-24** — `scripts/deepseek_smoke.py` → HTTP 200 from `deepseek-v4-flash` (see [ERR-001](../ledgers/errors.md) + [journal §18](../01-session-decision-journal.md))

## Context
[ADR-0012](0012-model-agnostic-llm.md) made the LLM model-agnostic but bound the *transport* to Amazon Bedrock's Converse API. Bedrock then blocked us: the new-account **daily-token quota = 0 / non-adjustable** ([ERR-001](../ledgers/errors.md)) throttled *every* model (Anthropic and Kimi alike) for weeks, with no lift timeline. Waiting on AWS is not a plan. The architecture's whole point ([ADR-0015](0015-type-replaceable-pipeline-stages.md), P2) is that a blocked stage is broken by the *minimal migration* — here, swapping the LLM transport behind the `LlmClient` port. A local option was checked (RTX 3050, **4 GB VRAM** → 3–7B models only, and a laptop can't run the deployed serverless pipeline).

## Decision
**Transport = the OpenAI-compatible Chat Completions API** (the de-facto multi-provider standard), behind the existing `LlmClient` port. The **backend is pure config**: `base_url` + `api_key` (Secrets Manager) + `model` per task. One `OpenAICompatLlmClient` adapter therefore serves **any** OpenAI-compatible host — DeepSeek, Ollama (local), OpenRouter, Together, vLLM — and Anthropic-direct or Bedrock are reachable via a thin second adapter behind the same port.

**v0 default provider = the DeepSeek API** (`https://api.deepseek.com`, OpenAI-compatible). **Per-task models** ([ADR-0012](0012-model-agnostic-llm.md)): `deepseek-v4-flash` (cheap) for the bulk silver `Dissector` + gold `FilterStrategy`; `deepseek-v4-pro` (strong) for the `Scorer`. *(Use the v4 ids — the `deepseek-chat`/`deepseek-reasoner` aliases retire 2026-07-24.)* Structured output stays prompt + Pydantic (portable).

**Bedrock is parked** as one possible backend; **[ERR-001](../ledgers/errors.md) → Mitigated** (worked around — the quota is still 0 but no longer gates us). The earlier "chosen model = Kimi K2 via Converse" is moot (Kimi was chosen only because it was on Bedrock).

## Alternatives Considered
- **Stay on Bedrock, wait for the quota.** Rejected: blocked for weeks, no timeline, and couples the whole pipeline to one AWS-account gate.
- **Bedrock Converse as the only transport (the prior design).** Rejected: single-provider fragility — exactly the failure we hit. The port exists so a provider problem is config, not a rewrite.
- **Anthropic API direct (Claude).** Strong — best privacy (US-hosted, no training on API data) and serverless-ready — but ~10× DeepSeek's per-token cost. **Kept as the config-swap privacy/quality fallback**, not the v0 default.
- **Local Ollama only.** Rejected as the default: 4 GB VRAM caps quality (3–7B; not real DeepSeek/Claude), slower, and a laptop can't run the deployed daily cloud pipeline. Remains the $0/offline/privacy backend (a config swap).

## Consequences
- **Easier:** unblocks the LLM **today** — the whole pipeline (bronze → silver dissection → gold → score) becomes live-runnable once the DeepSeek key is in Secrets Manager; ~$0.50–1/mo at our volume (5M free signup tokens cover the backfill); the provider is now a config value, so "a provider is blocked" can never again be a code change.
- **Harder / honest:** **data leaves AWS** — JD text and (at scoring) the profile go to DeepSeek's **China-hosted** servers, whose ToS permits training on API inputs. Accepted for v0 (public JDs are low-risk); the port lets us flip *scoring* (the PII step) to local Ollama or Anthropic-direct later via config. Slightly weakens the "all-AWS" story — but the model-agnostic, multi-backend framing is a *stronger* portfolio signal than Bedrock-only, and the rest of the stack stays on AWS.
- **Impact:** supersedes ADR-0012's transport (Converse → OpenAI-compatible); the `Dissector`/`FilterStrategy`/`Scorer` strategies ([ADR-0015](0015-type-replaceable-pipeline-stages.md)) all run on it; ERR-001 reframed Open → Mitigated.
