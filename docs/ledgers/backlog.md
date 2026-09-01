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

---

## B-5 · A daily alarm is not an alarm — nothing escalates a *persistent* failure ⚠️

**Logged:** 2026-09-01, from [ERR-010](errors.md). **Status:** open — the real residual gap from the 38-day outage. Not built.

**What.** The `pipeline-returned-500` alarm ([INV-002](../investigations/INV-002-silent-500-alarm/README.md)) worked exactly as designed: it fired on **all 38** days of the outage and SNS delivered **two emails per day** (ALARM + OK) to the operator's inbox — confirmed in Gmail. The pipeline still sat dead for 38 days.

**Why it matters (this is the uncomfortable one).** Detection was never the gap, so *adding another alarm is treating the wrong failure.* An alarm that fires every single morning becomes indistinguishable from the product it is reporting the absence of — and in this case literally so: the daily SNS email *replaced* the daily digest email in the inbox, at roughly the same hour. Every gate this project has fires at **build** time (start/review/close-step, Investigator → Surgeon → Examiner). Nothing fires at **use** time.

**So-what (design space — nothing chosen).** (a) **Escalate on persistence, not occurrence** — a second alarm on "N consecutive days in ALARM" routed differently (the CloudWatch-native shape is an `M out of N` datapoints alarm on the same metric). (b) **Alarm on the absence of the *product*, not the presence of an error** — SES `Send == 0` over 24h is the one signal that means "the tool did not do its job", whatever the cause; it would have caught both this outage *and* a run that silently succeeds while finding nothing. (c) **Make the OK email stop** — the `ok_actions` half doubles the volume and carries no information on a chronic failure. (d) Accept it and rely on noticing the missing digest — which is what actually happened, 38 days late.

**Connections:** [ERR-010](errors.md) (the incident) · [INV-002](../investigations/INV-002-silent-500-alarm/README.md) (the alarm that worked) · [ADR-0029](../adr/0029-ops-hardening.md) (the two original alarms) · the "zero-results run" blind spot, which (b) would also close.

---

## B-6 · Four more unbounded reads share the 1 MB ceiling

**Logged:** 2026-09-01, from [ERR-010](errors.md). **Status:** open, measured, not urgent. Not built.

**What.** [ADR-0036](../adr/0036-gold-filter-rejection-lineage.md) fixed `get_silver_postings` and `get_gold_candidates`. Three unbounded `.all()` reads remain behind the same Data API cap: `get_scored_for_reassess`, `get_scored_shortlist`, and `get_all_scored` — the last powering the v0.10.0 full-list report and selecting wide LLM prose (`strengths`, `gaps`, `strategic_assessment`).

**Measured 2026-08-31 on the live table:** `scored` = **401 rows / 834 kB**, of which **274 kB** is prose. Not at the wall; approaching it. `get_scored_shortlist` and `get_all_scored` are also ~150 lines of duplicated join, so a fix should probably parameterise one builder rather than patch two.

**Why it matters.** The same failure, on a slower fuse, in the path that produces the digest. `get_all_scored` is the one to watch — it grows with every scored posting and never sheds rows.

**So-what.** The column-projection pattern from ADR-0036 transfers directly; the `max_age_days` bound already exists on both and is simply not always passed. The interesting question is whether the shortlist split (currently: pull every scored row, partition by threshold in a Python loop) belongs in SQL.

**Connections:** [ERR-010](errors.md) · [ADR-0036](../adr/0036-gold-filter-rejection-lineage.md) · [ADR-0030](../adr/0030-reachable-full-list-from-digest.md) (the report path).

---

## B-7 · `ALEMBIC_HEAD` lives in three places; the staleness test guards two

**Logged:** 2026-09-01, found while shipping migration 0007. **Status:** open, small. Not built.

**What.** The migration head is written in `handlers/pipeline.py` (`_EXPECTED_MIGRATION_HEAD`), in `terraform/lambda.tf` (the `ALEMBIC_HEAD` env var), and implicitly in `migrations/versions/`. `test_expected_migration_head_matches_migrations_directory` pins the first against the third — but the **env var overrides the constant at runtime**, so a stale `lambda.tf` sails past the unit test and the smoke gate then validates against the wrong expected head. The gate the test protects is bypassed by the value the test does not check.

**Why it matters.** The smoke gate exists to prove the deployed code matches the migrated schema. This is the one way it can pass while being wrong. [The runbook](../runbooks/deploy.md) says to update both by hand — which is exactly the coordination the test was written to stop relying on.

**So-what.** Cheapest fix: extend the existing test to parse `terraform/lambda.tf` and assert its `ALEMBIC_HEAD` equals the real head. Alternative: drop the env var and let the constant be the single source (loses the ability to override without a rebuild — which is why it exists).

**Connections:** [ADR-0029](../adr/0029-ops-hardening.md) (the smoke gate) · [ADR-0036](../adr/0036-gold-filter-rejection-lineage.md) (surfaced it).

> **How this feeds the roadmap:** when the current program closes and P2 reopens, these entries are ranked (leverage = capability ÷ complexity) alongside the [roadmap](../03-roadmap.md) candidates (M2 dedup, M3 Step Functions, near-miss M4, CV tailoring). A graduated entry becomes a labeled release; a rejected one stays here with the reasoning.
