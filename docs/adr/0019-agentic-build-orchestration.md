# ADR-0019 — Agentic build orchestration (per-unit gate pipeline + cross-unit fan-out)

## Status
Accepted

## Context
[ADR-0013](0013-enforcement-gate-trio-branch-pr.md) set the build discipline — the gate trio (`/start-step` → `/review-step` → `/close-step`) + branch/PR — but left *how the gates are executed* open ([05-methodology](../05-methodology.md): "enforcement is emergent — add machinery when a real need justifies it"). Crossing from design into the v0 build, two needs now justify machinery: (1) Tarig wants to build the phases with a **multi-agent team** he can drive and watch (the orchestration is itself portfolio signal), and (2) the gate roles map naturally onto distinct agents. The failure mode to avoid: naïvely running N agents *simultaneously on one unit* — the reviewer reviews a moving target and agents collide on the same files.

## Decision
Build each unit as a **per-unit pipeline mapped to the gate trio**, with **fan-out across independent units** (not across agents on one unit):

- **Pipeline per unit:** **Builder** (implements the unit spec) → **Reviewer** (`/review-step` + `/simplify` — clean, minimal, idiomatic) → **Scribe** (`/close-step` + the ledgers — logs what was built, records any deviation from the plan, verifies plan-adherence + the validation gate) → **Guardian** (`/security-review` for secrets + `/verify` for behavioral correctness). This roster is the initial set, **extensible** as real needs appear.
- **Parallelism is across units:** run *genuinely independent* build units concurrently (e.g. a no-AWS schema unit and an unrelated unit), each as its own pipeline. The roles *within* a unit are sequential because the reviewer/scribe need the builder's output to exist first.
- **Writes are isolated:** units (or agents) that touch the same files run in **git worktrees** (or one-writer-at-a-time) so concurrent work never collides.
- **Prove then scale (P1/P2 on the process):** validate the pattern on **one unit first (C-2)**, observe, then widen the fan-out. Every unit still passes the two human checkpoints — spec before code; approval before merge/tag.

The **Workflow** tool (deterministic, scripted fan-out/pipeline) remains an available execution mode when repeatability matters more than hands-on driving.

## Alternatives Considered
- **Solo sequential build (one agent runs the gates in series).** Works, but slower and forgoes the orchestration as a portfolio artifact; separation-of-concerns is weaker when one agent wears all hats.
- **Fully parallel — N agents simultaneously on the *same* unit.** Rejected: the reviewer/scribe operate on a moving target, and concurrent edits to the same files cause git conflicts. The real independence is *across* units, not *within* one.
- **Workflow-scripted only (no hands-on agent team).** Rejected as the default because Tarig explicitly wants to drive + watch the team (FleetView); kept as an optional mode for repeatable runs.

## Consequences
- **Easier:** the gate trio ([ADR-0013](0013-enforcement-gate-trio-branch-pr.md)) becomes the literal pipeline stages; separation of concerns is enforced by distinct agents; the orchestration is a visible senior/staff portfolio signal; deviations are logged live (documentation-as-infrastructure).
- **Harder:** more token cost + coordination than a solo build; worktree isolation adds setup; on small units the full team can be overkill — mitigated by right-sizing (start on C-2; fan out only across genuinely independent units).
- **Impact:** resolves the "enforcement = emergent" open item in [05-methodology](../05-methodology.md); the first run is **C-2** (schema + `Repository` — [ADR-0018](0018-persistence-sqlalchemy-data-api-repository.md), plan §31); applies to every subsequent v0 unit and later migrations.
