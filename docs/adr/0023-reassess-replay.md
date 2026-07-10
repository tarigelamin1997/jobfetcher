# ADR-0023 — Reassess / replay: re-score existing jobs on an updated profile (no re-fetch)

**Status:** Accepted · shipped v0.4.0 (2026-07-06)
**Date:** 2026-07-03

## Context

The candidate's profile changes over time — a new skill, a finished project, a certification. When it does, jobs already in the system should be **re-evaluated**: a posting that scored as a `stretch` last week can become a `strong_fit` today. Tarig surfaced this from real use: *"give me an option to re-run/reassess the saved records on new input — I made progress in a skill, so this posting should now be a strong fit."*

This is the medallion architecture's designed-in **immutable-bronze → replay** property: bronze is the permanent, append-only record of every job ever fetched; silver/gold/score are **pure functions** over it. So re-scoring is a **replay over existing data with zero JSearch calls** — only LLM scoring tokens (pennies). It also realizes the **graduation half of the old M4 hypothesis** ("watch → re-score → graduate when you learn a skill"), re-derived from usage via the P2 protocol.

**The schema + write path already support it** (they were built anticipating this): `score.previous_score` is commented *"for near-miss re-scoring"*, and `save_score` (`repository_postgres.py`) already carries the existing score into `previous_score` on an upsert conflict. The **only** missing piece was that scoring skips already-scored postings (`score_gold` reads `status='gold_candidate'` and marks them `'scored'`), so nothing re-scores today.

## Decision

Add a **`reassess` mode** to the single Lambda handler. Flow (builds on the runtime config, ADR-0022): **edit `profile.local.yml` → `python scripts/push_config.py` → invoke `{"mode":"reassess"}`.**

- **`Repository.get_scored_for_reassess()`** — returns every `status='scored'` posting with its **current** `score` + `fit_category` (so the old→new delta is computable).
- **`core.ingest.reassess(...)`** — re-scores that set against the current profile, structurally mirroring `score_gold`'s **concurrent** loop (H-2: LLM calls on a thread pool, all DB writes on the main thread; H-1 failure isolation + retry; the deadline guard). Each `save_score(..., previous_score=old_score)` moves the old score into `previous_score`. Returns `{reassessed, graduated, downgraded, unchanged, failed, deferred}` + a `graduations` list — a **graduation** = a posting that newly reached at/above the threshold (`old < threshold <= new`).
- **Handler `mode` routing** — `event["mode"]=="reassess"` (via the pure `resolve_mode`) runs `reassess()` after syncing the profile from config, then returns its report; it **skips fetch, gold, and notify**. Default (no mode) = the normal pipeline, unchanged.
- **Scope (correct by design):** only the `scored` set is re-scored. A *skill* change doesn't alter gold membership (gold = title/location/avoid-keyword, not skills), so this is exactly right for the graduation use case. Re-running **gold** for a targeting/avoid change is a separate, later concern.

## Alternatives considered

- **Re-fetch then re-score.** Rejected: wasteful (JSearch quota + tokens) and it *throws away* the immutable-bronze value — the whole point is that history is already stored and replayable.
- **A separate scoring service / queue.** Rejected: over-infra for a single daily user; the existing concurrent `score_gold` machinery already does exactly this work.
- **Auto-reassess on every normal run.** Rejected: wastes LLM tokens re-scoring unchanged jobs when the profile didn't change. Kept as an **explicit mode**; a later refinement can auto-trigger only when a profile-hash change is detected.
- **Score-history table now.** Deferred: today `score` keeps *current + previous* (one step), enough to report graduations. A full append-only `score_history` (for "45→62→78 over time" charts) is a later add, alongside the query/filter capability.

## Consequences

- A profile improvement re-evaluates the whole backlog for **~$0** (no re-fetch) and reports which jobs **graduated** — the tool's core intelligence ("my progress changes my matches").
- `previous_score` is populated on every reassess, so the before→after is persisted + queryable.
- New event shape: `{"mode":"reassess"}`. New repo method + orchestrator; the handler's default path is untouched.
- **Follow-ons (noted, not built here):** the "what graduated" **digest email** rides the email-UX unit (don't entangle the notifier rework); the **query/filter** surface (export to SQLite/CSV → Datasette/Excel) is the next capability; a `score_history` table if time-series is wanted.

Full reasoning: [journal](../01-session-decision-journal.md) · plan §41. Related: [ADR-0022](0022-runtime-config-in-s3.md) (the edit→push flow), [ADR-0016](0016-llm-dissection-at-silver.md) + the immutable-bronze/replay principle in [02-architecture](../02-architecture.md).
