---
description: CODE gate — run static analysis, secret scan, unit tests, and every negative case before a build unit can close. Read-only; reports, does not fix.
argument-hint: <build unit, e.g. "v0 Step 4" or "M1">
---

# /review-step — CODE gate

Gate the **code** of a build unit before it closes. Nothing here trusts that a check was *written* — it **runs** them.

**Build unit:** $ARGUMENTS

Run **in order**. Report **PASS / FAIL / SKIP** for each. This command is read-only — it reports, it does not fix.

### Check 1 — Lint / static (behavioral)
- `ruff check` clean on the changed Python. **This is what CI enforces** (`.github/workflows/ci.yml` → `ruff check .`), with `ruff` pinned to `0.6.9` in both `pyproject.toml` and `.pre-commit-config.yaml` so local and CI cannot disagree ([B-8](../../docs/ledgers/backlog.md)).
- **FAIL** with the offending files otherwise.
- ⚠️ **`ruff format --check` is NOT run and NOT enforced.** This check used to claim it was. It never has been — the tree is 84 files from format-clean, so running it would hard-fail every unit, and CI has only ever run `ruff check`. Rather than keep a gate nobody could pass, the gap is recorded as [**B-11**](../../docs/ledgers/backlog.md) with an owning stage. A documented gate that would fail if anyone ran it is the same shape as ERR-015 — a written standard more demanding than the enforced one — and is fixed the same way: by making the document tell the truth.

### Check 1b — Doc audit
- `python scripts/check_docs.py` clean: internal links resolve, no `plan §NN` citations, every guarded prose count matches its ground-truth command, no stale "not yet deployed" claims.
- Also enforced by CI, so this is a pre-flight rather than the gate itself ([ERR-016](../../docs/ledgers/errors.md)).

### Check 2 — Secret scan
- No secret / API key / material in the diff or repo (run the pre-commit secret scan; review `git diff --staged`).
- A *planted* fake key MUST be blocked — if the scan can't catch one, the scan is the bug.
- **FAIL** if any secret-shaped string is staged.

### Check 3 — Unit tests
- `pytest` (unit) green for the unit's logic (e.g. normalization, fingerprint, score-output parsing, threshold routing, email rendering, `SearchSpec` validation).
- **FAIL** with the failing test otherwise.

### Check 4 — Every NEGATIVE case executed (the core)
- For each validation-gate row of this unit, the **negative case is actually run** and the guard **fires** (e.g. malformed payload → rejected + logged, not silently persisted; threshold above all scores → valid "no matches" email, not a crash).
- A negative case that was **not executed** is a **hard FAIL**.

### Check 5 — No hardcoded config / secrets
- Threshold, model id, query matrix, region, secret names come from config / `SearchSpec` / Secrets Manager — not literals in code.
- **FAIL** (cite the line) otherwise.

## Allowed mutations
None — verification only.

## Output
A table — **Check | Status | Notes** — ending with `<unit> code gate: PASS — clear for /close-step.` or every FAIL with its exact fix.
