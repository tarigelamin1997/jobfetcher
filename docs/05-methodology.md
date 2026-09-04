# 05 · Methodology Adoption

> How JobFetcher adopts Tarig's own **Master Project Implementation Plan** (the gate-driven, "airtight" phase methodology used to ship InsureFlow) and **Modern DE Best Practices** — **right-sized** for a minimalist, single-user, evolutionary project via the [defensibility rubric](00-design-philosophy.md#the-defensibility-rubric). The rule: *keep everything whose value is **memory across time**; shrink everything whose value is **coordination across people**.*

## The two pillars (adopted wholesale)
1. **Documentation as infrastructure** — the repo is the memory; any session resumes from the files alone. (Already the foundation of this doc set.)
2. **A standard not wired into a command is a suggestion** — adopted as *discipline*; enforcement *machinery* is emergent (below).

Plus the **four-layer pattern** for any project-wide standard: *define once → inherit via template → enforce at gate → audit*. And **safety-first / the Castle Principle** (in [00-design-philosophy](00-design-philosophy.md#safety-first-engineering-the-castle-principle)).

---

## ADOPT (cheap, high-leverage even solo — value is memory across time)
- **ADRs with rejected alternatives.** Every significant decision gets one; "we chose X over Y because Y needs Z which violates W." The rejected road *is* the evidence of judgment. → [`adr/`](adr/).
- **Error/incident log — the Five Questions.** Every error: *what happened · why (root cause) · how (chain) · how fixed · how prevented (a concrete guard) + detection (what check would catch it earlier)*. Verbatim error strings (searchable). Read the log before re-attempting a fix. → [`ledgers/errors.md`](ledgers/errors.md).
- **Interface contract ledger** (Produces→Consumes), one file — guards cross-stage drift in a real multi-stage pipeline. → [`ledgers/interface-contracts.md`](ledgers/interface-contracts.md).
- **Phase index** (⬜/🚧/✅) + **locked-decisions table** + **naming conventions** — live single sources of truth. → [`ledgers/`](ledgers/).
- **Behavioral validation gates** (positive + negative). A presence/liveness check is *no gate* — it reports green on a broken system. The canonical lesson (InsureFlow ERR-001): a `localhost` healthcheck would have *passed* while the stack was broken; the behavioral gate caught it. → applied in [04-v0-build-plan](04-v0-build-plan.md).
- **Data contracts at boundaries** (Pydantic + dbt contracts/tests + freshness) · **idempotent operations** · **medallion with clear layer ownership** · **scenario-based seed/test data** · **pre-commit + secret scan** · **`terraform destroy` → $0** with `force_destroy`.

## RIGHT-SIZE (keep the idea, shrink the ceremony — solo scale)
- **Per-phase doc ceremony → collapsed.** One project doc set + short per-migration notes, not a heavy `CLAUDE.md`-per-phase with a 12-section template.
- **Chaos/stress discipline → a couple of targeted negative-injection tests** on the riskiest path (folded into the validation negative-cases), **not** the full six-angle matrix. Label skipped angles with a one-line reason.
- **Observability → right-sized:** a few real alarms (pipeline-didn't-run, cost-spike, error-rate) + documented SLOs, not a full dashboard suite. *(Shipped in v0.9.0 / [ADR-0029](adr/0029-ops-hardening.md): the dead-man "pipeline-didn't-run" alarm + a Lambda "error-rate" alarm → SNS email. The dashboards + SLO-calibration remainder stays deferred to M7, and even then modest.)*
- **Meta-ADRs → short paragraphs**, not full ceremony.
- **Fitness functions → only for genuine architectural invariants** (e.g. "the analytical plane never writes operational tables"), not one-per-property by rote.

## CUT / label-as-deferred (overkill at single-user scale — value is coordination across people)
- **A *human* external PR reviewer as a hard gate** → our own CI + the fresh-context adversarial Examiner + **CodeRabbit** (now a standing per-PR automated reviewer, [ADR-0019](adr/0019-agentic-build-orchestration.md)) suffice — only the *human* external-reviewer hard gate is cut. (A human co-reviewer stays optional.)
- **`/audit-foundation` as standing automation** → run an ad-hoc consistency check before a release, no standing command.
- **Full Templates Library abstraction** → inline the 2–3 skeletons we actually reuse (ADR, error entry).
- **One-file-per-phase / per-error directory sprawl** → flat ledgers (one `errors.md`, one decisions table).

Each cut is **labeled "deferred → adopt when X"**, never silently dropped — the labeling is itself the discipline and reads as a deliberate decision trail.

---

## Enforcement — the gate trio ([ADR-0013](adr/0013-enforcement-gate-trio-branch-pr.md))
The "emergent" machinery decision is now **made** — the trigger (building, inside Claude Code where a command is near-free) is met. The enforcement surface is **three committed Claude Code slash-commands** ([`.claude/commands/`](../.claude/commands/)), one per gate (the methodology *wired in*, not just written):
- **`/start-step`** (ENTRY) — refuses to start a build unit until its spec is complete, prereqs are closed, deferred procedures are authored, and **every validation criterion is strong** → sets 🚧.
- **`/review-step`** (CODE) — lint · secret scan · unit tests · **every negative case actually executed** · no hardcoded config.
- **`/close-step`** (EXIT) — docs current · ADRs present · validation gate positive+negative · no open errors · contract verified + *Produces* appended → sets ✅.

**Two human checkpoints** bracket every unit: **A** — spec/plan approved *before* code (= plan mode); **B** — approval *before* the irreversible merge/tag. **Git workflow:** v0 *code* builds on a per-migration branch → self-reviewed PR (+ CI from Step 9) → tag; `main` is PR-only **for everything, docs included** — enforced by branch protection with `enforce_admins: true`, not by convention ([ERR-015](ledgers/errors.md)).

**How the gates run — agentic per-unit pipeline ([ADR-0019](adr/0019-agentic-build-orchestration.md)).** The trio executes as a multi-agent pipeline per unit — **Builder** → **Reviewer** (`/review-step` + `/simplify`) → **Independent Verifier** (a *fresh-context, adversarial* agent that breaks the code against the spec + runs the gate itself — added after it caught crash-bugs the in-build reviewer missed on Step 4) → **Scribe** (`/close-step` + the ledgers; logs deviations + plan-adherence) → **Guardian** (`/security-review` + `/verify`) — with **parallelism across *independent* units** (not N agents on one unit, which would review a moving target + collide on files). Writes isolate via git worktrees; **CodeRabbit + the human are additional independent eyes per PR.** Proven on **C-2 first**, then scaled (P1/P2 applied to the process itself).

**The gate-robustness standard** (self-applied by the commands): a criterion that checks only existence/liveness is **no gate** — it reports green on a broken system. Every criterion must be *behavioral* + carry a *negative case* (or `N/A — reason`):

| Weak (presence / liveness) | Robust (behavioral) |
|---|---|
| Container is `running` | INSERT a row, SELECT it back — the value matches |
| Port is open | Produce a message, consume it, payload decodes against the schema |
| HTTP 200 returned | Response body matches the declared schema field-for-field |

Still **not** adopted (coordination/scale ceremony — deferred, labeled): the full 10-command catalog (scaffolders, `/audit-foundation`), per-phase doc ceremony, the six-angle chaos matrix, an external-reviewer *hard* gate.

## Documentation system summary (what lives where)
| Layer | Where | Updated |
|---|---|---|
| Principles / constitution | [00-design-philosophy](00-design-philosophy.md) | rarely, deliberately (with an ADR) |
| Reasoning / "why" | [01-session-decision-journal](01-session-decision-journal.md) + ADRs | as decisions happen (constructed live) |
| Design / "what" | [02-architecture](02-architecture.md) | as the design changes |
| Direction | [03-roadmap](03-roadmap.md) | after every release (it's a living hypothesis) |
| Current stage | [04-v0-build-plan](04-v0-build-plan.md) | per stage (just-in-time) |
| Live state | [ledgers/](ledgers/) | continuously |
| Bottleneck investigations | [investigations/](investigations/) | per investigation (a verified dossier between a backlog signal and an ADR decision — [ADR-0034](adr/0034-investigation-dossier-system.md)) |

> The test this system must pass (the **knowledge-transfer test**): a fresh session can read these files and answer *"what is this, why is it built this way, what's the current state, and what do I do next?"* — without the original context window. If it can't, the docs are incomplete.

### One canonical home per fact ([ERR-016](ledgers/errors.md))

The system above says which *layer* owns which *kind* of writing. It did not say which **file owns a given fact** — so the same content was copy-pasted into many, and every release meant hand-syncing **~27,700 words** across six files. Nothing failed when a copy went stale, so copies went stale.

**A caveat on how that was measured, because the obvious metric misleads.** Counting files that mention `"286 rows"` (12) or `"15.95"` (10) overstates the problem, and clearing those counts would make the docs *worse*. Both are **historical measurements** — frozen the moment they were taken, incapable of going stale. Each appears in a different row of a different ledger (the ADR that decided it, the contract it validated, the procedure gate it passed, the backlog item it closed), where it is the evidence for *that* row's claim; stripping it to a link would make each row less self-contained, which is the opposite of what a ledger is for.

**The duplication that actually cost something was the other two kinds:** *derived counts* typed as literals — now CI-guarded, so they cannot drift — and *release narrative* restated in full. `CLAUDE.md` had become a second CHANGELOG: eleven per-release paragraphs, ~15 numbers owned elsewhere. That is the one that got cut (3,166 → 1,832 words), and the rule below is what stops it growing back.

| Fact class | Owner (states it in full) | Everyone else |
|---|---|---|
| Release narrative + the numbers measured live for a release | [`CHANGELOG.md`](../CHANGELOG.md) | one line + a link to the tag section |
| Live build/unit state (what shipped, what's in flight) | [`ledgers/phase-index.md`](ledgers/phase-index.md) | link to the row |
| Port / contract shapes | [`ledgers/interface-contracts.md`](ledgers/interface-contracts.md) | link to the row |
| Locked decisions | [`ledgers/decisions-locked.md`](ledgers/decisions-locked.md) | link to the row |
| Direction + open hypotheses | [`03-roadmap.md`](03-roadmap.md) | link |
| Reasoning, reversals, the wrong turns | [`01-session-decision-journal.md`](01-session-decision-journal.md) + [ADRs](adr/) | link to the § / ADR |
| Observed bottlenecks | [`ledgers/backlog.md`](ledgers/backlog.md) | link to the B-N |

**`README.md` and `CLAUDE.md` own nothing.** They are the two entry points, so they carry *orientation* — what this is, what's deployed, where to look — and link out for detail. When they restate a per-release measurement they become a second copy that has to be synced, which is exactly how the debt accumulated.

**Two kinds of number, handled differently:**

- **Derived** — a count that a command can produce right now (tests, Terraform resources, releases, ADRs, ERR entries). **Never trust a typed literal.** Wrap it in a non-rendering marker — `<!--fact:tests-->556<!--/fact-->` — and [`scripts/check_docs.py`](../scripts/check_docs.py) diffs it against the real command in CI. A stale one fails the build. `python scripts/check_docs.py --list` prints the live values.
- **Historical** — a measurement taken once, on a date (`286 rows over the Data API`, `avg spread 15.95`, `$0.0155/posting`). These are **correct forever as of their date** and must not be automated. The fix for these is *attribution*, not automation: state them once, with the date and the conditions, in the owning file — and link from anywhere else that needs them.

Confusing the two is its own bug: automating a historical measurement would demand that history change, and typing a derived one guarantees it goes stale.

**One labelled exception: [`README.md`](../README.md)'s *Proof* section.** It restates live-run measurements the CHANGELOG owns, and it stays. This repo is explicitly a portfolio piece as well as a tool; a reader deciding whether it is worth their time needs the evidence *in front of them*, not one click away. Removing it to satisfy a de-duplication rule would make the artifact worse to serve a rule that exists to make it better. Per the [defensibility rubric](00-design-philosophy.md), that is labelled here rather than left as an unexplained duplicate — and it is bounded: **dated, live-run results only.** Release narrative, contract shapes and unit state still link out.
