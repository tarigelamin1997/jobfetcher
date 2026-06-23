---
description: ENTRY gate — refuse to start a build unit until its spec is complete, prerequisites are closed, and every validation criterion is strong (behavioral + negative).
argument-hint: <build unit, e.g. "v0 Step 4" or "M1">
---

# /start-step — ENTRY gate

Gate the start of a **build unit** (a v0 Step or a migration). Refuse to begin implementation until the spec is complete and every validation criterion is *strong*. On PASS, ensure we are on a branch and set the phase-index to 🚧. This is the entry guardrail — most defects are cheapest to prevent here.

**Build unit:** $ARGUMENTS

Run the checks **in order**. Report **PASS / FAIL / SKIP** for each with a one-line note. Any FAIL blocks the unit from starting.

### Check 1 — Spec is complete (behavioral)
- The unit's scope + apply-step exists in `docs/04-v0-build-plan.md` (or the migration's just-in-time plan) with **WHY / WAIT-FOR / FAILURE-MODE**.
- **FAIL** if any `[TO BE FILLED]` / `[TBD]` placeholder remains, or WAIT-FOR / FAILURE-MODE is missing.

### Check 2 — Prerequisites closed
- Everything the unit consumes is satisfied (e.g. the Secrets Manager secret exists; the upstream step's *Produces* is in `docs/ledgers/interface-contracts.md`; required `D-…` / ADR sub-decisions are resolved).
- **FAIL** (name the open prerequisite) otherwise.

### Check 3 — Deferred procedures authored
- Any procedure marked `Deferred → <this unit>` in `docs/ledgers/procedure-registry.md` is now **Written**.
- **FAIL** if a procedure this unit owes is still Deferred.

### Check 4 — Every validation criterion is STRONG (the gate-robustness standard)
- Each of the unit's validation-gate rows is **behavioral** (asserts the thing does its job through its real interface) **and** carries a **negative case** (or `Negative case: N/A — <reason>`).
- A presence/liveness-only criterion, or a missing negative case, is a **hard FAIL** (a weak gate reports green on a broken system — worse than none).

### Check 5 — On a branch
- We are **not** on `main`. If on `main`, create `feat/<unit-slug>` (or `chore/<slug>`) and switch to it.

## On PASS
- Set the unit's row in `docs/ledgers/phase-index.md` to **🚧**.
- Print: `<unit> is cleared to implement. Checkpoint A (spec approved) is the human's call.`

## Allowed mutations
ONLY: `docs/ledgers/phase-index.md` (status → 🚧) and creating the git branch. Nothing else.

## Output
A table — **Check | Status | Notes** — then the clearance line, or every FAIL with its exact fix.
