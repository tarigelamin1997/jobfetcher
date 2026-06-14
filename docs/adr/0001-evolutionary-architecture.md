# ADR-0001 — Evolutionary architecture: minimal v0 + bottleneck-driven migrations

## Status
Accepted

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
