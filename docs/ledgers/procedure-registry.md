# Ledger · Procedure Registry

> A "procedure" is a reusable standard read *before* a recurring task (how we write a source adapter, a scoring prompt, a dbt model, etc.). The registry's invariant: **a referenced procedure is either `Written` or a `Deferred → <stage>` entry with an owning stage — never a dangling reference.** Right-sized for this project: most standards live inline in the docs below rather than as separate procedure files; this table is the index + status.

| Procedure | Status | Lives in / owned by |
|---|---|---|
| ADR authoring (with rejected alternatives) | ✅ Written | [adr/README](../adr/README.md) |
| Error/incident logging (Five Questions) | ✅ Written | [05-methodology](../05-methodology.md#adopt-cheap-high-leverage-even-solo--value-is-memory-across-time) + [errors.md](errors.md) |
| Validation-gate standard (behavioral + negative case) | ✅ Written | [05-methodology](../05-methodology.md) + applied in [04-v0-build-plan](../04-v0-build-plan.md) |
| Secrets management (Secrets Manager, `jobfetcher/<service>`) | ✅ Written | [decisions-locked](decisions-locked.md) (Security) · pattern in [`scripts/jsearch_probe.py`](../../scripts/jsearch_probe.py) `get_key()` |
| **Gate trio** (entry/code/exit) as slash-commands | ✅ Written | [`.claude/commands/`](../../.claude/commands/) (`start-step` · `review-step` · `close-step`) · [ADR-0013](../adr/0013-enforcement-gate-trio-branch-pr.md) |
| **Agentic build pipeline** (per-unit gate stages: builder→review→scribe→guardian; cross-unit fan-out; worktree isolation) | ✅ Written | [ADR-0019](../adr/0019-agentic-build-orchestration.md) — first run C-2 |
| Data-contract / source normalization | ✅ Written → v0 | `SearchSpec` ([core/search_spec.py](../../src/jobfetcher/core/search_spec.py)) · `SourceAdapter` port + `JSearchSourceAdapter` (Step 4) · the **dissection contract** `DissectedPosting`/`Skill` (`core/models.py`, C-1) · the **`Profile`** contract ([core/profile.py](../../src/jobfetcher/core/profile.py), Unit A — `extra="forbid"`, `from_yaml`/`from_jsonb` fail loud; the per-user input alongside `SearchSpec`); posting/score contracts at C-2 |
| **Filter-strategy / gold-filter pattern** (`FilterStrategy` port + `FilterError`; deterministic-default + optional-LLM strategies; fail-open on `FilterError`; 1:1 cluster per fit) | ✅ Written → v0 (Unit A) | `core/ports.py` (`FilterStrategy`/`FilterError`) · `adapters/{filter_deterministic.py, filter_llm.py}` · `core/ingest.py` `apply_gold_filter` ([ADR-0015](../adr/0015-type-replaceable-pipeline-stages.md) type-swap) |
| Scoring-prompt standard (7-factor, explainable, temp 0) | ✅ Written → v0 (Step 5) | `src/jobfetcher/core/scorer.py` `SCORING_SYSTEM_PROMPT` (mirrors the `Dissector` — grounded, JSON-only, one retry; `fit_category` band-derived in the orchestrator, not by the LLM) |
| Dissection-prompt standard (grounded, evidence-required, temp 0) | ✅ Written → v0 (C-1) | `src/jobfetcher/core/dissector.py` `DISSECTION_SYSTEM_PROMPT` + `grounding_check` |
| Persistence / `Repository` pattern (SQLAlchemy Core + aurora-data-api dialect; local-Postgres DB tests) | ✅ Written → **built** v0 (C-2) | [ADR-0018](../adr/0018-persistence-sqlalchemy-data-api-repository.md); `src/jobfetcher/{db/tables.py, adapters/repository_postgres.py}` + `migrations/` |
| Notification / digest pattern (`Notifier` port + `NotifierError`; dependency-free HTML + plaintext `render_digest`; threshold-gated shortlist + below-count; all text HTML-escaped, apply-link `href` scheme-allowlisted to http/https; valid zero-matches email) | ✅ Written → v0 (Step 6) | `core/ports.py` (`Notifier`/`NotifierError` — ADR-0015's port now defined) · `adapters/ses_notifier.py` (`SesNotifier`, lazy/injectable client mirroring `S3RawStore`) · `core/notifier.py` (`render_digest`) · `core/ingest.py` (`notify` orchestrator) |
| Migratability checklist (ports/adapters, flags, Alembic, additive TF) | ✅ Written | [03-roadmap](../03-roadmap.md#migratability-requirements-build-v0-so-the-above-stays-cheap) |
| Dedup / entity-resolution standard | 🔜 Deferred → M2 | authored when multi-source + dedup lands |
| CV-render standard (content model → DOCX + PDF; honesty rules) | 🔜 Deferred → M1 | authored when CV tailoring lands |
| dbt-modeling standard (staging→marts, tests, lineage, incremental) | 🔜 Deferred → M5 | authored when the analytics plane lands |
| Chaos / negative-injection (right-sized: targeted, not six-angle) | 🔜 Deferred → M7 | a couple of failure-injection tests on the riskiest path |

> When a deferred procedure's stage starts, **authoring it is the first step** of that stage. Keep this table honest — a `Deferred` entry with no owning stage is a bug.
