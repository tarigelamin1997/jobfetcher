# Ledger · Phase Index

> Live source of truth for progress. Status legend: ⬜ not started · 🚧 in progress · ✅ shipped (tagged release). `/start-step` sets 🚧, `/close-step` sets ✅ ([ADR-0013](../adr/0013-enforcement-gate-trio-branch-pr.md)). The roadmap beyond v0 is a **[living hypothesis](../03-roadmap.md)** — re-derived after each release; update this table as reality unfolds.

**Current state:** **v0 in progress — Step 0 (ingestion coverage probe).** Design + docs complete. First code exists: a validated, `SearchSpec`-driven JSearch probe ([`scripts/jsearch_probe.py`](../../scripts/jsearch_probe.py)) proven end-to-end against the live API; JSearch key in Secrets Manager; **LLM = OpenAI-compatible API, v0 backend = DeepSeek** ([ADR-0017](../adr/0017-llm-transport-openai-compatible-deepseek.md)) — **✅ verified live 2026-06-24** (`deepseek-v4-flash`, HTTP 200); Bedrock's wall ([ERR-001](errors.md)) is **worked around: no open blocker.** The silver `Dissector` (C-1) and the storage schema + `Repository` (C-2) are built; next = Terraform infra (C-3) and the full 18-query sweep.

| Release | Adds | Status |
|---|---|---|
| **v0.1** | One Lambda: 1 source → S3 + Postgres → LLM score (DeepSeek) → daily email; Terraform, Secrets Manager, tests, minimal CI | 🚧 Step 0 done; **C-1** silver `Dissector` built + live-validated; **C-2** storage schema + `Repository` built + **live-validated** (Alembic migration · SQLAlchemy + aurora-data-api · ADR-0018; **5/5 DB round-trip passes on real Postgres** — schema builds, `DissectedPosting` round-trips equal, idempotency holds); next = Terraform infra (C-3, Step 3) |
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
