# Ledger · Backlog — observed bottlenecks & requests from real use

> **What this is:** the raw log of friction and feature-requests discovered by *actually using the tool* — the direct input to **step 2 of the [P2 migration-decision protocol](../03-roadmap.md#the-migration-decision-protocol-how-the-next-step-is-actually-chosen)** ("surface the top-3 bottlenecks"). An entry here is an **observation awaiting the protocol**, **not a commitment** — the protocol ranks these by leverage (`capability ÷ complexity`) after each release and picks the next migration. Nothing here is scheduled until it graduates into the [roadmap](../03-roadmap.md) / [phase-index](phase-index.md) as a real release.
>
> Keep it honest: record the *why* (what real use exposed it) and the *current state* (what exists today), so a future session — or the next Investigator — can rank it without re-discovering it. Convention: **What / Why / So-what**, links to the relevant ADRs. When an entry is investigated *properly*, it graduates to a durable, evidence-verified **dossier** in [`docs/investigations/`](../investigations/) (via [`/investigate`](../../.claude/commands/investigate.md), [ADR-0034](../adr/0034-investigation-dossier-system.md)); add a `**Dossier:**` link to the entry alongside its `**Status:**`.

---

## B-1 · Reachable full job list from the digest (the "see your export" dead-end)

**Logged:** 2026-07-10, from Tarig reviewing a live digest (the first unattended-cron email). **Status:** ✅ **SHIPPED — `v0.10.0`, 2026-07-10** ([ADR-0030](../adr/0030-reachable-full-list-from-digest.md)). Squad-built (Investigator → Surgeon → Examiner CLEAN PASS → auto-merge PR #30 → live deploy), rung 2 = self-contained HTML page + presigned S3 link, non-fatal; live-validated (`get_all_scored` over the Data API = 286 rows, report page rendered + uploaded).

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

## B-3 · Scoring boundary noise — the shortlist cutoff is a coin-flip ✅ **SHIPPED v0.11.0 (2026-07-11)**

**Logged + graduated + shipped:** 2026-07-11, by a fresh P2 data-quality scan (read-only on the live stack). **Status:** **the P2-scan winner** — built by the agentic squad, merged (PR #31), deployed + **live-validated** (reassess `graduated: 0` under the unchanged profile; `mean_delta` 8.4 vs the ~16 baseline), tagged **v0.11.0**.

**What.** The scorer's holistic number is a **non-deterministic LLM at temp 0**; with the profile held static (pure noise) it drifts **avg 15.95 pts, max 60**, and **62 of 286 scores sat within ±16 of threshold 60** — roughly the entire ~61-job shortlist boundary flipping in/out at random. Reassess **"graduation" badges fired on that noise** (15 measured false positives under an unchanged `profile_hash`).

**So-what (the fix, shipped).** **Boundary resample** (median-of-N=3, boundary-only, cost-guarded, deadline-aware) collapses the coin-flip exactly where membership is decided; **honest graduations** badge a crossing only when the profile actually changed. No migration / infra / dep ([ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md)).

**Overturned with evidence:** the pre-committed **M7 shadow-`code_total` cut-over** was the roadmap agent's initial pick; the data-quality agent killed it — the code-total inherits the LLM subscore noise (max spread **71** > the holistic's 60), and there's zero ground truth to calibrate toward (`application_event` = 0 rows). M7 stays parked.

**Still-parked companions (named, not built):** the **silent-`500` alarm gap** ✅ **SHIPPED 2026-07-21** ([INV-002](../investigations/INV-002-silent-500-alarm/README.md), PR #35) — a mode-gated `PIPELINE_ALARM` log-metric-filter → the existing SNS topic now pages on a returned `statusCode:500` from the unattended daily run; the **dark human-judge loop** (0 outcomes logged — the reason calibration has no target; a one-click feedback affordance in the digest/report is the later unit).

**Connections:** [ADR-0028](../adr/0028-scorer-subscores-shadow.md) (the shadow instrument the scan re-read) · [ADR-0023](../adr/0023-reassess-replay.md) (the reassess/graduation feature made honest) · M7 (parked, evidence above).

---

## B-4 · Full-backlog reassess is deadline-partial (resample throughput) — observed 2026-07-11

**Logged:** 2026-07-11, from the v0.11.0 live-validation. **Status:** observation (P2 input), **not** built. Not urgent — the daily path is unaffected.

**What.** The v0.11.0 boundary resample ([ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md)) re-scores ~1/5 of jobs at 3× LLM calls, cutting per-run throughput. A full-backlog `{"mode":"reassess"}` over the ~286-scored set now hits the **deadline guard** (worked as designed — returned `partial`: **163 reassessed / 123 deferred**). Because reassess is **ordered by `posting_id` and deadline-bounded**, successive runs re-do the *head* — the deferred **tail (highest `posting_id`s) is never reached** in this pattern, so a profile improvement wouldn't lift the newest matches on reassess.

**Why it matters (mild).** Only the *manual, occasional full-backlog reassess* is affected; the daily incremental scoring (~10–30 new gold jobs) pays trivial extra time and is fine. The gap is coverage of the reassess tail after the set grew past what fits one 15-min window.

**So-what (candidate fixes, right-sized — pick when it earns it).** (a) **Rotate/paginate** the reassess order so successive runs advance the tail (cheapest — a cursor / `ORDER BY least-recently-reassessed`); (b) raise per-run throughput (more workers / only-resample-when-needed); (c) **async invoke + read logs** for long reassess runs (the sync CLI times out at ~14.5 min though the Lambda completes) — a procedure note, not code. This is the natural pull toward **M3** (chunking / Step Functions) if the backlog keeps growing.

**Connections:** [ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md) (introduced the throughput cost) · [ADR-0023](../adr/0023-reassess-replay.md) (reassess) · the H-2 deadline guard · **M3** (chunking, the documented scale path).

---

> **How this feeds the roadmap:** when the current program closes and P2 reopens, these entries are ranked (leverage = capability ÷ complexity) alongside the [roadmap](../03-roadmap.md) candidates (M2 dedup, M3 Step Functions, near-miss M4, CV tailoring). A graduated entry becomes a labeled release; a rejected one stays here with the reasoning.
