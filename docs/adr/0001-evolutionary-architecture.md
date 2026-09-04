# ADR-0001 — Evolutionary architecture: minimal v0 + bottleneck-driven migrations

## Status
Accepted · **one stated Consequence — a before/after diagram per release — was retracted 2026-09-03 after 15 releases produced none; see the Retrospective at the foot of this file ([ERR-016](../ledgers/errors.md)). The decision itself stands.**

## Context
JobFetcher must be both a daily tool and a portfolio piece, and the desired end-state is a full system (LLM scoring + warehouse/dbt + serverless/IaC + streaming/CDC). Building that all at once conflicts with two hard requirements: value is needed in **weeks**, and the design must stay **minimal and defensible** (no bloat, no resume-driven complexity). A pre-drawn multi-phase plan also assumes we can predict the right sequence before writing any code — we can't.

## Decision
Build the **minimal working core (v0)** first, then grow through a **sequence of deliberate, observable migrations — each a clean, semver-tagged GitHub release** that introduces capability the previous lacked. Commit firmly only to: (1) v0, (2) a *migratable* architecture, (3) release discipline. The roadmap beyond v0 is a **living hypothesis**, re-derived after each release via the bottleneck protocol (see [ADR-0002] and [roadmap](../03-roadmap.md)).

## Alternatives Considered
- **Build the full system up-front (the original 8-phase plan).** Rejected: delays first value by weeks, front-loads complexity that may prove unnecessary, and presumes a sequence we can't yet know. It also makes every component hard to defend individually.
- **Lean MVP, then ad-hoc additions.** Rejected: captures "ship small" but loses the *discipline* — additions would accrete without justification, exactly the entropy P1 resists.
- **Plan-everything-then-build (a middle option Tarig initially chose).** Refined, not rejected: we plan the *foundation + current stage* fully, but plan each migration *just-in-time*, because implementation reveals the next bottleneck.

## Consequences
- **Easier:** fast first value; every addition is justified by a real bottleneck; the *evolution itself* becomes a rare senior/staff portfolio signal (ADR + migration guide per step).
- **Harder:** requires real release discipline (tags, CHANGELOG, migration tests, before/after diagrams) and migratable foundations (ports-&-adapters, feature flags, Alembic, additive Terraform) from v0.
- **Impossible (by design):** "just add it because it's cool" — a component with no bottleneck justification doesn't get built.
- **Impact:** reconciles "full system" with "absolute minimalism" — completeness is the destination reached by migration, never front-loaded.

## Retrospective (2026-09-03) — one stated consequence was never delivered, and is retracted

**The decision above stands; this note corrects one thing it promised.** The Consequences list requires *"before/after diagrams"* as part of release discipline. **Across 15 tags, zero were produced.** The requirement was restated in [`README.md`](../../README.md) (*"…with a before/after diagram. You can read the architecture evolve"*) and [`03-roadmap.md`](../03-roadmap.md) (*"Each release documents a before/after architecture diagram"*), so a reader was told three times that something exists which does not.

**Retracted, not quietly dropped.** Producing 15 retroactive diagram pairs now would violate the project's own rule that documentation is *constructed live, not reconstructed later* — they would be drawn from the code as it is today, which is exactly the reconstruction the first pillar rejects. What the repo actually maintains, and maintains well, is [`docs/diagrams.md`](../diagrams.md): a set of thematic Mermaid diagrams (architecture, ingestion, roadmap, dimensional model, replay, config, read/curate surfaces) **updated in place when a release changes the topology**. That is the honest version of the same intent — the architecture is still legible as it evolves; it is legible from the current diagram plus the CHANGELOG and the ADR, not from a per-release pair.

The rest of the release discipline named here **is** kept: semver tags, a CHANGELOG entry, an ADR per real decision, and a migration script when data changes. This retraction is one instance of a class — see [ERR-016](../ledgers/errors.md), where the class itself is what got fixed.

*This note is appended rather than edited into the text above, because what ADR-0001 believed in 2026-06 is the useful record; a rewrite would hide that the requirement was ever made.*
