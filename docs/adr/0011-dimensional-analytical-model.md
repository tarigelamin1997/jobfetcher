# ADR-0011 — Analytical model: insight-driven dimensional (constellation) schema

## Status
Accepted

## Context
The analytical plane must turn accumulated job data into career-strategy insight (skill demand, skill gaps, sector intelligence, progress trends). The raw listing is one wide, denormalized record. The question is how to model it so insights **compound** — without over-fragmenting into a table-per-field, and consistent with minimalism + the evolutionary roadmap. Tarig's framing: "dissect every piece into its dedicated place, then connect the dots so combinations create new insight."

## Decision
Model the analytical plane as an **insight-driven dimensional (constellation) schema** on dbt/Postgres:
- **Facts:** `fct_job_posting` (grain: posting/cluster) · `fct_job_skill` (bridge: posting × skill) · `fct_job_score` (posting × scoring-run) · `fct_application`.
- **Conformed dims:** `dim_date` · `dim_skill` · `dim_title` (raw → canonical + variants) · `dim_company` · `dim_sector` · `dim_location`; **profile as point-in-time** (SCD2 / snapshot).
- **Decompose by *insight*, not by *field*:** bronze retains every field losslessly, so a dimension is built **only when a real question needs it** — and modeled retroactively over history via bronze replay.
- **Priority (Tarig):** `dim_skill` + `fct_job_skill` first (powers skill-demand/gaps + sector), then point-in-time profile + score/application facts (trends), then `dim_sector`. Built at **M5/M6**.

## Alternatives Considered
- **Table-per-field / "dissect everything" up front.** Rejected: over-fragments into dozens of pipelines nobody queries; violates minimalism. Bronze already guarantees nothing is lost, so model later.
- **One Big Table (wide denormalized analytics table).** Rejected: easy to query but it buries the relationships — "connecting the dots" (skill × sector, title trends) is exactly what conformed dimensions make natural, and OBT fights skill's many-to-many (the bridge).
- **Defer all analytical design to M5.** Partial: we *build* at M5, but sketch the *target* now because it tells silver what to retain and which **derived** dims (skills, canonical title) the text pipeline must produce.
- **Snowflake-hosted star now.** Rejected per [ADR-0004](0004-warehouse-strategy.md): Postgres + dbt suffices; warehouse stays conditional.

## Consequences
- **Easier:** insights are joins over a small conformed-dim set (~5 dims + ~4 facts), not bespoke queries; the model grows cleanly per question; the `fct_job_skill` bridge powers two priorities at once.
- **Harder:** the richest dims (`dim_skill`, canonical `dim_title`) require **LLM extraction + normalization from text** — the hardest pipeline, and the one all three priority insights depend on.
- **Impact:** unifies the silver text pipeline with the dimensional model (skills/title are *derived*, not mapped); **point-in-time profile (SCD2)** is required for trend insights; realized in the dbt marts at M5/M6 ([02-architecture](../02-architecture.md#analytical-plane--dbt-marts-adr-0004), [roadmap](../03-roadmap.md)).
