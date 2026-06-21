---
description: EXIT gate — docs, ADRs, the validation gate (positive + negative), contracts, and ledgers all verified before a build unit is marked complete and PR'd.
argument-hint: <build unit, e.g. "v0 Step 4" or "M1">
---

# /close-step — EXIT gate

Gate the **completion** of a build unit. On PASS the unit is ready for its PR/merge — and **Checkpoint B (the merge/tag) stays with the human.** Sets the phase-index to ✅.

**Build unit:** $ARGUMENTS

Run **in order**. Report **PASS / FAIL / SKIP** for each. Any FAIL blocks the close.

### Check 1 — Docs current (no drift)
- Every doc the change touched is up to date: `docs/02-architecture.md` / `docs/04-v0-build-plan.md` / the ledgers / `CLAUDE.md` status. No doc still describes the old reality.
- **FAIL** (name the stale doc) otherwise.

### Check 2 — ADRs present
- Every significant or irreversible decision in this unit has an ADR (with **rejected alternatives**) in `docs/adr/`, indexed in `docs/adr/README.md`.
- **FAIL** if a decision is undocumented.

### Check 3 — Validation gate run POSITIVE **and** NEGATIVE
- The unit's validation-gate rows (e.g. VG1–VG8 for v0) are executed: the positive passes **and** the negative fires. Paste the evidence.
- A gate not actually run is a **hard FAIL**.

### Check 4 — No open errors
- No `ERR-NNN` in `docs/ledgers/errors.md` owned by this unit is **Open** without an implemented prevention.
- **FAIL** otherwise.

### Check 5 — Interface contract verified + appended
- This unit's *Consumes* matches the already-shipped *Produces* upstream; append this unit's *Produces* row to `docs/ledgers/interface-contracts.md`.
- **FAIL** on any contract mismatch.

### Check 6 — Phase index
- Set the unit's row in `docs/ledgers/phase-index.md` to **✅**.

## On PASS
- Print: `<unit> is complete and verified. Open the PR — Checkpoint B (merge/tag) is the human's call.`

## Allowed mutations
ONLY: `docs/ledgers/interface-contracts.md` (append *Produces*) and `docs/ledgers/phase-index.md` (status → ✅). Nothing else.

## Output
A table — **Check | Status | Notes** — then the completion line, or every FAIL with its exact fix.
