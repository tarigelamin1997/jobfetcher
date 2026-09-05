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

> **Investigated 2026-09-04 → [INV-004](../investigations/INV-004-alarm-escalation/README.md) (`handoff-ready`).** An audit of all three alarms produced the number that settles it: `returned-500` fired **29 times** during the 38-day outage, to a confirmed email subscription, and was ignored every time. The dead-man has **never** fired and is therefore untested. CloudWatch cannot express "failing N days running" at all (24h evaluation ceiling), so escalation has to come from the pipeline reading `run_log.digest_sent_at` — which `get_last_digest_sent_at` already exposes. **The dossier also sequences [B-12](#b-12--a-run-that-fetches-nothing-reports-success--no-log-no-counter-no-alarm-️-live) behind this one:** adding a fourth alarm to an inbox that ignored 29 makes things worse, not better.

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

---

## B-8 · The tree is 262 lint errors behind a modern ruff (unpinned tool drift)

**Logged:** 2026-09-01, when PR #36's CI went red for reasons unrelated to PR #36. **Status:** open, contained. Not built.

**What.** `pyproject.toml` floored `ruff>=0.4` while `.pre-commit-config.yaml` pinned `v0.6.9`, so CI linted with whatever shipped most recently. Ruff **0.16.5** broadened its *default* rule set — `UP037`/`UP007`/`UP017`, `I001`, `RUF100`, `BLE001`, `DTZ011`, `TRY004`, `EXE001` — and a green `main` became **262 errors with no code change**. Measured on the day: main 262, PR branch 272 (so ~10 attributable to the branch, the rest inherited).

**Fixed for now by pinning** `ruff==0.6.9` to match the pre-commit hook, so local and CI cannot disagree. That closes the reproducibility hole; it does **not** address the 262.

**Why it matters.** Two things, and the second is the interesting one. (a) `terraform/providers.tf` states *"reproducibility is the portfolio value"* — and the providers are pinned via `.terraform.lock.hcl` while the Python toolchain was not, which is the sharpest internal inconsistency in the repo. (b) Most of the 262 are genuinely worth fixing: `I001` import sorting, `UP007`/`UP037` modern annotations, `DTZ011` naive `date.today()`, and — pointedly — **`BLE001` blind `except Exception`**, which the codebase already annotates with `# noqa: BLE001` in ~13 places *believing the rule was enforced*. It never was: with only the old default `E4/E7/E9/F` enabled, every one of those `noqa` comments is decorative, and the newer ruff flags them as `RUF100` unused. The project has been writing suppressions for a rule it does not run.

**So-what.** Pick a target ruff, adopt an explicit `[tool.ruff.lint] select` (the real fix — never rely on a linter's *defaults*, which are a moving target by design), run `--fix` for the mechanical 245, and hand-review the rest. Pair it with the long-deferred `ruff format` decision ([procedure-registry](procedure-registry.md) still carries it as `Deferred` with no owning stage, which that ledger's own invariant calls a bug). No dependency lockfile exists either — same root cause, bigger scope.

**Connections:** [ERR-010](errors.md) / [ERR-011](errors.md) (found during the same close-out) · `.pre-commit-config.yaml` · [procedure-registry](procedure-registry.md) (`ruff-format` deferred).

---

## B-9 · A retrying LLM call can still outlive the deadline guard

**Logged:** 2026-09-01, while sizing the scoring timeout ([ERR-013](errors.md)). **Status:** open, bounded, not urgent. Not built.

**What.** The H-2 deadline guard checks `deadline.expired` **before starting** a call, never during one, so a call begun a moment before the wall runs its full timeout past it. `_DEADLINE_MARGIN_S` is now 200 s to cover a single 180 s scoring call — but the transport retries transients up to `max_retries=3`, so the true worst case is **4 x 180 s plus backoff ≈ 12 minutes** past the wall. Against a 900 s Lambda that is a hard timeout instead of the clean `partial` return H-2 exists to guarantee.

**Why it matters (mildly).** It needs a genuinely transient failure (429/5xx/connection) on a call that starts in the last moments of a run — rare, and the consequence is a lost run rather than lost data, since every stage is idempotent and the `run_log` blocks a double digest. But it silently defeats the one guarantee the deadline guard makes, so it should not stay unnamed.

**So-what.** (a) Give the *retry loop* a deadline, not just the task: pass the `Deadline` into `OpenAICompatLlmClient` and stop retrying once expired — the smallest honest fix. (b) Derive `_DEADLINE_MARGIN_S` from `(max_retries + 1) * timeout_s` instead of hand-tuning it, so the two can never drift apart again. (c) Cap total in-flight time per posting rather than per attempt. (a) is probably right; (b) is a good companion because the coupling is exactly what ERR-013 showed people get wrong.

**Connections:** [ERR-013](errors.md) (raised the timeout that exposed this) · [ADR-0021](../adr/0021-m1-pipeline-hardening.md) (H-2, the deadline guard) · [ADR-0037](../adr/0037-per-task-reasoning-budgets.md) (reasoning effort and timeout are coupled).

---

## B-10 · The real running cost is DeepSeek, not AWS — and it is unmeasured per run 💰

**Logged:** 2026-09-02, from the ERR-010 backlog drain. **Status:** open — figures established, no instrumentation built. Requested by Tarig as a thing to be *aware of*, not to build yet.

**What.** The architecture's cost story has always been about AWS ("Aurora scale-to-0 ⇒ ~$0 idle"). That framing is now wrong in the way that matters: **AWS is the cheap part.** The binding constraint on whether this tool runs tomorrow is the DeepSeek balance, and nothing in the pipeline measures or reports it.

**Measured 2026-09-02** (balance read via `GET https://api.deepseek.com/user/balance` before and after a single run):

| | |
|---|---|
| Postings scored | **618** |
| Balance before → after (settled) | **$9.71 → $0.14** |
| **Cost** | **$9.57** |
| **Per posting** | **$0.0155** |
| Wall clock | ~10 min at 80 workers |

**Extrapolated to the daily run** (10–30 new postings): **$0.15–$0.46/day ⇒ roughly $5–14/month.**

> ⚠️ **Billing settles late, and the first reading lied.** Immediately after the run the balance
> read **$1.48**, giving $8.23 / $0.0133 per posting. Re-read ~30 minutes later it was **$0.14** —
> another **$1.34**, a **16% understatement**. So `GET /user/balance` is authoritative *eventually*,
> not immediately. Any measurement or guard built on it must allow for settlement lag, and a
> threshold alarm that reads it right after a run will read high. The figures above are the settled
> ones. Against an AWS bill that is near zero at idle, DeepSeek is essentially the entire running cost of the product.

**What drives it.** `reasoning_effort=high` on the scorer ([ADR-0037](../adr/0037-per-task-reasoning-budgets.md)). Measured token split per score: ~1,000–2,000 **reasoning** tokens against only ~270–340 tokens of actual answer — so **80–85% of scoring spend buys thinking that is discarded.** Dropping to `low` measured 886 reasoning tokens vs high's ~1,850, so it would roughly halve the bill. That is a real quality-vs-cost decision (and it would invalidate ADR-0031's calibration), not a free win — which is exactly why it belongs in a decision, not a tweak.

