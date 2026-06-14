# Ledger · Phase Index

> Live source of truth for progress. Status legend: ⬜ not started · 🚧 in progress · ✅ shipped (tagged release). The roadmap beyond v0 is a **[living hypothesis](../03-roadmap.md)** — re-derived after each release; update this table as reality unfolds.

**Current state:** Pre-implementation. Design + documentation complete; **awaiting Tarig's review of the docs** before building v0. No code exists yet.

| Release | Adds | Status |
|---|---|---|
| **v0.1** | One Lambda: 1 source → S3 + Postgres → Bedrock score → daily email; Terraform, Secrets Manager, tests, minimal CI | ⬜ |
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
