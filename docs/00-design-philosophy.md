# 00 · Design Philosophy

> The operating constitution. Every architecture and process decision in this project obeys what's here. If a future choice conflicts with this doc, either the choice is wrong or this doc must be deliberately amended (with an ADR) — not quietly ignored.

---

## The two pillars (adopted from Tarig's Master Project Implementation Plan)

**Pillar 1 — Documentation as infrastructure.**
State and reasoning live in the *files*, not in memory, a tool, or implied by code. This buys two concrete properties we require:
- **Session independence** — discard the context window and lose nothing; any new session (human or agent) resumes from the repo alone. *This is the reason this doc set exists: it must survive session-end, compaction, and new sessions.*
- **Public reproducibility** — a stranger can understand and rebuild the system from the docs.

Two rules that make it real:
- **What / Why / So-what** on every document. *What* = the concrete output/decision. *Why* = the reasoning + what was rejected. *So-what* = the implications (what it enables/forecloses downstream). A doc that answers *what* without *why* is incomplete.
- **A placeholder is a blocker, not a draft.** `[TO BE FILLED]` blocks progress; don't ship docs around it.
- **Documentation is constructed, not described** — written *live*, as decisions and errors happen, not reconstructed from memory afterward. Memory is lossy; files are not.

**Pillar 2 — A standard not wired into a command is a suggestion.**
We don't trust that the right thing happens because it's written down; ideally the system is *unable to proceed* until it has. We adopt this as **discipline now** and add **enforcement machinery (commands/gates) only when a real need justifies it** (see [methodology](05-methodology.md) — enforcement is itself emergent, per P1/P2 below).

---

## P1 — Absolute minimalism

> *Build the minimal complexity that solves the present problem. Nothing for hypothetical futures.*

- **Complexity is entropic.** It accrues *uninvited* as features grow — you never have to *add* complexity on purpose; it arrives for free. Therefore the default stance is **active resistance**: every addition must justify itself against a simpler alternative.
- **Design seams, don't build futures.** Make future capability *cheap to add later* (clean module boundaries, config flags) — but do not build it now.
- **Smallest change that works.** Fewer files, fewer lines, fewer moving parts. Large change-sets have large blast radii.

## P2 — Bottleneck-driven evolution

> *The system grows by repeatedly breaking the single biggest bottleneck blocking the next real capability — with the minimal migration that breaks it.*

This is the **engine of the roadmap** (see [roadmap](03-roadmap.md)). After each release:
1. **Use it. Observe.** Real usage surfaces real friction.
2. **Identify the top-3 bottlenecks** blocking the next *true* capability (a capability, not polish).
3. **Rank by leverage** = `capability unlocked ÷ complexity added`.
4. **Design the minimal migration** that breaks the highest-leverage bottleneck.
5. **Ship it as a clean, labeled release** — ADR records *bottleneck → capability unlocked → minimal solution*.
6. Repeat.

**The roadmap is a living hypothesis, not a contract.** You cannot draw the full sequence before shipping — each stage's *implementation* is what reveals the next bottleneck. So we **commit firmly to only three things**: (a) the current stage (v0, fully designed), (b) the *migratable* architecture, (c) release discipline. Everything past the current stage is re-evaluated after every release.

## P1 × P2 — the single rule

> **Add the minimum, and only to break the biggest real bottleneck → maximum capability per unit of complexity.**

**Data corollary — never-discard → decompose-by-insight.** Retaining data is *not* the same as modeling it. **Never delete** — raw landing (bronze) is immutable, so nothing is ever lost. But **model into structured dimensions/facts only what compounds into a real insight** — decompose by *insight*, not by *field*. Because the raw is immutable, a new dimension can be modeled **retroactively over all history** via replay. This is P1/P2 applied to data: don't pre-build a table per field; grow the model one justified question at a time. (See [ADR-0011](adr/0011-dimensional-analytical-model.md).)

---

## Tool-minimalism wins (the gate) · DE-depth is the tiebreaker

