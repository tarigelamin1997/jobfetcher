# SPEC-001 — Autonomous apply for the A-list

**Status:** agreed design, **not built**. Settled with Tarig over eight decision rounds (2026-10-03 → 2026-10-29);
amended 2026-10-30 with D21–D22 (Tarig-only, surge brakes) after the skill-growth discussion ([journal §37](../01-session-decision-journal.md#37-skills-that-grow-jobs-that-move-and-the-databricks-reversal)).
The next step is the read-only [INV-006](#11-build-sequence) investigation; nothing in this spec is implemented
until its unit's brief is approved (crucial tier, both human checkpoints — [agentic workflow](../../.agents/agentic-workflow.md)).

> **What.** Every job that clears the pipeline and scores at or above the threshold is applied to **autonomously**:
> a CV tailored to the job description (JD) is generated, and a local runner submits it on LinkedIn Easy Apply or the
> employer's own careers site. Recruiter-email jobs get a drafted email that Tarig sends himself.
>
> **Why.** JobFetcher finds and scores jobs but stops at the inbox: no CV is tailored (ADR-0006 was never built), and
> applying happens outside the tool, by hand, if at all. Tarig's call is that the tool should finish the job.
>
> **So-what.** This reverses three written positions — the README's *"It does not auto-apply"*, ADR-0006's *"every CV
> is a draft Tarig approves"*, and journal §7's *"official APIs only, no scraping"*. **ADR-0039** records the reversal
> and the rejected alternatives. The safety rails below are the feature, not decoration: every submission is
> irreversible, outward-facing, and made under Tarig's name on the strength of a score that is a triage signal.

---

## 1. Scope

**In scope:** eligibility · CV tailoring (the old M1, [ADR-0006](../adr/0006-cv-renderer.md)) · an apply queue with a
veto window · a local apply runner with LinkedIn Easy Apply and employer-ATS drivers · email *drafts* · digest sections ·
staged rollout per channel.

**Out of scope (decided, not deferred):** sending any email on Tarig's behalf · running a browser in AWS · answering
salary questions automatically · a hosted dashboard.

## 2. Evidence this design rests on

| Fact | Source |
|---|---|
| No CV tailoring exists: no `cv` table, no renderer, no `gold/cvs/` prefix | `src/`, `db/tables.py`, 2026-10-03 |
| The A-list today is only `score >= threshold` (60); nothing records "verified" | [02-architecture](../02-architecture.md) |
| Stored apply links are mostly **aggregators**, not employers. In the 2026-07-08 snapshot, of 49 jobs ≥ 60: Jooble 12, Cosmoquick 8, Jobrapido 6, WhatJobs 6, JobLeads 4, Trabajo 4, BeBee 3; Indeed 2, LinkedIn 1, Naukrigulf 1; **0 employer ATS** | local `export/jobs.sqlite` |
| JSearch marks every probe sample `job_apply_is_direct: false`, with one apply option | `probe_output/*.json` |
| Silver keeps only `job_apply_link`; `apply_options[]`, `job_apply_is_direct`, `employer_website` are dropped but survive in bronze `raw_payload` (recoverable by replay, zero quota) | `core/ingest.py` |
| A catch-up sweep produced **91** gold postings in one run, which went `partial` and sent no digest | [B-20](../ledgers/backlog.md) |
| 17.8% of scored postings were same-fingerprint duplicates | [ADR-0027](../adr/0027-digest-truthfulness.md) |
| Near the cutoff the score drifts (avg spread 15.95 before the resample fix) | [ADR-0031](../adr/0031-boundary-self-consistency-honest-graduations.md) |

The snapshot is three months old. **INV-006 re-measures the channel mix on current data before any channel is built.**

## 3. Decisions

| # | Decision | Settled |
|---|---|---|
| D0 | Apply mode | **Fully autonomous submission.** (Claude recommended assisted apply; Tarig's call.) |
| D1 | Gate | **Score alone** (`>= threshold`, 60). **No overall daily cap.** |
| D2 | Channels | **LinkedIn Easy Apply** + **employer ATS**, autonomous. Tarig accepts the LinkedIn terms-of-service / account risk. **Recruiter-email jobs: draft only — Tarig sends.** |
| D3 | CV | Tailored per job by an LLM, **grounded** in a structured master CV (§5). |
| D4 | CV LLM | **DeepSeek**, seeing the full CV including professional contact details (public on LinkedIn). Real data still never enters this public repo. |
| D5 | Where things run | Daily Lambda: eligibility + queue. **A second Lambda invocation (`apply_prepare`)**: tailoring + drafts, on its own 15-minute budget. **Local runner** on Tarig's machine: all submitting. |
| D6 | Form filling | **Per-platform Playwright drivers**; an LLM only **maps a field label to an answer-bank key** and never writes a value. |
| D7 | Unknown question | **Fail closed** → "Needs you" → Tarig answers once → the answer joins the bank. |
| D8 | Auto-answered kinds | Logistics · diversity/EEO · legal declarations. **Salary is never auto-answered.** |
| D9 | Cover letter | **Only when the form requires one** (grounded, same rules as the CV). |
| D10 | Uncertain submission | **At-most-once**: never retried automatically (§7.3). |
| D11 | ATS accounts | The runner **creates the account and reads the verification email itself** (§7.5). |
| D12 | LinkedIn pacing | Random gaps + **20 per day**; overflow rolls to the next day, never dropped. |
| D13 | Same company | **Apply to every qualifying role** (the fingerprint rule still blocks a repost of the same role). |
| D14 | Rollout | **Shadow → veto → live, per channel**, against a fixed evidence bar (§9). |
| D15 | Veto | A signed **"✕ Don't send"** link per queued job; window **~24 h** (runner submits next morning). |
| D16 | Runner health | A **digest banner** when the runner's heartbeat is > 36 h old. No new alarm ([INV-004](../investigations/INV-004-alarm-escalation/README.md)). |
| D17 | Code visibility | **Public core, private drivers.** The LinkedIn and ATS site drivers live outside this public repo; ADR-0039 says so openly. |
| D18 | Master CV edits | Re-tailor every **not-yet-submitted** job; submitted evidence is never touched. |
| D19 | Small defaults (accepted) | (a) eligibility re-checked at submit time · (b) an auto-submission writes the `applied` outcome itself · (c) evidence kept in private S3 indefinitely, never in logs · (d) tailoring cost recorded per run · (e) "Needs you" grouped by reason · (f) the answer bank lives only on the runner machine. |
| D20 | Runner identity | *Derived from a standing rule, not asked:* machines get their own least-privilege identity ([decisions-locked](../ledgers/decisions-locked.md)), so the runner uses a dedicated **`jobfetcher-runner`** IAM user, never the personal `jobfetcher-dev` key. |
| D21 | Who auto-apply is for | **Tarig only**, even when invited users join ([B-24](../ledgers/backlog.md#b-24--the-tool-has-one-user-built-into-every-layer)). They get scoring, digests and tailored CVs; submitting stays his. He accepted the LinkedIn terms risk for his own account and cannot accept it for anyone else. |
| D22 | Surge brakes | A profile change can push many jobs over the threshold at once ([B-21](../ledgers/backlog.md#b-21--a-job-changes-stage-only-when-someone-remembers-to-run-reassess)), and so can a scorer change that has nothing to do with Tarig. Three brakes (§7.8): **(A)** every queued job records its `cause`, and one Undo dequeues every job that cause queued · **(B)** a run that queues far more than usual gets a longer veto window and a digest banner · **(C)** a job that qualifies with **no** profile change goes to "Needs you", not the queue. |

## 4. Eligibility (evaluated at queue time, and again at submit time)

A job is queued only if **all** hold:

1. Effective score `>= threshold` — `score_override` wins when set (a human correction outranks the LLM).
2. `legitimacy_verified` is true — the scorer's scam gate; Tarig's CV and phone number never go to a suspected scam.
3. Posting age `<= digest_max_age_days`.
4. No `apply_event` exists for this posting, no `application_event` exists for it (already applied by hand), and no
   posting with the **same fingerprint** was submitted (a repost of the same role).
5. Its channel resolved to `linkedin` or `ats:<vendor>` and that channel's mode is not `off`. An `email` channel
   produces a draft instead (§6.3); `unknown` goes to "Needs you".
6. Its `cause` is not `unexplained` (D22-C). A job that crossed the threshold on a re-score while the profile hash
   stayed the same was moved by the model or the prompt, not by Tarig, so it goes to "Needs you".

At submit time the runner re-checks 1–4 against the latest data plus "not vetoed"; a job that fails is `dequeued` with a reason.

## 5. CV tailoring

### 5.1 The master CV
Tarig's master DOCX is converted **once** (`scripts/master_cv.py`) into a structured private YAML: every bullet, skill,
project, role, certification and degree gets a stable `item_id`. Tarig reviews the conversion before first use. The file
is gitignored and mirrored to private S3 alongside the profile; its hash (`master_hash`) is stamped on every tailored CV.

### 5.2 The rule: facts are fixed, wording is free
**Tailoring is re-voicing, not re-facting** (Tarig, 2026-10-04): the same facts, told in the JD's and the company's
language, matching the JD's keyword emphasis.

| Fixed — must trace to a master item | Free |
|---|---|
| skills and tools · employers · past job titles · dates · numbers and metrics · certifications · degrees | phrasing · order of bullets, sections and skills · emphasis · which items appear · the JD's own term for a skill Tarig has |

- **Generalising is allowed; specialising into an absent tool is not.** "Event streaming platforms (Kafka)" ✅;
  "Kafka / Azure Event Hubs" ❌ when Event Hubs is not in the master. Inventing a use case ("…for fraud analytics") ❌.
  Changing a number (40% → ~50%) ❌.
- **Headline** always mirrors the JD's title, verbatim. It is the only text exempt from the fact check; past job titles
  under Experience never change.
- **Skills marked `learning`** (in the sanitized sample profile: Spark, PySpark, Delta Lake) appear honestly: in a "Currently developing" line or
  tied to the project where they were used — never in the core-skills line.
- **Company voice** comes from the JD text plus the silver-dissected `sector`. No website fetch.
- **Format:** Compact, 1–2 pages (the tarig-cv Suleiman protocol: no summary, XYZ bullets, Skills before Experience).

### 5.3 The grounding check (deterministic, code — not the LLM)
Every tailored bullet carries the `item_id` it rewrites. For each bullet, the check extracts the fact tokens — numbers,
dates, employer names, title words, and any term in the canonical skill vocabulary (master skills + the silver skill
canon + a synonym map: "Postgres" ≡ "PostgreSQL") — and requires each to appear in the cited master item or the
master's skill list. A failing bullet is **replaced by its master bullet** and counted as `dropped_rewrites`. Same
pattern as the silver dissection's `grounding_check` (`core/dissector.py`).

**Measured promise:** every JD-requested skill that Tarig has appears in the CV under the JD's own term. The check
reports coverage before and after tailoring (the scorer's `ats_keyword_coverage` factor, made explicit).

### 5.4 Master edits
A changed `master_hash` re-tailors every job that is queued but not yet submitted (D18).

## 6. Channels

### 6.1 Destination resolution
`apply_prepare` recovers `apply_options[]`, `job_apply_is_direct` and `employer_website` from bronze and classifies the
job as `linkedin`, `ats:<vendor>` (by URL host — Workday, SuccessFactors, Taleo/Oracle, Greenhouse, Lever, …), `email`
(an application address explicitly stated in the JD), or `unknown`. Following an aggregator to its final URL may need
a browser; **where that runs is an open item for INV-006** (§12).

### 6.2 LinkedIn Easy Apply and employer ATS (autonomous)
Submitted by the local runner (§7). Each driver implements one interface: `open(job) → fill(form, answers, cv) →
confirm() → evidence`. The public repo carries the interface and a fake driver; the real site drivers are private (D17).

### 6.3 Recruiter email (draft only)
`apply_prepare` writes a grounded subject + body and attaches the tailored CV; the digest lists it under "Needs you —
email drafts". **Nothing is ever sent by the system.**

## 7. The local runner

### 7.1 Schedule and veto window
| When (UTC) | What |
|---|---|
| Day D 06:00 | Daily run: … score → eligibility → `queued` rows → digest D lists them, each with **✕ Don't send** |
| Day D ~07:00 | `apply_prepare` invocation: destination, tailored CV, drafts → `prepared` |
| Day D+1 ~05:30 (08:30 Riyadh) | Runner submits D's `prepared`, not-vetoed jobs (~23.5 h veto window) |
| Day D+1 06:00 | Digest D+1 reports what was submitted, with evidence links |

A laptop that is off delays the run; queued jobs never expire silently — they wait, and the heartbeat banner (D16) says so.

### 7.2 Modes, per channel
`off` · `shadow` (fill every field, screenshot, **never click Submit** → `shadow_filled`) · `veto` (submit after the
window) · `live` (submit at the next runner pass; the veto link still works until then). Every channel ships in `shadow`.

### 7.3 At-most-once submission
`submitting` is written **before** the click; `submitted` only after the platform confirms (a confirmation page, or
LinkedIn's "Applied" badge). A crash or timeout in between → `unknown` → "Needs you", with the screenshot. **Never
retried automatically** — a missed application is recoverable, a duplicate is not. Before starting, a visible
"already applied" sign skips the job. *(This is the deliberate inverse of the digest's at-least-once send.)*

### 7.4 Answer bank and field mapping
A private YAML on the runner machine only (D19f), grouped by kind: `logistics` (notice period, start date, relocation,
location, years per skill, work authorisation), `eeo`, `legal`, `salary`. The mapper LLM sees a field's label and
options plus the list of bank **keys** — never values — and returns a key or `NONE`. The value comes from the bank;
for a select field it must match an option exactly. `NONE`, a mismatch, or any `salary` key → `needs_you` with the
question shown. A cover-letter field is filled only when required (D9).

### 7.5 ATS accounts and inbox access
For an employer with no account yet, the runner creates one with Tarig's professional email and a generated password
stored in the local OS keychain (Windows Credential Manager), then reads the verification email and follows the link.
Inbox access is restricted to messages from the ATS sender and links on that employer's ATS domain. **The Gmail
mechanism is an open item (§12)**: OAuth read-only is least-privilege but a personal app in Google's "Testing" status
loses its refresh token every 7 days; an IMAP app password lasts but opens the whole mailbox.

### 7.6 LinkedIn session and pacing
A dedicated Playwright persistent profile, logged in by Tarig, on his own IP. Random 2–6 minute gaps; at most 20
submissions a day; overflow rolls to the next day.

### 7.7 Health
Every run writes a heartbeat (`runner/{date}/heartbeat.json`); the daily digest opens with "⚠ The apply runner hasn't
run since {date} — N applications waiting" when it is older than 36 h, through the same banner path and order as the
B-12 intake and INV-005 credit alerts.

### 7.8 Surges (D22)
A per-job veto does not survive a surge: nobody clicks "✕ Don't send" forty times in a day. So:
- **(A) Undo by cause.** Every `queued` row carries its `cause`: `new_posting` · `profile_change:<hash>` ·
  `promotion:<id>` (a skill promotion, [B-23](../ledgers/backlog.md#b-23--proof-of-a-skill-has-nowhere-to-go)) ·
  `unexplained`. The digest groups queued jobs by cause, and one signed **"✕ Undo this change"** link dequeues every
  not-yet-submitted job with that cause (and, for a promotion, reverts it).
- **(B) Longer window on a surge.** When one run queues more than a multiple of the usual daily count, that batch's
  veto window grows (~72 h instead of ~24 h) and the digest leads with a banner. Nothing is dropped and nothing needs
  approval. **The threshold is measured by INV-006, not guessed.**
- **(C) Unexplained moves are held.** See §4 rule 6.

Per-company spreading was considered and rejected (§16); D13 stands.

## 8. Digest changes

New sections, after the existing ones: **Queued — sends tomorrow morning** (✕ Don't send) · **Submitted since last
digest** (evidence link; replaces "Mark applied" for these cards) · **Needs you** grouped by reason (salary · unknown
question · unconfirmed submission · account step failed · email draft · unknown channel) · in shadow,
**Would have submitted**. The full-list report gains the apply state per job.

## 9. Rollout and cut-over (per channel, Tarig flips each switch)

| Phase | Exit bar |
|---|---|
| Shadow | ≥ 2 weeks **and** ≥ 20 reviewed "would have submitted" with 0 Tarig would have vetoed **and** ≥ 10 complete form-fills with 0 wrong answers |
| Veto | ≥ 2 weeks with fewer than 1 veto per 10 submissions |
| Live | — |

The numbers at each switch are recorded in the CHANGELOG. A per-channel kill switch is a config push (§10), no redeploy.

## 10. Configuration (new required `auto_apply` block in the SearchSpec)

```yaml
auto_apply:
  channels: { linkedin: shadow, ats: shadow }   # off | shadow | veto | live
  veto_window_hours: 20
  linkedin_daily_max: 20
  linkedin_gap_seconds: [120, 360]
  runner_stale_hours: 36
```

Every field is required (the fail-loud contract). **Push config before deploying the unit that reads it**
([procedure registry](../ledgers/procedure-registry.md)).

## 11. Build sequence

One squad run per unit, one open PR at a time, crucial tier throughout.

| Unit | Delivers |
|---|---|
| **U0 · INV-006** (read-only) | Current channel mix of the ≥ 60 set (LinkedIn / ATS by vendor / email in JD / unknown), the bronze `apply_options` reality, aggregator → final-URL behaviour on a sample, volume per sweep, the share of forms asking salary (from a manual sample), Tarig's minutes-per-application baseline. **Added 2026-10-30:** the usual queued-per-run count (the D22-B surge baseline), and the share of reassess graduations already past `digest_max_age_days`. May drop a channel. |
| **U1 · ADR-0039** + this spec | The reversal, on the record. |
| **U2 · Queue** | `core/apply.py` eligibility + `apply_event` (migration 0008) + digest "Would be queued" + the veto token kind. No CVs yet. |
| **U3 · Tailoring** | `scripts/master_cv.py`, `core/cv_tailor.py` (+ grounding), `cv` table, the `apply_prepare` invocation (infra), email drafts. |
| **U4 · Runner core** (public) | Queue reader, state machine (§7.3), answer bank + mapper, evidence, heartbeat, fake driver, `jobfetcher-runner` IAM user (infra). |
| **U5 · LinkedIn driver** (private) | Shadow first. |
| **U6 · ATS driver(s) + inbox** (private) | Only the vendors INV-006 shows matter. |

## 12. Open technical items (resolved with evidence inside the units, not by Tarig)

- Gmail access for verification emails: OAuth read-only vs IMAP app password (§7.5).
- Where aggregator → final-URL resolution runs: Lambda (if plain redirects) or the runner (if a JavaScript button).
- PDF rendering: the runner's Chromium print (default) vs a Lambda-side renderer for email drafts.
- How `apply_prepare` is triggered: a second EventBridge rule vs a self-invoke from the daily run.

## 13. Data model (migration 0008, additive)

- **`cv`** — `cluster_id`, `content_s3_key`, `docx_s3_key`, `pdf_s3_key`, `master_hash`, `dropped_rewrites`,
  `model`, `tailored_at`.
- **`apply_event`** — append-only, latest wins: `posting_id`, `state`, `channel`, `mode`, `reason`, `cause`
  (D22, §7.8), `evidence_s3_key`, `run_id`, `noted_at`. States: `queued` · `prepared` · `vetoed` · `dequeued` · `needs_you` ·
  `email_draft` · `shadow_filled` · `submitting` · `submitted` · `unknown` · `failed`. The CHECK is built from one
  `APPLY_STATES` tuple in `core/models.py`. Inserts are `.inline()` with no `RETURNING`; bulk reads go through
  `_read_paginated` (the Data API's 1 MB cap — [ERR-010](../ledgers/errors.md)).
- **Capture token** gains an optional `act` claim: absent = `outcome` (every existing "Mark applied" link stays valid),
  `veto` writes an `apply_event` instead of an `application_event`, and `undo` (D22-A) is scoped to one `cause`
  instead of one posting. Verification still precedes any database touch
  ([ADR-0035](../adr/0035-outcome-capture-endpoint.md)).

## 14. Security and privacy

- The public capture endpoint gains the veto action — security-reviewed like ADR-0035.
- New credentials, all off the repo: Gmail read access and ATS passwords on the runner machine only; the LinkedIn
  session in a dedicated browser profile; `jobfetcher-runner` IAM keys scoped to the Data API, the `applications/`
  S3 prefix and the DeepSeek secret.
- Evidence (screenshots, values sent, the exact CV) in private S3 `applications/{posting_id}/`, never in logs.
- The master CV, answer bank and contact details are gitignored; the repo ships sanitized samples only.

## 15. Validation gates (behavioural, each with a negative; every test must fail when its guard is removed)

| Gate | Positive | Negative |
|---|---|---|
| Eligibility | a qualifying job is queued | below threshold · not legitimate · too old · already applied · same fingerprint submitted · channel `off` → **no row** (one test each) |
| Grounding | a JD-voiced bullet with master facts survives | an invented tool, number, employer or use case → master bullet used, `dropped_rewrites` incremented |
| Learning skills | shown under "Currently developing" | never in the core-skills line |
| Shadow | form filled + screenshot | **Submit is never clicked** (driver test against a fake page) |
| At-most-once | confirmed → `submitted` | crash after click → `unknown`, no retry; "already applied" sign → skipped |
| Mapper | a mapped key fills the bank value | `NONE`, an option mismatch, or a salary field → `needs_you`, nothing invented |
| Veto | valid token → `vetoed`, runner skips it | forged or expired token → 400, 0 rows; an old outcome link still works |
| Undo by cause | one valid undo → every unsubmitted job with that cause dequeued | jobs with another cause untouched; a submitted job untouched; forged token → 400, 0 rows |
| Unexplained | a crossing with a new profile hash → queued | a crossing under an unchanged hash → `needs_you`, no queue row |
| Surge window | a run above the measured baseline → longer window + banner | a normal-sized run → standard window, no banner |
| Email | draft appears in "Needs you" | no code path sends mail |
| Heartbeat | fresh heartbeat → no banner | > 36 h → banner names the date and the waiting count |
| Data API | migration 0008 applied; smoke `200 @ 0008` | new bulk reads paginated (no unbounded select) |

## 16. Rejected alternatives (to be carried into ADR-0039)

Assisted apply with a human Submit (recommended by Claude, declined) · a human approve click per job · sending
recruiter emails automatically (*"defies the purpose of sending emails, which should be tailored and personal"*) ·
stripping contact details before the LLM (declined: the details are public) · browser automation in AWS (flagged IPs,
infra) · a general-purpose LLM browser agent (unpredictable; cannot prove it never invents an answer) · tailoring inside
the daily run (heavy days go partial and lose the digest, B-20) · tailoring in the runner (no CV before the veto
closes) · all code public (publishes a terms-violating LinkedIn driver in a portfolio repo). **Added 2026-10-30:**
a per-job veto as the only brake on a surge (unusable at volume) · per-company spreading of a surge (declined; D13
stands) · offering auto-apply to invited users (D21).
