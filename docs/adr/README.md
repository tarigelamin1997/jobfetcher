# Architecture Decision Records

Each ADR records one significant decision: **what was chosen, what was rejected, and why.** The rejected alternative is the point — it proves the tradeoff was evaluated. Format: Status · Context · Decision · Alternatives Considered (≥2, with project-specific rejection reasons) · Consequences.

These are the **foundational** decisions made during the design session — the ones that govern the whole project regardless of which migration is in flight. **Migration-specific implementation decisions get their own ADR when that migration is planned** (just-in-time, per the [roadmap](../03-roadmap.md)) — e.g. scoring-weight tuning (M-score), Step-Functions topology (M3), Snowflake adoption (if/when its bottleneck appears).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-evolutionary-architecture.md) | Evolutionary architecture: minimal v0 + bottleneck-driven migrations | Accepted |
| [0002](0002-tool-minimalism-wins.md) | Tool-minimalism is the gate; DE-depth is the tiebreaker | Accepted |
| [0003](0003-postgres-over-dynamodb.md) | PostgreSQL as the operational store (over DynamoDB) | Accepted |
| [0004](0004-warehouse-strategy.md) | Analytics: dbt-on-Postgres default; Snowflake conditional (over Databricks) | Accepted |
| [0005](0005-dedup-cluster-and-surface.md) | Deduplication: cluster-and-surface, never hide | Accepted |
| [0006](0006-cv-renderer.md) | CV rendering without LibreOffice-in-Lambda | Accepted |
| [0007](0007-self-hosted-distribution.md) | Self-hosted / open-source distribution (not SaaS) | Accepted |
| [0008](0008-region-us-east-1.md) | Region: us-east-1 | Accepted |
| [0009](0009-batch-not-debezium-v0.md) | Batch EL now; Debezium CDC as a documented scale-path | Accepted |
| [0010](0010-job-source-jsearch.md) | Job source: JSearch (probe-free → Pro), single-source for v0; Adzuna deferred | Accepted |
| [0011](0011-dimensional-analytical-model.md) | Analytical model: insight-driven dimensional (constellation) schema; grow per question | Accepted |
| [0012](0012-model-agnostic-llm.md) | Model-agnostic LLM via Bedrock Converse; model id in config (swap models freely) | Accepted |
| [0013](0013-enforcement-gate-trio-branch-pr.md) | Enforcement: gate-trio slash-commands (`/start-step` · `/review-step` · `/close-step`) + branch/PR workflow | Accepted |
| [0014](0014-operational-store-aurora-serverless-data-api.md) | Operational store: Aurora Serverless v2 + RDS Data API (no VPC); resolves D-v0-1 | Accepted |

> Full reasoning narrative: [01-session-decision-journal](../01-session-decision-journal.md). Crisp decision list: [ledgers/decisions-locked](../ledgers/decisions-locked.md).
