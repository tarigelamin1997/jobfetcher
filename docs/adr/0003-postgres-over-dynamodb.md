# ADR-0003 — PostgreSQL as the operational store (over DynamoDB)

## Status
Accepted

## Context
The operational data is inherently **relational**: postings belong to clusters, clusters have one score and one CV, applications track clusters, and we query it many different ways (by score, company, status, sector, cluster). The original design used DynamoDB + Streams. We need a store that fits this shape, supports rich queries and vector similarity (for dedup blocking), and gives a strong, honest DE signal.

## Decision
Use **managed PostgreSQL** as the operational store, with **pgvector** for JD-embedding similarity. The cross-table-sync / near-miss logic the old design put in DynamoDB Streams moves to ordinary application logic / (later) CDC consumers.

## Alternatives Considered
- **DynamoDB + Streams (the original choice).** Rejected: NoSQL for inherently relational, query-varied data is the *weaker* fit — it forces single-table-design gymnastics and GSIs to emulate joins. It was arguably the original plan's least-defensible call.
- **SQLite.** Rejected: not a managed, concurrent, network-accessible store for a scheduled Lambda; no pgvector.
- **Aurora vs RDS Postgres** — **resolved in [ADR-0014](0014-operational-store-aurora-serverless-data-api.md)** (D-v0-1): **Aurora Serverless v2 + RDS Data API, Lambda outside any VPC** (a VPC-bound Lambda would need a ~$32/mo NAT for the public JSearch fetch).

## Consequences
- **Easier:** natural relational modeling, rich SQL, pgvector for dedup, and a clean OLTP→analytics story (the same Postgres feeds dbt marts — [ADR-0004]). More defensible than NoSQL here.
- **Harder:** a managed relational DB has a non-zero idle cost (the dominant v0 cost) and, for RDS, Lambda-in-VPC considerations.
- **Impact:** enables the cluster-and-surface dedup model ([ADR-0005]) and dbt-on-Postgres analytics. Mirrors Tarig's real DE expertise (Postgres/Debezium), and sets up Debezium CDC as a natural documented scale-path ([ADR-0009]).
