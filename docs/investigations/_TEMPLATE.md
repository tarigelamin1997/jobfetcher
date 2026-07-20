---
id: INV-NNN
title: <short problem title>
status: open        # open | verifying | verified | handoff-ready | in-progress | fixed | killed
severity: tbd       # crucial | non-crucial | tbd  (crucial = schema/scoring-semantics/live-infra/new-dep/PII)
logged: YYYY-MM-DD
updated: YYYY-MM-DD
source: <backlog ref, e.g. B-4, or "P2 scan 2026-..."; who/what surfaced it>
---

<!-- One folder per case: this file is docs/investigations/INV-NNN-<slug>/README.md; put raw
     artifacts (query dumps, logs, before/after) in a sibling evidence/ folder. Relative links
     from here: ../../adr/ · ../../ledgers/ · ../../../src/ . Delete this comment. -->

# INV-NNN · <title>

**Status:** `<status>` · **Severity:** `<severity>` · **Owner of the fix:** _(a Surgeon, once handed off)_

> One sentence: what this bottleneck is and why it matters. _(Delete this quote line once written.)_

## The problem
_What it is, plainly — what it does, and whom it hurts. No jargon, no proposed fix yet._

## Does it exist? — verification
_The core of the dossier: **prove it's real, or KILL it.** Evidence, not assertion — measured numbers, log lines, code `file:line`, read-only live-stack queries. Every claim must be **re-runnable**: give the exact command/query and the expected result, so a future agent (or the Surgeon) can reproduce it. If the evidence shows the problem isn't real or isn't worth it → set `status: killed`, record why here, and stop._

- **Evidence 1** — _(measurement / observation)_. Reproduce: `<exact read-only command/query>` → expected `<result>`.
- **Evidence 2** — …
- **Magnitude:** _(how big — the number that decides whether this is worth building)_.

## Mechanism (root cause)
_Why it happens, traced to the code. `file:line` references. Distinguish the symptom from the cause._

## Blast radius
- **Changes:** _(files/components the fix will touch)_.
- **Must NOT change:** _(the guardrails — invariants the fix must preserve)_.
- **Unaffected:** _(what's explicitly out of the way)_.

## Fix plan (the handoff guideline)
_The **minimal** change that solves the *present* problem (design cheap seams, don't build the future). A plan a Surgeon executes — **not code.** Name the exact files, the **reuse points** (existing functions/patterns), and the sequence. If there are rungs (minimal → richer), name the recommended rung and why._

1. _Step / change_ — reuse `<existing function @ file:line>`.
2. …

## Validation gate
_How the fixing agent **proves** it's fixed — behavioral (asserts the thing does its job through its real interface) **and at least one negative case**. A presence/liveness check is no gate._

| # | Behavioral (positive) | Negative case |
|---|---|---|
| VG-a | _…_ | _…_ |

## Out of scope / rejected
_What NOT to do — the over-reach guardrails; alternatives considered and rejected (with the one-line reason)._

## Connections (typed — the graph seam)
_Typed edges, `<verb> → <target>` (vocab: causes/caused-by · touches · blocks/blocked-by · depends-on · relates-to · duplicates/supersedes). Target = a dossier ID, `file:path`, an ADR/roadmap item, or an external prereq._

- `touches` → `file:src/jobfetcher/...`
- `blocks` → _(a capability, e.g. M7 calibration)_
- `relates-to` → [ADR-00NN](../../adr/00NN-...)

## Handoff
- **Severity tier:** `<crucial|non-crucial>` → _(which human checkpoints apply — crucial = brief + PR; deploy is always a checkpoint)_.
- **Ready-for-Surgeon checklist:** verified ✅ · root-caused ✅ · fix plan ✅ · validation gate (behavioral + negative) ✅ · out-of-scope ✅.
- **On fix:** the **Resolution** section below is filled at close → set `status: fixed`.

## Resolution — as-built _(filled at close, when the fix ships)_
> ⏳ **Pending** — not yet built. When a Surgeon ships the fix, record here **what was actually built** (not just the proposal above), so this closed case is a self-contained archive a future phase can edit or extend from without re-reading the code.

- **What shipped:** _(the change in prose — the as-built)_.
- **Rung taken · divergence from the Fix plan:** _(which rung; any deviation from the proposal above + why)_.
- **Key files + decisions:** _(where the code lives; the load-bearing choices)_.
- **Links:** PR #… · [ADR-00NN](../../adr/…) · CHANGELOG `[vX.Y.Z]` · commit `<sha>`.
- **Extending / editing later:** _(the seams to build on, the gotchas — how a later phase reuses or modifies this)_.
