# ADR-0016 — LLM dissection at the silver layer (every posting)

## Status
Accepted

## Context
The analytical plane (Skill-Demand tracker, Sector Intelligence) measures the **market** — which skills/sectors/titles are in demand across **all** GCC DE postings. That requires the structured fields (skills, requirement levels, sector, normalized title, …) extracted from **every** posting, not only the gold (candidate-matched) ones. Extracting at scoring (post-gold) would bias "demand" toward Tarig's own profile. The question: where does the JD dissection happen, and by what?

## Decision
**Dissect every deduped posting with an LLM at the silver layer** (the `Dissector` port, [ADR-0015](0015-type-replaceable-pipeline-stages.md)) — *no pre-gate* (Tarig chose maximal market signal). The dissection returns a **structured-output contract** (Pydantic, temp 0): `skills[]` each with requirement level `{must|nice|implied}`, sector, normalized title, seniority, location, language, … — the raw material that **populates the dimensional tables** ([ADR-0011](0011-dimensional-analytical-model.md)). **Language is just one output field** — this *replaces* the `lingua` library. Per-task models ([ADR-0012](0012-model-agnostic-llm.md)): a **cheap/fast model for the bulk dissection**, the **strong model for scoring**. Scoring then judges *fit* on already-structured data.

**Countering JD variation** (phrasing, language, company): the LLM understands equivalence ("required" ≈ "essential" ≈ "must-have"); a **canonicalization layer** maps extracted entities to a controlled vocabulary (`dim_skill` synonyms — "Postgres" → "PostgreSQL") so counts aggregate across JDs; the structured contract + temp 0 give a stable shape; immutable bronze lets us re-dissect with a better model (replay).

## Alternatives Considered
- **Extract at gold/scoring only (the prior design).** Rejected: skills extracted only from candidate-matched postings bias the market analytics — you'd measure *your* fit, not market demand.
- **Deterministic / `lingua` silver (cheap, no LLM).** Rejected: a library can do language-ID + fingerprints but **cannot produce the structured skills/level/sector matrices** the analytics need; the rich dissection *is* the point.
- **Cheap English pre-gate before dissection.** Considered (saves tokens on non-English JDs); Tarig chose to dissect everything for maximal market signal.

## Consequences
- **Easier:** the dimensional model is populated market-wide and early (from lossless silver); scoring simplifies to fit-judgment on structured data; one dissection serves analytics + filter + score.
- **Harder / honest:** **reverses "only gold reaches the LLM"** — the LLM now runs on every deduped posting (cost ↑, mitigated by the cheap model + low volume). *(Originally this re-coupled silver-onward to the Bedrock quota — [ERR-001](../ledgers/errors.md); [ADR-0017](0017-llm-transport-openai-compatible-deepseek.md) then routed the LLM to DeepSeek over the OpenAI-compatible API — no new-account gate — so the **whole pipeline is now live-runnable**, and still unit/integration-testable with the LLM mocked.)*
- **Impact:** supersedes the earlier "silver = pure Python / `lingua` / quota-independent" capture; the `Dissector` is a config-swappable strategy ([ADR-0015]); embeddings + pgvector blocking remain M2 ([ADR-0005]).
