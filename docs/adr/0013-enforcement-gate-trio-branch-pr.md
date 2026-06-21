# ADR-0013 — Enforcement machinery: gate-trio slash-commands + branch/PR workflow

## Status
Accepted

## Context
[05-methodology](../05-methodology.md) deferred the gate-enforcement *machinery* (slash-commands vs Makefile vs checklists) as **emergent** — to decide "when a real need justifies it." Two triggers are now met: we are crossing from design into **building** v0, and we work **inside Claude Code**, where a slash-command is near-free. Tarig's **Master Project Implementation Plan** frames the rule: *a standard not wired into a command is a suggestion* — the system should be *unable to proceed* until a gate has actually run. We already adopted that plan's core (ledgers, ADRs, the behavioral-gate standard = VG1–8); this records the enforcement *surface* + the dev workflow.

## Decision
1. **The gate trio as Claude Code slash-commands** (`.claude/commands/`, committed): **`/start-step`** (ENTRY — spec complete, prereqs closed, every criterion strong → 🚧), **`/review-step`** (CODE — lint, secret scan, unit tests, every negative case executed), **`/close-step`** (EXIT — docs, ADRs, validation gate positive+negative, contracts, no open errors → ✅). Each self-applies the **gate-robustness standard** (behavioral + a negative case, or `N/A — reason`).
2. **Branch + PR + protected `main`** for v0 *code*: build on a per-migration branch → self-reviewed PR (+ CI when it lands at Step 9) → tag the release; `main` is PR-only. (Docs may stay direct for iteration speed.)
3. **Two human checkpoints:** **A** = spec/plan approved before code (= plan mode); **B** = approval before the irreversible merge/tag.

## Alternatives Considered
- **Manual checklists only** (the prior "emergent" default). Rejected *now*: the build is starting and a command in Claude Code costs ~0; a written-only checklist is the "suggestion" the Master Plan warns about — silently skippable.
- **A lean 1–2 commands** (just `/verify`). Rejected: the *entry* gate (refuse code until the spec/criteria are strong) prevents the cheapest-to-fix defects; dropping it keeps the weakest link.
- **The full 10-command catalog** (`/new-phase`, `/write-adr`, `/run-chaos`, `/audit-foundation`, …). Rejected for now: scaffolders + a standing audit are coordination/scale ceremony; the 3 gates carry the airtight core. They stay optional follow-ons.
- **Direct-to-main** (status quo). Rejected for code: no pre-merge gate, and it conflicts with "each migration = a clean, reviewable release."

## Consequences
- **Easier:** a gate can't be silently skipped — it runs and prints PASS/FAIL; Checkpoint B has a natural home (the PR merge); the committed commands are a portfolio signal (the methodology is *wired in*, not just described).
- **Harder:** slightly more ceremony per unit (three commands; a branch). Mitigated by keeping to the 3 gates (not 10) and self-review (no external-reviewer hard gate).
- **Impact:** supersedes the "enforcement is emergent / don't pre-decide" note in [05-methodology](../05-methodology.md) + CLAUDE.md; `procedure-registry` marks the gate commands **Written**; the six-angle chaos matrix + scaffolder commands stay deferred.
