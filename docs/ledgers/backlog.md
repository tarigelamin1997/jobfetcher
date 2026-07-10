# Ledger · Backlog — observed bottlenecks & requests from real use

> **What this is:** the raw log of friction and feature-requests discovered by *actually using the tool* — the direct input to **step 2 of the [P2 migration-decision protocol](../03-roadmap.md#the-migration-decision-protocol-how-the-next-step-is-actually-chosen)** ("surface the top-3 bottlenecks"). An entry here is an **observation awaiting the protocol**, **not a commitment** — the protocol ranks these by leverage (`capability ÷ complexity`) after each release and picks the next migration. Nothing here is scheduled until it graduates into the [roadmap](../03-roadmap.md) / [phase-index](phase-index.md) as a real release.
>
> Keep it honest: record the *why* (what real use exposed it) and the *current state* (what exists today), so a future session — or the next Investigator — can rank it without re-discovering it. Convention: **What / Why / So-what**, links to the relevant ADRs.

---

## B-1 · Reachable full job list from the digest (the "see your export" dead-end)

**Logged:** 2026-07-10, from Tarig reviewing a live digest (the first unattended-cron email). **Status:** captured — candidate for the next P2 round. **Not built.**

**What.** The daily digest surfaces the new matches + the top-5 "still open" jobs, then trails off into two lines of **plain, non-clickable text**:
- *"…and 55 more — see your export"* — the still-open overflow (`core/notifier.py`, `_STILL_OPEN_TOP_N = 5`).
- *"+225 more scored below your threshold of 60"* — the below-threshold footer.

So **~280 scored jobs are unreachable from the email** — the still-open overflow **and** the entire below-threshold ("didn't qualify") set. There is no link, button, or destination: *"see your export"* refers to [`scripts/export.py`](../../scripts/export.py) ([ADR-0024](../adr/0024-query-via-export.md)), a **local script the user must run by hand** — nothing clickable.

**Why (what real use exposed).** Tarig wants to **be the judge of the long tail** — to open the full list (*including* the below-threshold "unqualified" jobs), **filter/sort/search it with real tools**, and act on it (he may disagree with a score, override it, or spot a hidden fit). The truthful digest deliberately stays concise; the missing half is a **reachable, filterable surface for everything the digest compresses away.** The data already exists (the export includes all scored + silver rows) — the gap is purely a **clickable path from the push (email) to the queryable dataset.**

**So-what (design space — a right-sized ladder; decide the rung when the protocol picks this up).**
1. **Minimal — a link to a downloadable export.** The daily run uploads the snapshot to S3 (`export.py --s3` already does the upload) and the digest embeds a **presigned URL** to the CSV (opens in Excel/Sheets → filter/sort). Cheapest; presigned links expire (e.g. 7 days) — fine for a daily email.
2. **Medium — a self-contained filterable HTML page.** The run renders a **single-file HTML** table (client-side sort/filter/search over the full job set — a Datasette-lite) → S3 → presigned/static link in the digest. One click → browser → judge/filter. No server, no auth; a strong portfolio surface.
3. **End-state — a hosted read dashboard** over the data (the [ADR-0024](../adr/0024-query-via-export.md) "hosted dashboard is the end-state"), potentially with the **score-override / graduation actions inline** — the user judges → overrides → the change joins the override/reassess lineage ([ADR-0026](../adr/0026-outcome-tracking-override-lineage.md) / [ADR-0023](../adr/0023-reassess-replay.md)). Bigger build (hosting + auth) → a later migration.

**Connections:** [ADR-0024](../adr/0024-query-via-export.md) (export/query access — this is its natural "make it reachable" evolution) · [ADR-0027](../adr/0027-digest-truthfulness.md) (the digest text that promises "your export") · [ADR-0026](../adr/0026-outcome-tracking-override-lineage.md) (judging → override lineage) · the roadmap's **"hosted dashboard"** end-state.

**Leverage (first-pass):** medium–high capability (closes the "I can only see 5 of ~285 jobs" gap + realizes the human-as-final-judge loop) for **low** complexity at rung 1 (a presigned link on the already-built `--s3` export). A likely strong P2 candidate.

---

## B-2 · Digest deliverability — the email landed in Gmail **Spam** ⚠️

**Logged:** 2026-07-10, same review (the digest was found under Gmail's *Spam* label — 11 JobFetcher emails in spam). **Status:** flagged — **verify before acting.** Not built.

**What.** The SES-sent digest is being **filtered to Spam** by Gmail.

**Why it matters.** A digest in spam ≈ **no digest** — the daily-tool value collapses if the user never sees it. This is arguably **higher leverage than B-1** (B-1 improves an email the user must first actually receive in their inbox).

**Likely cause (to verify, not assume).** SES sending from a **raw email-address identity** without domain-level authentication alignment → Gmail distrusts a new bulk sender. Prime suspects: no **DKIM** (Easy DKIM on a verified *domain*), no/weak **SPF**, no **DMARC** record, or a From-domain that isn't aligned. (SES sandbox / low sender reputation are secondary possibilities.)

**So-what (fix path, right-sized).** Verify a **sending domain** in SES → enable **Easy DKIM** → publish **SPF** + a **DMARC** record → send From the aligned domain. Interim mitigation: mark **"Not spam"** + add the sender to contacts so today's digests reach the inbox. **Verify the actual DNS/SES state first** — don't change infra on assumption.

**Connections:** the SES sender config ([`terraform/`](../../terraform/) SES identity + `SES_SENDER`) · [ADR-0027](../adr/0027-digest-truthfulness.md) (the artifact being delivered).

---

> **How this feeds the roadmap:** when the current program closes and P2 reopens, these entries are ranked (leverage = capability ÷ complexity) alongside the [roadmap](../03-roadmap.md) candidates (M2 dedup, M3 Step Functions, near-miss M4, CV tailoring). A graduated entry becomes a labeled release; a rejected one stays here with the reasoning.
