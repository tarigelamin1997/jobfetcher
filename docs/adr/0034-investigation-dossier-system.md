# ADR-0034 — Investigation-dossier system + a typed-graph seam

**Status:** Accepted · **✅ adopted 2026-07-20** · docs/process-only (no src/tests/infra)

## Context
The squad's [Investigator role](../../.agents/roles/investigator.md) already does deep, read-only, evidence-first verification of a bottleneck and hands back a minimal-fix brief — but that brief is **ephemeral** (produced in-context during a squad run, then scattered into the plan file and the eventual ADR). We wanted a **durable, dedicated place** where a bottleneck is investigated, *proven to exist* (or killed), root-caused, and turned into a complete, testable handoff **any agent can pick up and fix** — produced strictly read-only, writing **no code**. The existing layers don't cover this: the [backlog](../ledgers/backlog.md) is a one-paragraph *signal* (What/Why/So-what), and an [ADR](README.md) is the *decision* — nothing holds the *verified investigation* in between.

Two external tools were proposed alongside it: a **Linear** MCP (issue tracking) and a **Neo4j** MCP (a graph DB to "connect the dots"). Both were evaluated against the defensibility rubric ([00-design-philosophy](../00-design-philosophy.md)) and **declined as ahead-of-need** (see Alternatives).

## Decision
Add a durable **investigation-dossier layer** — `docs/investigations/`, one evidence-verified dossier per bottleneck (`INV-NNN-<slug>.md`) — sitting between the backlog signal and the ADR decision:

```
backlog.md (observation) → docs/investigations/INV-NNN.md (verified + fix plan + gate) → Surgeon builds → ADR + CHANGELOG
```

- **Contract** ([`_TEMPLATE.md`](../investigations/_TEMPLATE.md)): frontmatter (`id/status/severity/…`) → Problem → **Does it exist? (re-runnable evidence, or KILL)** → Mechanism → Blast radius → Fix plan (files + reuse, *not code*) → Validation gate (**behavioral + a negative case**) → Out-of-scope → **Connections (typed)** → Handoff. Lifecycle: `open → verifying → verified → handoff-ready → in-progress → fixed | killed`.
- **Producer**: [`/investigate <candidate>`](../../.claude/commands/investigate.md) — the Investigator identity, **read-only on the codebase**, whose `## Allowed mutations` whitelist is *only the dossier + the index* (that whitelist **is** the "writes no code" enforcement). The role's `writes:` is carved from `none` to *"its own dossier only (never code/infra)."*
- **Typed-graph seam** (kept from the Neo4j idea): the Connections section is a list of typed edges — `<verb> → <target>`, vocab `causes/caused-by · touches · blocks/blocked-by · depends-on · relates-to · duplicates/supersedes` — a **graph-in-Markdown** that a future ~30-line `networkx`/DuckDB projector can read (the repo stays the source of truth). **No graph store built now.**

## Alternatives Considered
- **A Linear MCP (external issue tracker).** Rejected: it's an **out-of-repo second source of truth** — a Linear issue isn't in the repo, so a fresh session/agent can't *resume from files alone*, breaking the project's #1 principle ("the repo is the memory"). It duplicates the in-repo `README.md` index + GitHub PRs, and adds a SaaS account + auth for a **single-operator** project. It would earn its place with a **team** (assignment/notifications/cycles) — not here. Even as a portfolio line it's net-negative (it *harms* repo-is-memory). Reconsider if this becomes a multi-person effort.
- **A Neo4j MCP (graph DB) now.** Rejected as ahead-of-need: at ~4 open bottlenecks / ~34 ADRs / ~15 modules the graph is small enough that the **linked docs already are the graph** (dossier Connections, ADR cross-refs, the interface-contracts ledger). A standing graph DB + an ingestion pipeline to populate it + an MCP + auth + *another out-of-repo store* is "complexity is entropic / build the seam, not the future." Same call as **[Great Expectations (§34, deferred)](../01-session-decision-journal.md)** — right idea, wrong scale. **Kept the graph *thinking*** as the typed-connection seam; **adopt-when trigger:** when link-reasoning fails, project the typed edges into a local `networkx`/DuckDB graph (repo still source of truth), and adopt a graph *server* only if query load demands one.
- **One big `investigations.md` file** (vs one dossier per bottleneck). Rejected: one-per-bottleneck mirrors the repo's one-ADR-per-decision / one-error-per-ERR convention — diffable, linkable, independently statused.
- **Locate it under `.agents/`** (vs `docs/`). Rejected: `docs/investigations/` is repo-memory + portfolio-visible; `.agents/` is process machinery. The dossier is a durable *artifact* (like ADRs), so it belongs in `docs/`.

## Consequences
- **Easier:** a bottleneck's full investigation is durable, evidence-first, and a complete handoff — any agent (or a future session) can fix it without re-discovering it; a *killed* investigation is a first-class, recorded outcome (proving something isn't real is as valuable as fixing it).
- **A rare portfolio signal (honest):** a legible, evidence-first investigation trail — *separate* from decisions (ADRs) and signals (backlog) — shows verify-before-build discipline and complete, testable handoffs. Defensible because it's how the project already works, just made durable — not theater.
- **Bounded:** all Markdown/config, no new dependency, no infra; the graph store + the external tracker stay deferred with named triggers so they aren't re-litigated.
- **Registered:** the [procedure registry](../ledgers/procedure-registry.md), both doc-maps ([CLAUDE.md](../../CLAUDE.md) + [05-methodology](../05-methodology.md)), and the [backlog](../ledgers/backlog.md) point at it; the first worked dossier is **INV-001** (the dark feedback loop), verified live read-only.
