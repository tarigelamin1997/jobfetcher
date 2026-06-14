# Ledger · Procedure Registry

> A "procedure" is a reusable standard read *before* a recurring task (how we write a source adapter, a scoring prompt, a dbt model, etc.). The registry's invariant: **a referenced procedure is either `Written` or a `Deferred → <stage>` entry with an owning stage — never a dangling reference.** Right-sized for this project: most standards live inline in the docs below rather than as separate procedure files; this table is the index + status.

| Procedure | Status | Lives in / owned by |
|---|---|---|
| ADR authoring (with rejected alternatives) | ✅ Written | [adr/README](../adr/README.md) |
| Error/incident logging (Five Questions) | ✅ Written | [05-methodology](../05-methodology.md#adopt-cheap-high-leverage-even-solo--value-is-memory-across-time) + [errors.md](errors.md) |
| Validation-gate standard (behavioral + negative case) | ✅ Written | [05-methodology](../05-methodology.md) + applied in [04-v0-build-plan](../04-v0-build-plan.md) |
| Data-contract / source normalization | 🔜 Deferred → v0 | authored as first step of v0 (Pydantic models) |
| Scoring-prompt standard (7-factor, explainable, temp 0) | 🔜 Deferred → v0 | authored in v0 Step 5 |
| Migratability checklist (ports/adapters, flags, Alembic, additive TF) | ✅ Written | [03-roadmap](../03-roadmap.md#migratability-requirements-build-v0-so-the-above-stays-cheap) |
| Dedup / entity-resolution standard | 🔜 Deferred → M2 | authored when multi-source + dedup lands |
| CV-render standard (content model → DOCX + PDF; honesty rules) | 🔜 Deferred → M1 | authored when CV tailoring lands |
| dbt-modeling standard (staging→marts, tests, lineage, incremental) | 🔜 Deferred → M5 | authored when the analytics plane lands |
| Chaos / negative-injection (right-sized: targeted, not six-angle) | 🔜 Deferred → M7 | a couple of failure-injection tests on the riskiest path |

> When a deferred procedure's stage starts, **authoring it is the first step** of that stage. Keep this table honest — a `Deferred` entry with no owning stage is a bug.
