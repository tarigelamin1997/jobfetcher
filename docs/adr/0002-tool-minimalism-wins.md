# ADR-0002 — Tool-minimalism is the gate; DE-depth is the tiebreaker

## Status
Accepted

## Context
The project has two goals that can conflict: a **daily tool** (wants the simplest thing that works) and a **portfolio** (wants impressive DE depth — the assessment flagged SQL/warehouse modeling as the gap). Without a rule, "portfolio value" becomes a license to add components the tool doesn't need (resume-driven development). The data is tiny (~10–30 jobs/day), so almost nothing is justified by *load*.

## Decision
**Tool-minimalism wins.** Only build what a real **tool** bottleneck justifies; the portfolio takes whatever the tool honestly produces. **DE-depth is the tiebreaker** — when a bottleneck *does* justify a build and several solutions fit, pick the richer-DE-signal one *in its minimal form*. DE-depth is never, by itself, a reason to add a component.

## Alternatives Considered
- **Portfolio-can-drive (add showcases freely, labeled).** Rejected: even with honest labels, it invites complexity the tool doesn't need and undermines the "real, defensible architecture" goal.
- **Two equally-weighted drivers (tool OR portfolio justifies a build).** Rejected: in practice this collapses to "portfolio justifies anything," because there's always a portfolio rationale.
- **Pure minimalism (ignore portfolio entirely).** Rejected: the portfolio is a real goal; the tiebreaker preserves it without letting it override.

## Consequences
- **Easier:** every component passes the defensibility rubric by construction; the architecture is honestly defendable in an interview.
- **Harder:** some "cool" pieces don't get built here (they move to sibling projects where load justifies them — e.g. Spark/Delta → OrderFlow).
- **Impact (supersedes an earlier lock):** **Snowflake becomes conditional** (Postgres+dbt is the default; see [ADR-0004]); Step Functions survives only because real Lambda complexity earns it ([roadmap](../03-roadmap.md) M3). DE-depth is still fully served — minimally and honestly — via Postgres + dbt + measured entity resolution + data contracts + the evolutionary story.