Multiply by the [ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md) boundary resample: a posting near the threshold is scored **3×**, so it costs ~$0.04 rather than $0.013. Roughly 1 in 5 postings qualify.

**Why it matters.** Two failure modes, both seen today: (a) an empty balance stops the tool dead — 402 on every call, which is how the 38-day outage compounded; (b) a *low* balance silently throttles it, because DeepSeek scales concurrency with balance ([ERR-014](errors.md)) — 39 concurrent at ~empty, ≥200 at $10. So the balance is not just a bill, it is a **capacity input**, and it is invisible to every gate the project has.

**So-what (candidates — none chosen).** (a) Report `usage` totals per run in the run summary and the S3 audit — the API already returns prompt/completion/reasoning counts per call, so this is accumulation, not new data. (b) A balance check in the `{"mode":"smoke"}` gate, failing or warning under a threshold — the deploy gate already exists and this is one HTTP call. (c) A CloudWatch alarm on a published balance metric — closes the "empty account" failure class properly, and is the only option that catches it *before* a run dies. (d) Reconsider `reasoning_effort` with the cost visible.

**Connections:** [ERR-011](errors.md) / [ERR-014](errors.md) (both were balance failures wearing other costumes) · [ADR-0037](../adr/0037-per-task-reasoning-budgets.md) (the effort setting that drives the cost) · [ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md) (the 3× resample multiplier) · [B-9](#) (the retry tail also spends).

> **How this feeds the roadmap:** when the current program closes and P2 reopens, these entries are ranked (leverage = capability ÷ complexity) alongside the [roadmap](../03-roadmap.md) candidates (M2 dedup, M3 Step Functions, near-miss M4, CV tailoring). A graduated entry becomes a labeled release; a rejected one stays here with the reasoning.

---

## B-11 · The tree is 84 files from `ruff format` clean, and the gate that claimed to check it never ran

**Logged:** 2026-09-03, during the documentation-debt clearing ([ERR-016](errors.md)). **Status:** open, contained, **documented honestly**. Not built — this is a code change and was deliberately left out of a docs-only unit.

**What.** `.claude/commands/review-step.md` Check 1 stated that a build unit cannot close until *"`ruff check` and `ruff format --check` clean on the changed Python."* CI (`.github/workflows/ci.yml`) has only ever run `ruff check .`. Measured 2026-09-03:

```
$ python -m ruff format --check .
84 files would be reformatted, 7 files already formatted
```

So the documented gate **would hard-fail on any unit that actually ran it** — which is how it survived: nobody ran it.

**Why it matters, and it is not the formatting.** This is the third instance in two sessions of the same defect, and the pattern is the finding:

| | Mechanism | Why it was decorative |
|---|---|---|
| [ERR-015](errors.md) | branch protection requiring a PR | `enforce_admins` was `false` — it exempted its only user |
| **B-8** (above) | ~13 `# noqa: BLE001` suppressions | `BLE001` was never enabled — the suppressions suppressed nothing |
| **B-11** | `/review-step` Check 1 | `ruff format --check` was never wired into anything, and could not have passed |

Each looked like enforcement and was ornament. The project's second pillar says *a standard not wired into a command is a suggestion*; these say the harder half — **a standard can be wired and still be a suggestion if it is never armed, or armed against nobody.**

**Fixed for now by telling the truth.** Check 1 now describes what is actually enforced (`ruff check`, with `ruff` pinned to `0.6.9`) and states plainly that `ruff format --check` is not run. That closes the honesty gap; it does **not** close the 84 files.

**The remaining work (a code unit, not a docs one).** Either (a) run `ruff format` once across the tree, absorb the one-time diff, and add `ruff format --check` to CI so it stays clean — best done in the same unit as B-8's 262 lint errors, since both are one formatting-shaped diff against a pinned tool; or (b) decide formatting is not enforced here and delete the aspiration entirely. **Option (a) is recommended**, with the caveat that the diff will touch nearly every file and should therefore land alone, on its own PR, with no behavioural change mixed in.

**Owning stage:** the next code/tooling unit — alongside **B-8** (same tool, same pin, same class of debt). Explicitly *not* the documentation unit that logged it: reformatting 84 files is a code change, and the docs-only scope was set deliberately.

---

## B-12 · A run that fetches nothing reports success — no log, no counter, no alarm ⚠️ **LIVE**

**Logged:** 2026-09-04, from a live-stack review during the [ERR-016](errors.md) documentation audit — **not** reported by any alarm. **Status:** **partially closed 2026-09-04** — the blindness and the capacity mismatch are fixed ([ERR-017](errors.md), PRs #63/#64, config live); **what remains open is ANNOUNCEMENT.** Investigated: **[INV-003](../investigations/INV-003-silent-fetch-stop/README.md)** (`fixed`).

**What still isn't solved.** A `200` with `fetched: 0` is now *diagnosable* — `fetch_stopped` says `rate_limited` / `budget_exhausted` / `not_a_fetch_day` in the durable run summary — but it is still not *announced*. No alarm covers "green but empty", so it takes someone looking. Rung 3 of the dossier (a `PIPELINE_ALARM`-style marker reusing INV-002's metric-filter wiring, or saying it in the digest) is deferred because it is live infra and must be **proven by making it fire**, not asserted. Same shape as **B-5**.

**What.** Since 2026-09-02 the daily pipeline has returned `statusCode: 200` with `fetched: 0` on **three consecutive days**, no `raw/` object has landed since 2026-09-01, and all three CloudWatch alarms sit in `OK`. A digest still goes out each morning carrying 257 previously-scored jobs, so the tool looks alive from the inbox.

**Why it matters.** Two faults, and separating them is the point:

1. **The trigger** — JSearch returning HTTP 429 (quota / subscription / rate-limit). Fixed outside the codebase.
2. **The blindness** — a run cut off at the first request is byte-for-byte identical to a run with nothing new to fetch. `adapters/jsearch_source.py:190` handles the 429 with a **bare `return`**: no log, no counter, no signal. It sits **four lines below** a comment insisting a broken credential must *"FAIL LOUDLY, else a rotated key turns into a silent zero-count 'success'"* — the author guarded that exact failure mode for auth and left it open for quota.

Only #2 is engineering work, and only #2 stops the next occurrence. **Fixing the quota alone leaves the trap armed.** The prior "nobody noticed" ([ERR-010](errors.md)) ran 38 days *while firing an alarm every morning*; this one fires nothing at all.

**Not the cause** (checked, not assumed): the request budget (25 vs 18 — cannot fire first) · a config change (`search_config.yml` unchanged since 2026-07-08) · dedup (`already: 0`) · the 429 *stop behaviour*, which is correct and covered by a passing test. Only its silence is the defect.

**Next.** Rungs, gate and blast radius are in the dossier. Recommended stopping point is **rungs 1–2** (`src/`-only: log the stop, record the reason in the run summary) — non-crucial, no infra. **Rung 3** (making a zero-fetch day *noticed* rather than merely findable) touches `terraform/alarms.tf` and is Tarig's call. Related to but distinct from **B-5**: this is a failure with no signal; B-5 is a signal nobody escalates.

## B-13 · Two cadence knobs an operator can reach but not keep

**Logged:** 2026-09-05, from a fresh Examiner pass on PR #64 (see [CHANGELOG](../../CHANGELOG.md) "what a fresh adversarial Examiner found"). **Status:** open — **not** in PR #70's blast radius, deliberately.

**What.** `terraform/lambda.tf` lists `LOG_LEVEL` and `PIPELINE_MAX_WORKERS` in the Lambda's `environment.variables` **specifically so the knobs are IaC-visible** — the file says so in a comment. `JOBFETCHER_FETCH_EVERY_N_DAYS` is not listed. Because Terraform manages `environment.variables` as a whole map, an operator who sets the override in the Lambda console — the path [INV-003](../investigations/INV-003-silent-fetch-stop/README.md) advertises as "overrides the cadence without a redeploy" — has it **silently removed by the next `terraform apply`**. The documented escape hatch works exactly until the next deploy, then stops, with nothing announcing it.

**Why it matters.** Not urgent (the default is correct and the quota maths depends on it staying 3), but it is a documented capability that quietly does not hold, and the failure is invisible: the cadence reverts to 3 and the run summary says `not_a_fetch_day`, which looks entirely normal.

**Next.** One line in `terraform/lambda.tf`, with the comment the other two carry. **Terraform is Tarig's call** — proposed, not applied.

## B-14 · No way to force a sweep on an off-day

**Logged:** 2026-09-05, same Examiner pass. **Status:** open — **declined for PR #70 as a feature, not a defect** (P1).

**What.** The cadence reads `run_date` + the env knob only. An operator wanting to spend the ~50 spare monthly requests on an off-day must either edit Lambda config (see **B-13**) or fake `run_date` — which doubles as the `run_log` send-once key and the report's S3 prefix, so a faked date writes `mark_digest_sent` against a day the run did not happen on. An `event['force_fetch']` boolean would be ~2 lines.

**Why it matters — and why it waited.** It is a convenience with no observed bottleneck behind it: nobody has yet needed an off-day sweep. Recorded so the option is not rediscovered from scratch, per P2 — a candidate, not a commitment.
