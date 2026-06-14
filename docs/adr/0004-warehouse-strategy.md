# ADR-0004 — Analytics: dbt-on-Postgres default; Snowflake conditional (over Databricks)

## Status
Accepted

## Context
The portfolio's flagged gap is **SQL / warehouse modeling + dbt**. The instinct was to add a real warehouse (Snowflake) for the analytics plane. But the tool's data is tiny (~10–30 rows/day) — at this scale a dedicated warehouse is a *portfolio/skill* choice, not a data need — and we already run Postgres ([ADR-0003]), which can host the entire medallion via dbt for $0 extra. Tool-minimalism ([ADR-0002]) gates this.

## Decision
**dbt models the medallion into marts on Postgres by default** (staging→marts, tests/lineage/incremental). A dedicated **Snowflake** warehouse is **conditional** — adopted only if a real analytics bottleneck ever demands it (then via its own ADR + migration). The heavy Spark/lakehouse signal lives in the sibling **OrderFlow** project, where data volume justifies it.

## Alternatives Considered
- **Snowflake now (the earlier "lock").** Rejected/deferred: at this volume it's building for signal, which tool-minimalism forbids. Kept as the documented scale-path; the *dbt skill* (the actual gap) is fully demonstrated on Postgres regardless of engine.
- **Databricks.** Rejected: Spark-on-30-rows is unconvincing and a weaker fit for the SQL-modeling gap; the Spark/Delta signal belongs in OrderFlow. (We did a full Snowflake-vs-Databricks comparison — see [decision journal §8a](../01-session-decision-journal.md).)
- **DuckDB.** Strong free/local-friendly option and a reasonable future pivot, but Postgres already in the stack makes a second analytics engine unnecessary at v0.

## Consequences
- **Easier:** zero extra infra/cost for analytics; the dbt skill (tests, lineage, incremental, modeling) is demonstrated minimally and honestly; clean OLTP→marts story on one engine.
- **Harder:** "Snowflake" isn't a headline keyword unless/until the conditional trigger fires — accepted, because honesty + minimalism outrank keyword-chasing.
- **Impact:** the DE-depth prime directive is served by *modeling quality on Postgres + measured entity resolution + data contracts*, not by a warehouse logo. Snowflake and Debezium ([ADR-0009]) are both documented scale-paths, ready when a real bottleneck appears.
