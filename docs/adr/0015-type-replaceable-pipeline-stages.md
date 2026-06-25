# ADR-0015 — Type-replaceable pipeline stages (ports + config-selected strategies)

## Status
Accepted

## Context
The pipeline is an evolutionary architecture: stages get *upgraded by type* over time — `lingua`→LLM, deterministic-filter→LLM-filter, one embedding model→another, JSearch→+Adzuna, Kimi→Claude. [ADR-0012](0012-model-agnostic-llm.md) already made the *LLM* model-agnostic (model id in config). Tarig's requirement: make that **pervasive** — every stage swappable **by type, not just scaled by volume** — so an upgrade is a config change + a new adapter, never a rewrite.

## Decision
**Every pipeline stage is a Strategy behind a Port**, with the concrete implementation **selected by config**. The ports:

| Port | Job | v0 strategy | Swap examples |
|---|---|---|---|
| `SourceAdapter` | fetch + normalize a source | JSearch | + Adzuna / Bayt (M2) |
| `Dissector` | LLM-dissect a JD → structured fields | cheap OpenAI-compat model (`deepseek-v4-flash`) | stronger model; fine-tuned extractor |
| `FilterStrategy` | cut silver → gold candidates | LLM filter | deterministic / embedding filter |
| `Embedder` | text → vector (pgvector) | Titan / Cohere (M2) | any embedding model |
| `Scorer` | score fit + explain | strong OpenAI-compat model (`deepseek-v4-pro`) | any model/provider (config) |
| (`Notifier`, `CVRenderer`) | later stages | — | — |

Each port is a small interface; strategies are registered + chosen via config (the same pattern as the model id). Swapping a stage's *type* is adding an adapter + flipping config — no downstream rewrite.

## Alternatives Considered
- **Hardcode each stage's implementation.** Rejected: a single upgrade (lingua→LLM, or a new source) becomes a code change rippling downstream — exactly the bottleneck the evolutionary model exists to avoid.
- **Only the LLM is swappable (ADR-0012), the rest hardcoded.** Rejected as too narrow: dissection, filtering, embedding, and the source all get upgraded over the roadmap; they deserve the same seam.

## Consequences
- **Easier:** upgrades are config + an adapter; A/B-ing a strategy is trivial; the architecture *reads* as deliberately evolvable (a senior signal). Tests target the port interface, so a swapped strategy reuses the same gate.
- **Harder:** a little upfront indirection (ports/registries) vs inline calls — justified by the evolutionary roadmap, kept minimal (a port is a thin interface, not a framework).
- **Impact:** generalizes [ADR-0012]; underpins the M2 multi-source ([ADR-0010]) + dedup ([ADR-0005]) swaps and the silver `Dissector` ([ADR-0016]). A governing tenet in [00-design-philosophy](../00-design-philosophy.md).
