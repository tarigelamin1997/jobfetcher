# Ledger · Phase Index

> Live source of truth for progress. Status legend: ⬜ not started · 🚧 in progress · ✅ shipped (tagged release). The roadmap beyond v0 is a **[living hypothesis](../03-roadmap.md)** — re-derived after each release; update this table as reality unfolds.

**Current state:** **v0 in progress — Step 0 (ingestion coverage probe).** Design + docs complete. First code exists: a validated, `SearchSpec`-driven JSearch probe ([`scripts/jsearch_probe.py`](../../scripts/jsearch_probe.py)) proven end-to-end against the live API; JSearch key in Secrets Manager; chosen LLM = Kimi K2 Thinking. **Blocker:** account-wide Bedrock daily-token quota = 0 ([ERR-001](errors.md)) gates the scoring path. Full 18-query sweep + v0 build steps 1+ are next.

| Release | Adds | Status |
|---|---|---|
| **v0.1** | One Lambda: 1 source → S3 + Postgres → Bedrock score → daily email; Terraform, Secrets Manager, tests, minimal CI | 🚧 Step 0 (probe built + validated; `SearchSpec` + Secrets Manager done; scoring gated on ERR-001) |
| M1 · v0.2 | CV tailoring (reliable renderer, draft→review) | ⬜ (hypothesis) |
| M2 · v0.3 | Multi-source + clustering dedup + Suspected-Duplicates | ⬜ (hypothesis) |
| M3 · v0.4 | Single Lambda → Step Functions | ⬜ (hypothesis) |
| M4 · v0.5 | Notion workspace + near-miss/graduation | ⬜ (hypothesis) |
| M5 · v0.6 | dbt marts on Postgres | ⬜ (hypothesis) |
| M6 · v0.7 | Skill-Demand + Sector Intelligence | ⬜ (hypothesis) |
| M7 · v0.8 | Right-sized observability + scoring calibration loop | ⬜ (hypothesis) |
| M8 · v1.0.0 | CI/CD hardening + README/diagram/demo + seam-ready stubs | ⬜ (hypothesis) |
| Future | Debezium CDC · multi-user (v2.0) · feedback hub · BI dashboard · Snowflake (conditional) | ⬜ (documented scale-paths) |

> After each release ships: tag it, append its **Produces** to [interface-contracts](interface-contracts.md), run the [migration-decision protocol](../03-roadmap.md#the-migration-decision-protocol-how-the-next-step-is-actually-chosen), and re-confirm/replace the next row here.
