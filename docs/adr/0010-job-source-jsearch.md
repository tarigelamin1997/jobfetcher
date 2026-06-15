# ADR-0010 — Job source: JSearch (probe-free → Pro), single-source for v0

## Status
Accepted

## Context
We ingest *unfiltered* job data from external APIs and land/filter it ourselves. We need a source with real coverage of the **KSA/GCC Data-Engineering market** and **complete JD text** (the LLM scores on the full description). Two candidates: **JSearch** (RapidAPI / OpenWeb Ninja — aggregates Google-for-Jobs → LinkedIn/Indeed/Glassdoor/ZipRecruiter/company sites; quota-rationed: free 200 req/mo, Pro 10k/mo at $25) and **Adzuna** (free, its own index, max 50 results/page, often-truncated descriptions). Volume is moderate and **request-quota-bound, not storage-bound**.

## Decision
**JSearch is the source.** v0 uses it as the **single source** (one API ⇒ no cross-source dedup; exact-id only). The **first build step probes JSearch's free 200-request tier** on real `country=sa/ae` DE queries to confirm GCC depth + full JD text, then upgrades to **Pro ($25/mo)**. **Adzuna is deferred** — a candidate second source added at M2 only if a coverage gap is observed.

## Alternatives Considered
- **Adzuna only (free).** Rejected for v0: its own index under-covers the major boards for our market, and it commonly returns **truncated descriptions** — poor for LLM scoring. Kept as a possible later source.
- **Both sources from v0.** Rejected: $25 *and* it drags **cross-source clustering dedup** forward into v0 (we deferred that to M2) — premature complexity for one user.
- **Commit to JSearch Pro blind (no probe).** Rejected: don't pay $25 before evidence. The free 200-req tier is plenty to validate GCC depth at $0 first.
- **"Pay for cleaner data" framing.** Rejected as the *reason*: both APIs return structured JSON; you pay JSearch for **coverage + freshness + full JD text**, not preparation. Cleanup can't create coverage.

## Consequences
- **Easier:** strong coverage from one API (rides Google-for-Jobs; supports GCC via the `country` parameter — `sa/ae/qa/om`), full JD text, and JSearch **pre-merges many duplicates** (returns one job with multiple `apply_options`) — so v0 needs only exact-id dedup. Spend is evidence-based (probe → Pro).
- **Harder:** a real $25/mo dependency with a **rationed quota** → the request-budget + page-cap + `date_posted` window become config knobs; regional GCC boards (Bayt/GulfTalent) may be under-covered by Google-for-Jobs — a signal for a future source.
- **Impact:** multi-source + cross-source clustering dedup stays **M2** ([ADR-0005](0005-dedup-cluster-and-surface.md), [roadmap](../03-roadmap.md)); ingestion is a **medallion landing** ([02-architecture](../02-architecture.md#ingestion--medallion-landing-the-operational-medallion)); the v0 build opens with the coverage probe ([04-v0-build-plan](../04-v0-build-plan.md)).
