# ADR-0014 — Operational store: Aurora Serverless v2 + RDS Data API (no VPC)

## Status
Accepted (resolves **D-v0-1**) · **✅ Validated live (v0.1.0, 2026-06-29)** — `terraform apply` → the **14-resource** stack; the **Data API confirmed end-to-end** (schema migrated via `alembic upgrade head` *and* the pipeline ran over it: fetch → … → notify, `statusCode 200`); **scale-to-0** confirmed; then `terraform destroy` → ~$0. The Data-API URL/param gotchas surfaced only on the live path → [ERR-004](../ledgers/errors.md) (`%`-encoded ARNs vs configparser) + [ERR-005](../ledgers/errors.md) (`aurora_cluster_arn` connect-kwarg).

## Context
[ADR-0003](0003-postgres-over-dynamodb.md) chose managed PostgreSQL but left the *flavor + connectivity* as a v0 sub-decision (**D-v0-1**). The fetch Lambda must call the **public JSearch API** (internet) and also reach S3, Secrets Manager, and (later) Bedrock. **How the Lambda talks to Postgres decides the entire networking posture** (VPC or not), the cost floor, and the failure surface.

## Decision
**Aurora PostgreSQL Serverless v2 + the RDS Data API**, with the **Lambda outside any VPC**. The Lambda calls the database over the **HTTPS Data API** (no Postgres wire protocol, no VPC), so it has direct, plumbing-free access to S3, Secrets Manager, the LLM API (DeepSeek over HTTPS — [ADR-0017](0017-llm-transport-openai-compatible-deepseek.md)), and the public JSearch API. **`pgvector`** runs in the same Aurora DB for the M2 dedup-blocking embeddings (no separate vector store). **Build config (pinned):** `min_capacity = 0 ACU` (auto-pause → ~$0 between daily runs; the daily batch tolerates the ~15s cold-resume), small `max_capacity` (1–2 ACU); pick an Aurora PostgreSQL engine version that supports **Data API + `pgvector` + scale-to-0** together — verified at `terraform apply`. The application access pattern (SQLAlchemy + the aurora-data-api dialect, behind a `Repository` port) is [ADR-0018](0018-persistence-sqlalchemy-data-api-repository.md).

## Alternatives Considered
- **RDS PostgreSQL `db.t4g.micro` + Lambda-in-VPC.** Cheapest DB sticker (free-tier-eligible 12 months on the new account), but RDS for PostgreSQL has **no Data API** → the Lambda must live in the VPC. Because the fetch Lambda must call the **public JSearch internet API**, a VPC-bound Lambda then needs a **~$32/mo NAT gateway** (plus interface endpoints for Secrets/Bedrock and likely RDS Proxy for connection storms). The NAT cost erases the DB savings and adds real networking surface for a personal tool. **Rejected** — the VPC/NAT complexity isn't justified by the workload, and the networking "showcase" isn't worth the ongoing cost + failure surface.
- **A separate vector store (OpenSearch / Pinecone) for embeddings.** Rejected: `pgvector` in the same Aurora DB is one store, not two — simpler, cheaper, and the M2 blocking volume is tiny.

## Consequences
- **Easier:** zero VPC plumbing (no subnets / NAT / endpoints / proxy); the Lambda reaches everything directly; ASv2 scales toward ~0 ACU when idle (a daily batch is idle ~23h/day); one store for relational data *and* vectors.
- **Harder:** a higher per-ACU rate than a `t4g.micro`, **mitigated by scale-to-0** (`min_capacity = 0` → ~$0 idle between daily runs); the Data API has request/result-size limits (irrelevant at 10–30 rows/day); a hard dependency on Data API + `pgvector` + scale-to-0 on the chosen Aurora version (a build-time check).
- **Impact:** keeps the whole pipeline **serverless and VPC-free**; pairs with [ADR-0003] (Postgres) and dbt-on-Postgres analytics ([ADR-0004]); the `bronze_posting` / `posting` / `cluster` / `score` schema and pgvector blocking ([ADR-0005]) all live in this one Aurora instance.
