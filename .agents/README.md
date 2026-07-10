# `.agents/` — the agentic squad, model- & runner-agnostic

This folder is the **portable definition of how JobFetcher solves a bottleneck with agents.** It is written so *any capable LLM on any runner* (Claude Code today; ChatGPT, Gemini, or another harness tomorrow) can read it and execute the workflow faithfully — the design principle is the same **model-agnostic** bet the project already made for the LLM itself ([ADR-0012](../docs/adr/0012-model-agnostic-llm.md) / [ADR-0017](../docs/adr/0017-llm-transport-openai-compatible-deepseek.md)), applied one level up: to the agent roles.

**The idea:** separate the durable **identity/contract** (what a role *is* — its goal, scope, inputs, outputs, boundaries) from the swappable **runtime binding** (which model runs it, which tools it gets, which harness orchestrates it). The identities are the portable asset; the bindings are per-runner and thin.

## What's here

| File | What it is |
|---|---|
| [`agentic-workflow.md`](agentic-workflow.md) | The **procedure** — principles, the pipeline, the severity gate, how to invoke it, provenance, a worked example. Runner-neutral, with the platform-specific bits isolated in a clearly-labeled "current-runner bindings" section. |
| [`roles/investigator.md`](roles/investigator.md) | Identity: the fresh, read-only scout — verify, measure, brief, or **kill**. |
| [`roles/surgeon.md`](roles/surgeon.md) | Identity: the minimal-diff builder — smallest change, in an isolated workspace. |
| [`roles/examiner.md`](roles/examiner.md) | Identity: the fresh adversarial reviewer — one agent, two passes. |
| [`roles/orchestrator-scribe.md`](roles/orchestrator-scribe.md) | Identity: the coordinator + record-keeper (Claude, in the current runner). |

The roster is **extensible** — add or drop roles per the procedure when a workflow genuinely needs it.

## How to invoke

Say *"run the agentic workflow for X"* (or *"use the squad"*). The orchestrator executes [`agentic-workflow.md`](agentic-workflow.md), reading each role's identity from `roles/`.

## Porting to another runner (deferred — not built yet)

Everything here is **runner-neutral by design**, but the *enforcement* of each identity (tool-scoping, one-writer isolation, the PR/CI/review machinery) is currently done by prompting + Claude Code's conventions. When there's a real second runner, add a thin **adapter** per runner that binds these identities to that platform's native format — e.g. generate `.claude/agents/*.md` (to make the Investigator *physically* read-only in Claude Code), an OpenAI Agents SDK / Gemini equivalent, and optionally align the entry file with the emerging cross-tool [`AGENTS.md`](https://agents.md) convention. **Deferred on purpose** (P1): we don't build multi-runner infra before a second runner is real.