This project serves two goals (a daily tool **and** a portfolio). They can pull in opposite directions. The resolution:

- **Minimalism is the GATE.** Only build what a real **tool** bottleneck justifies. The portfolio takes whatever the tool *honestly* produces. **No building for signal alone.**
- **DE-depth is the TIEBREAKER.** When a bottleneck *does* justify a build and several solutions fit, choose the one with the richer data-engineering signal — *in its minimal form*. DE-depth is never a license to add a component the tool doesn't need.

**Consequence already in force:** the tool's data is tiny (~10–30 jobs/day), so **Postgres + dbt** fully solves the analytics problem — a dedicated warehouse (**Snowflake**) is *conditional*, built only if a real analytics bottleneck ever demands it. The heavyweight DE showcases (Debezium CDC at scale, Spark/Delta) live where data volume actually justifies them (the sibling *OrderFlow* project), not forced onto this trickle. See [ADR-0002](adr/0002-tool-minimalism-wins.md) and [ADR-0004](adr/0004-warehouse-strategy.md).

---

## The defensibility rubric

The honest truth: at this scale **nothing is justified by load** — a cron script writing to SQLite and calling an LLM would do the functional job. So we never defend the stack with *"the scale demands it."* We defend it as: **a personal-scale tool built to production standards — real patterns, modest scale, deliberately right-sized.**

A component is **defensible** if you can answer an interviewer's *"why this and not the simpler thing?"* without the real reason being *"to put it on my resume."* Concretely, **at least one** must hold:

1. **Right-tool fit** — best fit for the problem's *shape*, not its size (relational data → Postgres; semantic matching → LLM; blobs → S3).
2. **Real requirement** — meets an actual need of *our* use (reliability, reproducibility, $0-when-idle, observability we'll actually read).
3. **Honest showcase, labeled** — there to demonstrate a target skill, *stated openly*, with a clear "when this would be overkill vs. needed."
4. **Documented scale-path** — right-sized now, with a documented upgrade for when scale arrives (batch → CDC; Step Functions → Airflow; Postgres → Snowflake).

**Theater (cut it):** the *only* reason is "looks impressive" and you'd have to *pretend* the scale needs it.

**The one-line test:** *Can you name the simpler alternative and the tradeoff?* If yes → defensible (even a deliberately-chosen showcase). If you can't, or you'd have to pretend → it's theater.

**The dial: BALANCED.** Default to the simplest defensible option; keep the **2–3 highest-value showcases clearly labeled** (warehouse/dbt modeling + measured entity-resolution are the headliners). *Defensible ≠ minimal* — breadth is fine if every piece passes a lens *and* is framed honestly. The enemy is unjustifiable complexity and dishonest framing, not complexity itself.

---

## Safety-first engineering (the Castle Principle)

We are building a castle, not demolishing and rebuilding one. Every change is a new stone placed carefully on the existing structure.

- **Build, don't demolish.** Extend working code; don't rewrite/replace it unless there's a documented reason it *cannot* be extended. "It would be cleaner" is not a reason.
- **Change-scope minimization.** Touch the fewest files/lines necessary. If a fix needs 15 files, find the 2-file version.
- **One change at a time**, verified before *and* after (record a baseline; compare). "It works" → "it works AND nothing else broke."
- **Tag before risk.** A git tag before any batch that could break working functionality. Rollback cost: seconds.
- **Document before you delete.** Record what a thing does and why it's going before removing it.
- **Destructive ops require explicit approval** — `rm -rf`, `DROP`, `DELETE`, `terraform destroy`, `git push --force`. No automated/destructive action without human confirmation, logged.

---

## How these principles resolve the project's central tension

"Build the full system" (Tarig wants completeness) vs. "absolute minimalism" (Tarig wants no bloat) is reconciled by **evolution**: the full system is the *destination reached through migrations*, and minimalism + bottleneck-selection govern *how fast and in what order* we get there. Completeness is earned one justified migration at a time — never front-loaded.
