---
description: INVESTIGATE — assume the read-only Investigator role and turn one candidate bottleneck into a verified, evidence-backed dossier (or KILL it). Writes no code — only the dossier.
argument-hint: <candidate bottleneck, e.g. "B-2" or a one-line problem>
---

# /investigate — the read-only bottleneck scout (produces a dossier)

Assume the [Investigator identity](../../.agents/roles/investigator.md): a fresh, **strictly read-only** scout. Turn the candidate into an evidence-verified [dossier](../../docs/investigations/) — *does it exist? · what causes it? · what's the minimal fix, and how do we know it worked?* — or **KILL it** with evidence. **Read anything (code + the live system, read-only); mutate nothing but the dossier.** This is the squad's Investigator stage, slotting *before* [`/start-step`](start-step.md); the Surgeon then builds from the dossier.

**Candidate:** $ARGUMENTS

Run the steps **in order**. Report **PASS / FAIL / SKIP** (or **KILL**) for each with a one-line note. Evidence over assertion — a claim you can't reproduce read-only is not evidence.

### Step 1 — Locate or open the dossier (one folder per case)
- Find the candidate's existing case folder under `docs/investigations/`; if none, create the folder `docs/investigations/INV-NNN-<slug>/` (next free `INV-NNN`) and write the dossier as its `README.md`, copied from [`_TEMPLATE.md`](../../docs/investigations/_TEMPLATE.md). Add its row to the [index](../../docs/investigations/README.md). Set `status: verifying`. Put any raw artifacts (query dumps, logs, before/after) in a sibling `evidence/`.
- **FAIL** if the template's required sections aren't all present in the new `README.md`.

### Step 2 — Does it exist? (verify or KILL)
- Prove the problem on **real code/data**: measured numbers, log lines, `file:line`, **read-only** live-stack queries. Every claim must be **re-runnable** (record the exact command + expected result).
- If the evidence shows it isn't real or isn't worth it → **KILL**: set `status: killed`, record why, stop here.
- **FAIL** if any claim in "Does it exist?" is asserted without a reproducible check.

### Step 3 — Mechanism + blast radius
- Trace the **root cause** to code (symptom ≠ cause). Map the blast radius: files that change · files that must NOT change · what's unaffected.
- **FAIL** if the root cause is a guess, or the blast radius is unstated.

### Step 4 — Minimal-fix plan + validation gate
- Draft the **smallest** change that solves the *present* problem (cheap seams, not the future): exact files · **reuse points** (existing functions) · sequence. **Plan only — write no code.**
- Author the **validation gate**: behavioral **+ at least one negative case** (a presence check is no gate).
- **FAIL** if the plan expands scope, or the gate lacks a negative case.

### Step 5 — Severity + typed connections
- Classify **severity** — crucial (schema/scoring-semantics/live-infra/new-dep/PII) vs non-crucial (see the [severity gate](../../.agents/agentic-workflow.md#the-severity-gate)). Doubt rounds up.
- Fill the **Connections** as typed edges (`<verb> → <target>`) — the graph seam.
- **FAIL** if severity is unset or a connection isn't typed.

### Step 6 — Hand off (or leave killed)
- If verified: complete the Handoff checklist and set `status: handoff-ready`; update the index row.
- **FAIL** to reach `handoff-ready` if any section still lacks evidence (the gate-robustness standard, self-applied).

## Allowed mutations
ONLY: files inside the candidate's case folder `docs/investigations/INV-NNN-<slug>/` (its `README.md` dossier + an optional `evidence/`) **and** `docs/investigations/README.md` (its index row). **NEVER** any `src/`, `tests/`, `terraform/`, config, or git action — the investigation writes no code (that's the Surgeon's job, from this dossier).

## Output
A table — **Step | Status | Notes** — then either `INV-NNN is handoff-ready — <severity>; ready for the Surgeon.` or `INV-NNN KILLED — <one-line reason>.`, with the dossier path.
