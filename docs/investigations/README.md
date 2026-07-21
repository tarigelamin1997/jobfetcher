# Investigations — the durable Investigator layer

> **What this is.** One **evidence-verified dossier per bottleneck**, each in its own **case folder** (`INV-NNN-<slug>/README.md`, plus an optional `evidence/` for raw artifacts) — the durable form of the squad's [Investigator role](../../.agents/roles/investigator.md). A dossier answers, with proof, *does this problem exist? · what is it? · what causes it? · what's the minimal fix, and how do we know it worked?* — so **any agent can pick it up and fix it** without re-discovering it. It is produced **read-only** (via [`/investigate`](../../.claude/commands/investigate.md)); the investigation **writes no code** — only its own dossier. A closed case is a **self-contained archive**: it keeps both the *proposed* fix and the [**as-built Resolution**](#lifecycle-the-status-field) (what actually shipped + how to extend it), so a future phase can edit or build on it without re-reading the code.

**Where it sits.** The missing layer *between the raw signal and the decision*:

```
backlog.md          →   investigations/INV-NNN-<slug>/   →   Surgeon builds   →   ADR + CHANGELOG
(observation:           (VERIFIED: problem + evidence +      (from the dossier,    (the decision +
 What/Why/So-what)       root cause + fix plan + gate;        not an in-context     shipped record)
                         Resolution filled at close)          brief)
                                  ↑ this layer
```

No duplication: **backlog = the signal** ([ledgers/backlog.md](../ledgers/backlog.md)), **dossier = the verified investigation + handoff plan**, **ADR = the decision** ([adr/](../adr/)). A backlog item graduates to a dossier when it's worth investigating *properly*. Decided in [ADR-0034](../adr/0034-investigation-dossier-system.md).

## How to open one

`/investigate <candidate>` (e.g. `/investigate B-2`, or a one-line problem). It assumes the Investigator identity, verifies on real code/data (or **kills** the candidate), and emits/updates a dossier. It creates a **case folder** `INV-NNN-<slug>/` and writes the dossier as its `README.md` (copied from [`_TEMPLATE.md`](_TEMPLATE.md)); raw artifacts (query dumps, logs, before/after) go in a sibling `evidence/`. Or copy the template by hand into a new folder. A dossier is **not `handoff-ready` until every section carries evidence** (a presence check is no gate — the project's standard, self-applied).

## Lifecycle (the `status` field)

`open` → `verifying` → `verified` → `handoff-ready` → `in-progress` → `fixed`, or **`killed`** (with the evidence it isn't real). `killed` is a first-class, valued outcome — proving a bottleneck isn't real is as useful as fixing one.

**Archived, not discarded — the close-out.** When a fix ships, the Surgeon fills the dossier's **Resolution — as-built** section (what was *actually* built, which rung was taken + any divergence from the plan, the key files/decisions, the PR/ADR/CHANGELOG links, and the seams for extending it later) and sets `status: fixed`. The dossier is **not deleted** — a closed case keeps *both* the proposed fix and the as-built record, so the whole `INV-NNN-<slug>/` folder stays a durable, self-contained reference a later phase can edit or build on without re-reading the code.

## Typed connections (the graph seam)

Every dossier's **Connections** section is a list of typed edges — a graph-in-Markdown — so the investigation graph is *machine-parseable* without a graph database. Grammar: `<verb> → <target>`, where the verb is from this controlled vocabulary and the target is a dossier ID, `file:path`, an ADR/roadmap item, or an external prerequisite.

| Verb | Meaning |
|---|---|
| `causes` / `caused-by` | this bottleneck causes / is caused by the target |
| `touches` | the fix touches this file/component |
| `blocks` / `blocked-by` | this blocks / is blocked by the target (a capability or an external prereq) |
| `depends-on` | needs the target to exist first |
| `relates-to` | context (an ADR, a design doc) |
| `duplicates` / `supersedes` | same problem as / replaces the target |

**Deferred by design ([ADR-0034](../adr/0034-investigation-dossier-system.md)):** a real graph store (Neo4j) and an external tracker (Linear) were evaluated and **declined as ahead-of-need** — at this scale the linked docs *are* the graph, and an out-of-repo store would break "the repo is the memory." **Adopt-when trigger:** when the investigation/ADR/code graph can no longer be reasoned about in linked docs, a ~30-line script projects these typed edges into `networkx`/DuckDB (the repo stays the source of truth); adopt a graph *server* only if query load demands one.

## Index

| ID | Title | Status | Severity | One-liner | Backlog / ADR |
|---|---|---|---|---|---|
| [INV-001](INV-001-dark-feedback-loop/) | Dark feedback loop — the tool has no ground truth | `fixed` | crucial¹ | 0 outcomes logged (live-verified 2026-07-20) → **Rung 2 capture endpoint shipped + live-validated** (PR #34): one click from the email records an outcome | [B-3 companion](../ledgers/backlog.md) · M7 · [ADR-0035](../adr/0035-outcome-capture-endpoint.md) |
| [INV-002](INV-002-silent-500-alarm/) | Silent 500 — a returned statusCode:500 pages nobody | `fixed` | non-crucial | A returned 500 is a *successful* Lambda invocation → invisible to the Errors alarm (0 alerts on the 2026-07-09 missed digest) → **mode-gated marker + log-metric-filter alarm shipped + live** (PR #35) | [B-3 companion](../ledgers/backlog.md) · [ADR-0029](../adr/0029-ops-hardening.md) |

¹ crucial for the recommended fix (a public capture endpoint — live infra + auth); a rung-1 interim is non-crucial.

> Add a row per dossier at open time; update its `Status` as it moves through the lifecycle. Keep this table the single index — a dossier with no row here is a bug (mirrors the procedure-registry's "never a dangling reference").
