# Changelog

All notable changes to JobFetcher are recorded here. Format follows [Keep a Changelog](https://keepachangelog.com/); the project ships **semantic-versioned releases per migration** ([roadmap](docs/03-roadmap.md), [ADR-0013](docs/adr/0013-enforcement-gate-trio-branch-pr.md)). Until **v0.1** ships, everything lives under *Unreleased*.

The ***why*** behind every entry is the [session decision journal](docs/01-session-decision-journal.md); formal decisions are [ADRs](docs/adr/); the raw reasoning trail is the [working document](docs/session-log/working-document.md).

## [Unreleased] — v0.1 in progress (Step 0)

### 🏆 Milestones

- **2026-06-24 — THE LLM IS LIVE.** After weeks blocked by a Bedrock new-account daily-token quota of **0** ([ERR-001](docs/ledgers/errors.md)), the scoring/dissection path is unblocked. We stopped waiting on AWS and **routed around it** — the LLM transport now runs on the **OpenAI-compatible API with DeepSeek** (`deepseek-v4-flash`), verified end-to-end (`scripts/deepseek_smoke.py` → HTTP 200). The whole pipeline (bronze → silver dissection → gold → score) is now **live-runnable**, not blocked work. Full arc + reasoning: [journal §18](docs/01-session-decision-journal.md). ([ADR-0017](docs/adr/0017-llm-transport-openai-compatible-deepseek.md))

### Added

- **[ADR-0017]** — LLM transport = OpenAI-compatible API; v0 provider = **DeepSeek**; Bedrock parked. The model-agnostic port now swaps *provider*, not just model, via config (`base_url`/`api_key`/`model`) — one `OpenAICompatLlmClient` serves DeepSeek, Ollama, Anthropic-direct, or Bedrock.
- `scripts/deepseek_smoke.py` — key-free smoke test that proves the DeepSeek unblock.
- DeepSeek API key in **Secrets Manager** (`jobfetcher/deepseek`).
- **[ADR-0016]** — LLM dissection at the silver layer on *every* posting → populates the market-wide dimensional model.
- **[ADR-0015]** — type-replaceability as a first-class tenet (P3): every stage a config-selected strategy behind a port (`SourceAdapter`/`Dissector`/`FilterStrategy`/`Embedder`/`Scorer`).
- **[ADR-0014]** — operational store = Aurora Serverless v2 + RDS Data API, Lambda outside any VPC (resolves D-v0-1).
- **[ADR-0013]** — enforcement gate-trio (`/start-step` · `/review-step` · `/close-step`) + branch/PR workflow.
- First code: validated `SearchSpec` + JSearch coverage probe (`scripts/`).
- Full context-survival docs: journal Part 2 (build-phase reasoning) + the verbatim working-document archive.

### Changed

- **LLM transport: Bedrock Converse → OpenAI-compatible API** ([ADR-0012] retitled) — supersedes the interim Kimi-on-Bedrock choice (Kimi was only picked because it was *on* Bedrock).
- **Silver: `lingua` language-detect → LLM dissection on every posting** ([ADR-0016]) — "only gold reaches the LLM" reversed (market-wide analytics need all postings).
- **ERR-001: Open → Mitigated** — worked around via DeepSeek; the Bedrock quota is still 0 but no longer blocks. AWS case `178220019100382` stays open as *optional*.

### Notes — honest tradeoffs (read before changing the LLM)

- DeepSeek is **China-hosted** and its ToS permits **training on API inputs** — accepted for *public JD text*; the `Scorer` (which sends the CV/profile) can flip to local Ollama or Anthropic-direct via config ([ADR-0017]).
- The DeepSeek API needs a **funded balance** — its "free signup tokens" did not apply (returned `402` until a small top-up).
