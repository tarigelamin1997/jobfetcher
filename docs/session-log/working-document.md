# Working Document — verbatim design + build reasoning notes

> **What this is.** The raw, unedited working document behind JobFetcher's design and build — the live reasoning notes captured *as decisions were made*, preserved here in full for complete context. It is the granular source that the curated docs distill:
> - **Curated narrative:** [`../01-session-decision-journal.md`](../01-session-decision-journal.md) — Part 1 = design session (§1–11), Part 2 = build phase (§12–20, incl. the LLM-live milestone).
> - **Formal decisions:** [`../adr/`](../adr/) · **live state:** [`../ledgers/`](../ledgers/) · **release log:** [`../../CHANGELOG.md`](../../CHANGELOG.md).
>
> **Read it for the *why behind the why*.** It includes everything: the prior (scrapped) project that was absorbed (§1), the full ~150-question discovery intake (§2), the section-by-section captured understanding (§3), the architecture synthesis and its pivots (§4–§8), the governing principles (§9), the status checkpoints (§10), the methodology adoption (§11), and every build-phase capture (§12–§29) — each with its original *Context / Decision / Edits / Verification* working-notes.
>
> **Fidelity over polish (intentional).** Working-process meta is left in *verbatim* — plan-mode markers, per-capture "Edits (out of plan mode)" and "Verification" checklists, `✅ DONE (commit …)` stamps, and the occasional decision reversed in place (silver `lingua`→LLM in §27; Bedrock→DeepSeek in §28, verified live in §29). That mess **is** the context; nothing was cleaned up or removed. This is "documentation as infrastructure" taken literally: the repo is the memory.
>
> **Status.** A point-in-time snapshot of a *living* working doc (re-synced through **§29 — the LLM-live milestone**). For *current* authoritative state, the numbered docs + ADRs + ledgers win; this file is the historical reasoning record.
>
> *(Section numbers `§n` referenced throughout the curated docs point at the headings below.)*

---

# JobFetcher — Fresh-Start Discovery

## Context
Tarig wants to delete the existing JobFetcher planning artifacts (CLAUDE.md rules, the 8-phase Notion plan, the standards docs) and **start fresh, building "in a better way."** Before any rebuild, the goal of this session is *understanding*: I (Claude) have absorbed all existing knowledge, and now I must rigorously interrogate Tarig (100–300 questions) so the new design is grounded in his real intent — not inherited assumptions. The output of this phase is captured understanding, which will later be reflected into clean docs/documentation and a new implementation plan.

**This file is a working document, not yet an implementation plan.** It holds: (1) what I absorbed, (2) the question set, (3) a placeholder for answers/decisions.

---

## 1. What I Absorbed (correct me where this is wrong or stale)

**The product.** JobFetcher = a fully-serverless AWS pipeline that automates the job search: discover → deduplicate → score → tailor CV → notify, on a daily EventBridge cron. Dual purpose: (a) a working personal tool for Tarig's Data Engineering job search in KSA/GCC, and (b) a cloud-native portfolio piece proving production AWS/DE skills to employers.

**The pipeline.** EventBridge → Step Functions (Fetch → Bronze dedup → Silver score → Gold CV → Notify) → 7 Lambdas (job_fetcher, bronze_processor, job_scorer, cv_tailor, notifier, stream_processor, sector_intelligence). Bedrock (Claude Sonnet) does 7-factor ATS scoring and CV tailoring. CVs: structured JSON → python-docx DOCX → LibreOffice-layer PDF, deterministic template (the "tarig-cv" spec). Medallion (bronze/silver/gold) on S3. DynamoDB (jobs + profile) with Streams → stream_processor does CDC-style cross-table sync. Notion is the only UI (4 DBs: Live Status Tracker, Near Miss/Gap Tracker, Skill Demand Tracker, Sector Intelligence Reports). SES email digest + graduation alerts. CloudWatch dashboard/6 alarms/5 SLOs. Terraform (10 modules, S3 backend). GitHub Actions CI/CD. Secrets Manager (3 secrets, IAM-scoped). Region eu-north-1.

**Signature mechanics.** 3-layer dedup (exact ID → fingerprint → Bedrock fuzzy). Near-miss jobs (floor 50 → threshold 70, near-miss band 10) re-scored daily; graduate to main tracker when a closed skill gap pushes them over threshold. Skill Demand Tracker (ROI priority, "Roles Blocked", "You Have It"). Weekly Sector Intelligence (skill stack, pain points, current solutions, improvement opportunities, suggested portfolio projects, interview prep). Centralized feedback hub (separate account). Multi-user vision via a `user_id` dimension. Semver + setup.sh/migrate.sh + clean `terraform destroy = $0`.

**The candidate (profile source of truth).** Tarig Elamin, Riyadh; ~4 yrs; targets Data Engineer / Data Platform Engineer / Data Architect; AWS SAA-C03 / CLF-C02 / AIF-C01; core Python/SQL/Kafka/ClickHouse/Debezium/dbt/Airflow/PostgreSQL/MongoDB; cloud AWS/Terraform/Docker/K8s(Kind); learning Spark/PySpark + Delta Lake. Projects: Barakah CDC Challenge, OrderFlow (mega DE), JobFetcher. Open to GCC relocation; on-site preferred, remote OK.

**The portfolio assessment (the lavish HTML).** Verdict: strong base (85%), worth building. Primary gap = SQL / warehouse modeling (dbt). Recommendation: keep cloud-serverless as the differentiator, add a **Snowflake + dbt** analytics track off the S3 silver/bronze layer; cloud for demos, local only for dev/tests. Three scope forks offered: MVP Portfolio (Phases 1–3 + Snowflake/dbt, 4–6 wk), Full Portfolio (all 8 phases, 8–12 wk), DE Maximum (Phases 1-2-3-5-6 + deep Snowflake/dbt, skip CV/LibreOffice).

**The old execution discipline (CLAUDE.md).** Notion is source of truth; build strictly phase-by-phase; mandatory Correlation ID + Pre-condition Guards in every Lambda; living Execution Logs in Notion; 800-line write-block hook; "Claude executes, does not make architecture decisions"; stop after each phase for Tarig's confirmation.

---

## 2. The Question Set

> Answer in any format, any order. Skip any that don't matter. Write "you decide / recommend" and I'll propose a default with reasoning. Reference by ID (e.g. "F3: …"). Sub-bullets are facets of one question — a one-line answer is fine.

### A — Goals, success & what "better" means
- A1. In one sentence: what is the rebuild *for*?
- A2. Rank these by importance to you right now: (i) land a job fast, (ii) impress employers with the codebase, (iii) learn AWS/DE deeply by building, (iv) have a tool you actually use daily.
- A3. What specifically frustrated or disappointed you about the previous design/plan? What made you want to scrap it?
- A4. What does "better" mean to you concretely — simpler? more DE depth? more reliable? cheaper? faster to build? more impressive? something else?
- A5. How will you know, 3 months from now, that the rebuild succeeded? What's the observable outcome?
- A6. What parts of the old plan do you want to *keep almost as-is* because they were genuinely good?
- A7. What parts do you already suspect were over-engineered or pointless for one user?
- A8. Is there a hard deadline driving this (a job offer running out, credits expiring, an interview, a self-imposed date)?
- A9. How much of this do you want to build yourself vs. have me build while you review?
- A10. Do you want this rebuild to *reuse* any code/CVs/configs from before, or truly greenfield?

### B — Your real job-search situation (the actual need behind the tool)
- B1. Are you actively job-hunting right now, passively monitoring, or currently employed and not looking?
- B2. How urgent is landing a role — weeks, a few months, "whenever the right thing appears"?
- B3. Roughly how many roles do you apply to per week today, and how do you find them now?
- B4. What's the single most painful/time-wasting part of your current search?
- B5. Of {discovery, filtering/scoring, CV tailoring, tracking, market intel} — which would actually save you the most time if automated?
- B6. Do you genuinely want auto-generated tailored CVs, or would you rather tailor manually and just have great scoring + a shortlist?
- B7. Would a daily email + a simple list be enough, or do you want the full Notion workspace experience?
- B8. How much do you trust an LLM's match score? Would you act on it, or just use it to triage?
- B9. Have you ever applied to a job that JobFetcher (or your manual process) scored, and did the score feel right?
- B10. What would make you stop using the tool after a week?

### C — Target roles, market & profile accuracy
- C1. Are the target roles still DE / Data Platform / Data Architect — or has your target shifted (e.g., Cloud/DevOps, Analytics Engineer, ML/AI)?
- C2. Is Riyadh/KSA still primary? What's the real priority order across Riyadh, wider GCC, remote-global, relocation?
- C3. Any salary floor/expectations that should filter or rank jobs?
- C4. Is the profile in the PDF (skills/certs/projects/years) still accurate as of today? What changed?
- C5. Have you actually completed Spark/OrderFlow since, or is it still "in progress"?
- C6. Any new certs, projects, or skills to add to the source of truth?
- C7. Company types to favor or avoid (startups, enterprises, staffing firms, consultancies, contractor platforms)?
- C8. Sectors you want vs. want to avoid (fintech, gov, telecom, gaming, healthcare, consulting…)?
- C9. Languages: are JDs/CVs English-only, or does Arabic matter for the KSA market?
- C10. Is "tarigelamin.com offline" still true, and is the "Cansa Group" naming sensitivity still in force?

### D — Portfolio & career positioning
- D1. Which exact job title(s) is the portfolio meant to help you win? Optimize the showcase for whom?
- D2. Will hiring managers actually *clone and run* this, or mostly read the README/GitHub and watch a demo? (changes how much "it must run cheaply for strangers" matters)
- D3. Is the DE pivot (Snowflake + dbt + SQL modeling) the headline skill to prove, or is serverless/cloud the headline?
- D4. What are the 3–5 skills the repo MUST visibly demonstrate to be worth it?
- D5. Is a polished README + architecture diagram + demo video part of the definition of done?
- D6. Do you care about GitHub "production-grade" signals (CI badges, tests, IaC, observability) even if they add no value to you-the-user?
- D7. Would a smaller, genuinely-excellent system beat a larger, sprawling one for your portfolio goals?
- D8. Any specific companies/JDs you're targeting whose stack the portfolio should mirror?

### E — Scope, phasing & timeline
- E1. Pick the honest scope for v1: lean (fetch→score→shortlist), +CV tailoring, +DE analytics (Snowflake/dbt + skill/sector intel), or the full original system.
- E2. Which of these are MUST-HAVE vs NICE-TO-HAVE vs CUT: dedup, CV tailoring, near-miss graduation, skill demand tracker, sector intelligence, multi-user, feedback hub, CloudWatch dashboards/alarms/SLOs, CI/CD?
- E3. How many hours/week can you realistically put in, and over how many weeks?
- E4. Do you want something usable in week 1, or are you fine investing weeks before first value?
- E5. Is "ship one excellent vertical slice, then iterate" acceptable, or do you want the whole thing planned up front?
- E6. What's explicitly out of scope for v1 (things we agree NOT to build)?

### F — Architecture direction & the "better way"
- F1. Keep full AWS serverless, or are you open to a simpler/different shape?
- F2. Is the 7-Lambda + Step Functions + DynamoDB-Streams-CDC design something you love (portfolio signal) or suspect is overkill for one user/day?
- F3. The Streams→stream_processor cross-table sync is elegant but complex — keep it as a portfolio centerpiece, or replace with simpler direct writes?
- F4. Step Functions vs. a single orchestrating Lambda vs. something else (Airflow/MWAA/Dagster/Mage) — preference, given the DE-portfolio angle?
- F5. Notion as the only UI — keep, supplement, or replace (e.g., a small Streamlit/Next dashboard, or just email)?
- F6. Bedrock (IAM, no key) vs. Anthropic API directly vs. another model — any preference or constraint?
- F7. Are you open to NOT using AWS for some parts (e.g., Snowflake for the warehouse, dbt Cloud, a managed scheduler) if it's better DE signal?
- F8. Local-first option: would you value being able to run the whole thing locally (LocalStack/moto/containers) for dev and demos?
- F9. Monorepo with everything, or separate repos (pipeline / infra / feedback)?
- F10. Any architectural patterns you specifically want to showcase (CDC, medallion, event-driven, IaC, data contracts, idempotency, backfill)?

### G — Cloud / stack / cost constraints
- G1. What's your monthly cost ceiling for running this (hard number)?
- G2. Do you currently have AWS credits, and roughly how much/when do they expire?
- G3. Is the ~$10/mo JSearch (RapidAPI) acceptable, or do you want free-only data sources?
- G4. Do you already have a Snowflake account (free trial / paid), and a dbt setup, if we go that route?
- G5. Region: keep eu-north-1, or move closer to KSA (me-central-1/Bahrain) — does data residency matter to you?
- G6. Any language/runtime preference (Python 3.12 fine? open to TypeScript/Go anywhere)?
- G7. Terraform vs. alternatives (CDK, Pulumi, SAM) — preference for the IaC showcase?

### H — Data sources & ingestion
- H1. Is JSearch/RapidAPI giving good KSA/GCC coverage, or have you found it weak/noisy?
- H2. Add other sources (Adzuna, official Indeed/LinkedIn APIs, the Indeed MCP, company career pages, Bayt/GulfTalent for the region)?
- H3. How fresh do jobs need to be (today / 3 days / week)? Daily run enough, or more/less often?
- H4. Roughly how many new relevant jobs/day do you expect in your market (10? 50? 200)?
- H5. Do you want historical backfill/accumulation for the analytics, or only forward-looking?
- H6. Any scraping appetite, or strictly official APIs only (ToS/risk tolerance)?

### I — Deduplication
- I1. Is cross-platform dedup actually a problem you've felt, or theoretical?
- I2. Is the 3-layer engine (with the Bedrock fuzzy layer) worth it, or is fingerprinting enough?
- I3. Do you want duplicates surfaced as a "posted on N platforms = hot" signal, or just hidden?

### J — Scoring engine
- J1. Keep the 7-factor ATS framework and its weights, or rethink scoring entirely?
- J2. Are threshold 70 / floor 50 / near-miss 10 still right for you?
- J3. Do you need deterministic scores (temp 0, ±3 stability), or is rough triage fine?
- J4. Want the embedding pre-filter (Titan) to cut Bedrock cost ~60%, or keep it simple?
- J5. How important is *explainability* (strengths/gaps/strategic assessment) vs. just a number?
- J6. Do you want a feedback/calibration loop (you correct scores, the prompt learns)?
- J7. Scam/legitimacy gate + poster-type label — keep, drop, or change?

### K — CV tailoring
- K1. Is automated CV tailoring in or out for the rebuild? (DE Maximum cuts it.)
- K2. If in: keep DOCX→LibreOffice-PDF, or is DOCX-only / Markdown / a different renderer acceptable?
- K3. Is the LibreOffice Lambda Layer (~250MB) worth the complexity, or would you convert PDF another way (or skip PDF)?
- K4. Keep the exact "tarig-cv" template spec (fonts/colors/section order), or redesign the CV?
- K5. How strict are the honesty rules, and do you want a human-review gate before any CV is "final"?
- K6. One master CV, or multiple base CVs per track (DE vs Cloud vs DevOps)?
- K7. Do you actually re-use generated CVs, or would a "tailoring brief" (what to change) be more useful than a full document?

### L — Storage, data model & the Snowflake/dbt question
- L1. Three stores (S3 + DynamoDB + Notion) for one user — keep, or consolidate?
- L2. Do you want to add a real warehouse (Snowflake / DuckDB / Redshift Serverless / BigQuery) as the analytics layer, per the assessment?
- L3. If yes: Snowflake specifically (assessment's pick), or open to DuckDB/Postgres for a cheaper local-friendly DE story?
- L4. Do you want dbt models with tests/lineage as a first-class portfolio artifact?
- L5. Keep the medallion (bronze/silver/gold) framing, or model it differently?
- L6. Is DynamoDB the right metadata store, or would Postgres/SQLite be simpler and equally good DE signal?
- L7. How long should data be retained, and does any of it need to be queryable historically?

### M — User interface (Notion / email / dashboard)
- M1. Which of the 4 Notion DBs do you actually want: Status Tracker, Near Miss, Skill Demand, Sector Reports?
- M2. Is Notion pleasant to use daily for this, or have you found it clunky?
- M3. What does the ideal daily touchpoint look like — an email you skim? a Notion board? a web dashboard? a CLI?
- M4. What MUST be in the daily digest (top matches? scores? gaps? CV links? near-miss graduations?)?
- M5. Do you want manual status tracking (New/Applied/Interview/Rejected) in the tool, or do you track that elsewhere?
- M6. Any interest in a small custom dashboard (Streamlit/Evidence/Next) as additional DE/portfolio surface vs. Notion?

### N — Near-miss & graduation
- N1. Is the near-miss "watch + re-score + graduate when you learn a skill" loop genuinely useful to you, or feature creep?
- N2. Is the profile-change → re-score → graduation-email feedback loop worth the Streams complexity?
- N3. 30-day expiry sensible, or different?

### O — Analytics: Skill Demand + Sector Intelligence
- O1. Are the Skill Demand Tracker and Sector Intelligence the *most* valuable part to you (career strategy), or secondary?
- O2. Should analytics be the portfolio centerpiece (this is where Snowflake/dbt would shine)?
- O3. Is weekly cadence right? Do you want on-demand regeneration?
- O4. Sector Intelligence does a lot (pain points, current solutions, suggested projects, interview prep) — all of it, or a subset?
- O5. Would you trust/act on LLM-inferred "suggested portfolio projects," or is that noise?
- O6. Any specific analytics question you personally want answered every week?

### P — Observability, SLOs & ops
- P1. CloudWatch dashboard + 6 alarms + 5 SLOs for a one-user daily job — genuine value, portfolio signal, or theater?
- P2. Which alarms do you actually want to be paged on (e.g., "pipeline didn't run", "cost spike")?
- P3. Do you want real SLO measurement, or is documenting SLOs (for the interview story) enough?
- P4. Cost-spike alarm threshold (the old plan used $30/mo) — your number?

### Q — Security, secrets & privacy
- Q1. Secrets Manager + IAM-scoped-per-Lambda — keep, or simpler (SSM Parameter Store / env) acceptable for a personal tool?
- Q2. Your CV/profile contains PII and lives in S3/Notion — any privacy concerns about that, or about a public repo referencing it?
- Q3. Should the public repo ship with your real profile, a sanitized sample, or a template only?
- Q4. Any compliance/data-residency constraint (KSA data in-kingdom) that should drive region/storage choices?

### R — Multi-user, feedback hub & open-source
- R1. Is multi-user a real goal you'll pursue, or aspirational "architecture story" only?
- R2. Build the centralized feedback hub now, later, or never?
- R3. Do you actually intend to open-source this and support strangers running it, or is it a personal/portfolio repo?
- R4. If open-source: how much "works for anyone out of the box" effort is justified vs. "works for me, documented for others"?

### S — Orchestration & scheduling
- S1. Daily cron at 09:00 Riyadh still right? One run/day, or multiple?
- S2. Keep Step Functions, or is the DE-portfolio better served by Airflow/Dagster/Mage (warehouse-world tools)?
- S3. Do you want the "feature flags / skip states while developing" capability from the old plan?
- S4. Separate weekly workflow for analytics, or fold into the daily run?

### T — Build process, testing & how I (Claude) should work
- T1. The old rules said "Claude executes approved plans, never makes architecture decisions." For the rebuild, do you want me to *propose* architecture and decisions, or stay strictly executional?
- T2. Where should the plan/source-of-truth live this time: Notion (as before), in-repo Markdown, or this plan workflow?
- T3. Keep the rigid phase-by-phase + per-phase Execution Logs in Notion, or a lighter cadence?
- T4. Keep the 800-line file-size write hook and similar guardrails?
- T5. Keep the mandatory Correlation ID + Pre-condition Guards standards in every handler, or only where they earn their keep?
- T6. How much do you want me to STOP and confirm vs. proceed autonomously between steps?
- T7. Testing expectations: unit tests? integration against LocalStack? live smoke tests? none?
- T8. Do you want me to use multi-agent workflows/parallelism for big build steps, or keep it linear and reviewable?
- T9. Commit/PR style: small frequent commits, conventional commits, PR-per-feature?
- T10. How do you want progress tracked (Notion tracker, GitHub issues/projects, a TODO in repo)?

### U — Documentation & the "reflect understanding into docs" goal
- U1. When you say "reflect this understanding in docs/documentation," what artifacts do you picture — a fresh CLAUDE.md, a design doc, an ADR set, a README, a Notion rebuild, all of these?
- U2. Who's the audience for each doc (future-you, future-me/Claude, hiring managers, other users)?
- U3. Should docs live in-repo (Markdown/`docs/`) so they're part of the portfolio, or stay in Notion?
- U4. Do you want lightweight ADRs (Architecture Decision Records) capturing the "why" behind each rebuild choice?
- U5. What level of detail do you want in the new build plan — exhaustive apply-sequences like before, or leaner?

### V — Risks, non-goals & decision rights
- V1. What are your hard "never do this" constraints for the rebuild?
- V2. What's your tolerance for complexity vs. your bias toward shipping something simple that works?
- V3. Where do you want final decision rights, and where are you happy to delegate to my recommendation?
- V4. Anything important about your goals, context, or constraints that none of these questions touched?
- V5. Of everything above, what are the 3 questions you think matter most — so we anchor on those first?

---

## 3. Captured Understanding / Decisions
*(running log as Tarig answers — this becomes the seed for the new design docs + plan)*

### Framing (the 4 forks)
- **Primary goal:** Both equally — the tool IS the portfolio. Every component must earn dual value (daily-useful AND real portfolio signal).
- **v1 scope:** Full original system (not a lean cut). Discovery = re-justify + improve each part, not remove.
- **Architecture authority:** Claude recommends (defended), Tarig approves.
- **Working style:** Co-design interactively now → Claude builds to the locked plan.
- **Discovery method:** Section-by-section interactive.

### Section A — Goals & "better"
- **A3 (why restart):** Nothing failed — "it was fine, want it better." A clean, smarter second pass.
- **A4 (what "better" means):** (1) More DE depth (SQL/warehouse/dbt — the assessment's gap), (2) More reliable / actually works daily. → These are the two primary improvement vectors.
- **A7 (overkill?):** None — keep it all. Complexity is acceptable *if justified*. Full ambition retained.
- **A10:** Truly greenfield. Reference old ideas as inspiration only; clean slate.
- **Implication:** Not a scope-reduction exercise. The mandate is: rebuild the full system clean, ADD a real warehouse + dbt analytics layer for DE depth, and raise reliability. Burden on Claude to justify each retained piece of complexity (since "keep it all" + "more DE depth" can fight "simpler"/"reliable" — I must show each part pays for itself).
- **Open (free-form, revisit):** A5 success-in-3-months, A6 must-keep (implicitly "all"), A8 deadline.

### Section B — Real job-search situation
- **B1 status:** Active but selective — employed/stable, will move for the right role; watching closely, applying occasionally. → He WILL use this soon; it must be genuinely good, not a demo.
- **B2 urgency:** Weeks — needs value fast. → **Resolves the "full system but fast" tension:** build a usable core vertical slice (discovery → score → CV → shortlist) first so it delivers in weeks, then complete the full system around it ("build while using"). MVP-first sequencing inside a full-system commitment.
- **B5 biggest time-save:** Discovery + Filtering/scoring + CV tailoring (the core pipeline). NOT tracking/market-intel. → **Key insight:** the analytics layer (Skill Demand, Sector Intel) is primarily *portfolio/strategic* value, not daily time-save. Core pipeline = personal ROI; analytics = portfolio signal. Both kept, but I now know where each one's value lives → sequence core pipeline first.
- **B6 auto-CV:** Core value. → CV tailoring stays first-class; the DOCX/PDF/LibreOffice questions are live and must be answered well.
- **Open (free-form, revisit):** B3 apps/week + how, B4 most painful part, B9 score felt right/wrong, B10 abandon-in-a-week.

### Section C — Target roles, market & profile
- **C1 roles:** DE / Data Platform / Data Architect only — NOT expanding to Analytics Eng, Cloud/DevOps, or ML. Focused target.
- **C2 locations:** Riyadh (primary) + wider GCC + open to relocate anywhere. **Did NOT pick remote-global** → on-site/relocation oriented; de-emphasize the old "Remote" search query.
- **C4 profile:** Accurate as-is → use the PDF candidate profile unchanged as the source of truth. No fresh intake needed. (Implies C5 Spark still "in progress", C6 no new additions.)
- **C9 language:** English only → simplifies data sources, scoring, and CV (no Arabic handling needed).
- **Assumed carried forward (confirm later):** C10 — tarigelamin.com offline; use "Cansa Group" (not "Cansa Agricultural"/"CANSA"); honesty rules intact.

### Section D — Portfolio & career positioning
- **D2 engagement:** "Some will clone & run" → must be genuinely reproducible (clean Terraform + setup; a real interviewer might pull and run it). Not full OSS-for-strangers polish, but it has to stand up.
- **D3 headline:** "End-to-end system design" → **breadth IS the portfolio point.** This makes "keep it all / full system" coherent: the complete, working system is the story, not any single tool.
- **D4 must-demonstrate:** ALL FOUR — LLM/AI integration, Warehouse + dbt modeling, AWS serverless + IaC, Streaming/CDC. → **Resolves the complexity-justification question for several pieces:**
  - Streaming/CDC is a *required* signal → the DynamoDB-Streams mediator now has portfolio justification (not theater).
  - Warehouse + dbt is *required* → confirms ADDING a real warehouse + dbt layer (the DE-depth gap-filler) is in, first-class.
  - Serverless + IaC and LLM stay.
- **D5 presentation:** "Yes, all of it" → README + architecture diagram + demo video are part of Done.
- **Portfolio thesis (locked):** *A complete, reproducible, production-style system demonstrating end-to-end design across LLM + warehouse/dbt + AWS-serverless/IaC + streaming/CDC, fully documented (README/diagram/video), that an interviewer can clone and run.*

### Section E — Scope, phasing & timeline
- **E3 commitment:** Full-tilt, 20+ h/wk — primary focus. Calendar timeline can be short despite full scope.
- **E5 approach:** **Plan everything, then build.** Design + document the entire system before production code. → Confirms the immediate deliverable of this session = a complete design/doc set; build is a later phase. Matches Tarig's opening flow (absorb → question → docs → implement).
- **E2 next-after-core:** Claude to recommend the post-core sequencing.
- **E6 multi-user + feedback hub:** **Design-for, build later.** Architect the `user_id` seam + feedback-POST hook, but don't implement multi-user/feedback in v1. → Pragmatic scoping inside "keep it all": capability designed in, implementation deferred.
- **v1 build scope (single-user, full):** core pipeline (discovery→score→CV→shortlist) + 3-layer dedup + near-miss/graduation + warehouse/dbt analytics + Skill Demand + Sector Intelligence + observability + CI/CD + full docs. Deferred-but-designed: multi-user, feedback hub.

### Section G — Cost, accounts & region
- **G1 cost ceiling:** $50+/mo acceptable — optimize for quality/signal, not minimal cost.
- **G2 credits:** Some (limited) AWS credits — be cost-aware but not cost-bound.
- **G4 warehouse:** **Snowflake + dbt (locked).** Strong DE keyword, matches assessment. External-to-AWS → adds a genuine cross-cloud story (S3 external stage / Snowpipe → Snowflake → dbt marts) AND a setup dependency to document for clone-and-run.
- **G5 region:** Claude's call. Lean: `eu-north-1` (existing plan, cheap, EU Bedrock inference profile) or `us-east-1` (widest Bedrock model availability). Avoid me-central-1 unless Bedrock models are confirmed there. Residency not a hard constraint (Tarig didn't select it).
- **Open (free-form):** G6 runtime (assume Python primary; possibly SQL-heavy for dbt), G7 IaC (assume Terraform — his showcase — confirm).

### Section H — Data sources
- **H1:** No attachment to JSearch — open to the best sources for KSA/GCC DE.
- **H2 sources:** JSearch (aggregator) + Adzuna. → Multi-source from day one → **pluggable source-adapter layer** (good DE signal; regional boards addable later).
- **H4 volume:** Modest, 10–30 relevant/day → Bedrock scoring cost trivial; dedup is about quality not scale; embedding pre-filter optional (nice-to-have signal, not needed for cost).
- **H6:** **Official APIs only — no scraping.** Cleaner + ToS-safe + better portfolio hygiene.

### Sections I + J — Dedup & scoring
- **J1 framework:** Keep the 7-factor ATS framework; refine weights together during design.
- **J5 explainability:** **Critical** — keep per-job strengths / gaps / strategic_assessment. Reasoning is the value, not just the number.
- **J6 calibration:** Claude's call → **recommend a lightweight loop:** capture Tarig's score corrections as structured data → tune the scoring prompt + drive a "scoring accuracy" SLO. High reliability ROI, low complexity, strong interview story. (Supports the "more reliable" north star.)
- **I2 dedup:** **All 3 layers** (exact ID → fingerprint → Bedrock fuzzy) + keep the "found on N platforms = hot signal" feature.
- **I2-UPGRADE (multi-API accuracy concern raised by Tarig):** dedup is precision-first + fail-safe + *measured* (no literal 99.99% promise — that's not honest for fuzzy matching). Error model: missed-dup = trivial cost; wrong-merge = lost opportunity (the dangerous error) → bias hard toward precision.
  - **L1** exact source-id (re-fetch). **L2** deterministic fingerprint (normalized title+company+location). **L3** multi-signal resolution:
    - **JD-body embeddings** (Bedrock Titan) stored in **pgvector** (in the Postgres operational store) → nearest-neighbor *blocking* (no O(n²)); biggest accuracy lever (reposts share descriptions; different roles don't).
    - **apply-URL / canonical-source-id** match (high-precision, cheap; old plan missed it).
    - **company canonicalization dictionary** (STC=Saudi Telecom=stc, etc.).
    - **time-window scoping** (dup only within N days; later repost = genuine new opening + hiring signal).
  - **Confidence bands:** auto-merge (high) / keep (low) / **ambiguous → Bedrock adjudication on full JD → SAME/DIFFERENT/UNSURE** / **UNSURE → never auto-merge; surface "possible duplicate?" for 1-click human merge** (fail-safe).
  - **Measurement:** log every decision; human-review of ambiguous cases = labels → compute & dashboard **precision/recall**; conservative auto-merge bar; tune from data. (Entity resolution w/ measured P/R = strong senior-DE signal.)
  - **Synergy:** source-adapter + data-contract normalization (TWEAK 2) precedes dedup → clean common schema is the prerequisite for accuracy.
  - **Target framing:** ~99%+ auto-merge **precision** (don't lose jobs) + measured, continuously-tuned recall + human escape hatch — NOT a blanket 99.99%.
- **I2-MODEL (Tarig's decision — GROUP, don't suppress):** This overrides the old "mark one canonical + hide the duplicates" behavior.
  - **Philosophy:** never-miss > never-duplicate. Applying 2–3× is acceptable; hiding a real job is not. Engine **never silently removes a posting.**
  - **Behavior:** **cluster** suspected-same postings and **surface the whole group** with all platform apply-links + a "suspected same as X/Y/Z" annotation. User decides whether to apply via one or several platforms (hiring teams sometimes favor one platform's pipeline — preserve that option).
  - **"Suspected Duplicates" surface** (Notion view/DB) holds uncertain clusters; user confirms or splits. Machine proposes, user disposes.
  - **Cost + safety both win:** **score + tailor CV once per cluster** (identical content) using a representative posting, but **never hide an apply-link.** One clean entry per real job, every platform option attached.
  - **Data model:** `cluster_id` + `representative_job_id` (for scoring/CV) + per-posting `apply_url`/`source` + `match_status` (confirmed / suspected / rejected_by_user) + `match_confidence`. "Found on N platforms = hot" falls out of cluster size.
  - ✅ **Per-cluster work:** **score + tailor ONE CV per cluster** (identical content), surface all platform apply-links.
  - ✅ **Suspected Duplicates = a dedicated (5th) Notion database** for ambiguous clusters to confirm/split — kept separate from the clean Status Tracker shortlist.
- **Open (free-form):** J2 thresholds (assume 70/50/10 unless changed), J7 scam gate + poster-type label (assume keep).

### Section K — CV tailoring
- **K2/K3 rendering:** Claude's call → **drop LibreOffice-in-Lambda** (the old #1 reliability risk). Recommend a deterministic, dependency-light renderer: one structured content model → **DOCX (python-docx) + PDF via pure-Python/HTML (e.g. WeasyPrint/Chromium)**, no system Word/LibreOffice. Reliability-first; restyling stays easy.
- **K4 template:** Keep the tarig-cv spec as base; refine during design.
- **K5 review:** **Strict honesty + human-review gate** — every CV is a draft Tarig approves before "submission-ready." → pipeline needs a draft→approved state; review gate doubles as calibration-data capture.
- **K6 variants:** **One master CV** (tailoring handles per-job emphasis); fits the DE-only focus.

### Section M — Daily interface / UI
- **M1:** **5 Notion DBs** — Status Tracker, Near Miss, Skill Demand, Sector Reports, **+ Suspected Duplicates** (added during dedup discussion).
- **M3 daily surface:** **Email + Notion both first-class** — email to triage each morning, Notion to act + track.
- **M6 dashboard:** BI dashboard over Snowflake/dbt marts = **designed-for, build-later** (v1.1 seam-ready, alongside multi-user + feedback hub). Strong portfolio surface, not a core blocker.
- **M5:** Manual application-status tracking **in Notion** (New/Applied/Interview/Rejected).
- **v1.1 "seam-ready, build-later" bucket so far:** multi-user, feedback hub, BI dashboard.

### Section Q — Security & privacy
- **Repo visibility:** Public, **PII-scrubbed always** — repo never contains real personal data.
- **Q3 profile-in-repo (Claude's call):** ship a realistic **sanitized sample profile + empty template** → system is demonstrable/runnable by anyone (supports clone-and-run).
- **Q2 real PII (Claude's call):** real profile/CV in a **gitignored local `config` → uploaded to private S3 at setup**; never in the repo tree; tailored CVs only in private S3/Notion.
- **Q1 secrets:** **AWS Secrets Manager**, IAM-scoped per function (best signal, ~$1/mo).

### Section T — Working process
- **T6 autonomy:** **Confirm major decisions only** — drive within phases, stop for consequential/irreversible choices. (More autonomy than the old stop-every-phase rule.)
- **T7 testing:** **All four** — unit + integration (LocalStack/moto) + **dbt tests** + live smoke. Full pyramid → reliability + DE-quality + clone-and-run confidence.
- **T2 plan source-of-truth:** **Notion** (like before) — phase plans/source-of-truth in Notion.
- **T3 phase rigor (Claude's call):** phased build + **streamlined** execution log (old per-phase living-document was too heavy) + in-repo ADRs/CHANGELOG for the portfolio record.
- **Open (free-form):** T9 commit/PR style, T10 progress tracking surface, T5 correlation IDs + guards (recommend: correlation IDs everywhere — cheap, great observability/portfolio signal; guards where they earn it).

### Section U — Documentation
- **U1 artifacts:** ALL — fresh (lean) **CLAUDE.md** + **architecture/design doc** + **ADRs** + **README + diagram** (+ demo-video plan).
- **U3 location:** "Everything in repo." → **CONFLICTS with T2 (Notion).** Resolution (pending Tarig confirm): **repo `/docs` canonical for all design/plan/ADR docs; Notion only for the operational user-facing databases.** More portfolio-coherent. ⚠️ confirm in Section V.
- **U5 detail:** **Exhaustive apply-sequences** (WHY / WAIT-FOR / FAILURE-MODE per step) — he wants the detailed build plan.
- **U-next deliverable (Claude's call):** **Architecture recommendation first → Tarig reacts → then full doc set + detailed build plan.** (Co-design flow.)

### Section N/O/P/S — Near-miss, analytics, observability, orchestration
- **S2 orchestration:** **Step Functions only** (AWS-native, cheap); dbt runs as a task in the flow (Lambda/ECS or dbt invocation). No separate Airflow/Dagster.
- **P observability:** **Full suite** — CloudWatch dashboard + ~6 alarms + measured SLOs.
- **N near-miss:** **Keep the full loop** — daily re-score + auto-graduation + graduation emails + Streams-driven events (the CDC showcase). → Streams mediator fully justified + in.
- **O analytics:** **Important, not centerpiece** — build well, but the **core pipeline is the star.** Confirms: sequence core pipeline first; analytics is the DE-depth layer behind it. Weekly cadence + on-demand regen.

### Section V — Decision rights, reconciliation, prime directive
- **Docs reconciliation (Claude's call):** **Repo canonical.** All design/plan/ADR docs in repo `/docs`; Notion hosts only the operational user-facing databases. Overrides the earlier T2 "Notion" answer.
- **V3 decision rights:** **Approve architecture + major decisions; delegate the rest** to Claude.
- **Prime directive (tiebreaker for all design tradeoffs):** **DE depth / warehouse signal.** When two designs compete, favor the one that best showcases SQL/warehouse/dbt. (Reliability remains a strong secondary north star.)
- **Ready:** Synthesize the recommended architecture now.

---

## 4. Recommended Architecture (v1) — for Tarig's reaction

**Framing improvement over the old design:** split the system into two cleanly-separated planes. This is the single biggest upgrade and it directly serves the prime directive (DE depth) without losing the serverless/cloud signal.

### Plane 1 — Operational (AWS serverless app)
The daily job-hunting tool. EventBridge (daily, ~09:00 Riyadh) → **Step Functions**:
1. **fetcher** — pluggable source adapters (JSearch + Adzuna; official APIs only) → land raw JSON in **S3 `raw/`** + upsert job-state in **DynamoDB**.
2. **bronze_processor** — 3-layer dedup (exact id → fingerprint → Bedrock fuzzy); mark canonical + "found on N boards".
3. **scorer** — Bedrock (Claude Sonnet), 7-factor ATS (weights TBD), explainable (strengths/gaps/strategic_assessment), emits `skills_extracted` + `sector` → DynamoDB + S3 `scored/`.
4. **cv_tailor** — Bedrock → structured content model → **DOCX (python-docx) + PDF (pure-Python/HTML renderer, NO LibreOffice)**; CV is a **draft** pending review-gate → S3 `gold/` + DynamoDB.
5. **notifier** — Notion writes + SES daily digest.
6. **stream_processor** — DynamoDB **Streams CDC**: cross-table sync, near-miss → graduation loop, Skill-Demand updates, CV-link round-trip. (Justified: Streaming/CDC is a required portfolio signal + powers the graduation loop.)
- Cross-cutting: **Secrets Manager** (IAM-scoped), **correlation IDs everywhere**, guards where they earn it, **CloudWatch** dashboard + ~6 alarms + measured SLOs.

### Plane 2 — Analytical (DE-depth: Snowflake + dbt) ← the prime-directive layer
Realize the **medallion *in the warehouse*** (the modern DE pattern), not as mere S3 folders:
- **S3 (`raw/` + `scored/`) → Snowflake external stage / Snowpipe** → raw landing schema.
- **dbt**: `staging → intermediate → marts`, with **tests** (not_null/unique/relationships/accepted_values), **docs + lineage**, and **snapshots** for SCD (job-status history, profile changes).
  - Marts: `dim_company`, `dim_skill`, `dim_job`, `fct_job_score`, `fct_skill_demand`, `fct_sector_signal`, `fct_application_funnel`.
- **Analytics consumers** read from marts: Skill Demand Tracker + weekly **Sector Intelligence** (Bedrock summaries *grounded in mart data*, not raw guesses) → Notion.
- dbt runs as a **task orchestrated by Step Functions** (dbt-core in a container/Lambda) — keeps it AWS-native, shows orchestration of transforms.

### Why this is "better" on both axes
- **DE depth (prime directive):** a genuine warehouse with dbt modeling/tests/lineage/snapshots — exactly the flagged SQL/modeling gap, now the centerpiece of the data story.
- **Reliability:** drop the fragile LibreOffice path; full test pyramid (unit + LocalStack + dbt tests + smoke); calibration loop measures scoring accuracy.
- **Breadth/end-to-end (the thesis):** polyglot persistence done right — DynamoDB (operational + CDC source) + S3 (lake landing) + Snowflake/dbt (warehouse) + Notion (UI). Each store has a distinct, defensible role.

### Key "you recommend" decisions (defended)
- **Region:** **us-east-1** — widest Bedrock model/inference-profile availability + easy Snowflake co-location; residency isn't a constraint. (Alt: eu-north-1 like the old plan.)
- **Warehouse:** Snowflake (locked); medallion realized via dbt.
- **dbt execution:** dbt-core in a container task invoked by Step Functions (AWS-native). (Alt: dbt Cloud — less infra, external dep.)
- **CV PDF:** pure-Python/HTML renderer (WeasyPrint or headless Chromium), DOCX via python-docx — reliability-first.
- **Deferred but seam-ready:** multi-user (`user_id` dimension), feedback hub, BI dashboard over marts.

### Phasing (delivers core value in weeks, then DE depth)
- **P0 Foundation:** repo + `/docs` + Terraform skeleton + CI + LocalStack dev.
- **P1 Ingestion+dedup** → usable dedup'd feed.
- **P2 Scoring** → scored shortlist in Notion + email *(core value, ~weeks at full-tilt)*.
- **P3 CV tailoring** (reliable renderer + review gate).
- **P4 Debezium CDC (Postgres→Kinesis) + near-miss/graduation + skill tracker + reverse-ETL.**
- **P5 Warehouse:** S3→Snowflake + dbt staging/marts + tests/docs *(DE-depth headline)*.
- **P6 Sector Intelligence + analytics consumers.**
- **P7 Observability + calibration loop.**
- **P8 CI/CD hardening + README/diagram/demo video + seam-ready stubs.**

### Architecture locks (after Tarig's reaction)
- ✅ **Two-plane framing approved** (with the tweaks below).
- ✅ **Medallion location:** **Both S3 + warehouse** — bronze/silver/gold prefixes on S3 (lake signal) AND dbt-modeled in Snowflake (warehouse signal). Accept minor duplication for the broader story.
- ✅ **Region:** **us-east-1.**
- ✅ **dbt execution (Claude's call):** **dbt-core in a container task orchestrated by Step Functions** (AWS-native).

### Adopted tweaks (reshape the operational + CDC planes)
- ✅ **TWEAK 1 — Postgres + Debezium CDC replaces DynamoDB+Streams.** Operational store = **managed PostgreSQL** (Aurora Serverless v2 *or* RDS Postgres — cost decision in design doc); change-data-capture via **Debezium** (real CDC, mirrors Tarig's Barakah/OrderFlow). This is now the **strongest DE-depth signal** in the project: OLTP → Debezium CDC → warehouse. Replaces the old DynamoDB-Streams → stream_processor pattern (the cross-table sync / near-miss-graduation logic moves onto CDC consumers). Supersedes TWEAK 4 (the CDC transport provides the streaming backbone).
  - **Transport cost flag (resolve in design):** full Kafka/MSK is too costly for this scale. Lean = **Debezium Server (standalone, no Kafka-Connect cluster) on Fargate → Kinesis** sink (real Debezium, ~$20–30/mo). MSK noted as the documented production-scale upgrade. Aurora Serverless v2 floor ~$40/mo vs RDS t4g.micro ~$12/mo — pick in design (budget allows either).
  - **⚠️ AMENDED (honesty audit + Tarig "we'll need it later"):** v1 **keeps Postgres** but **DEFERS Debezium** → v1 uses **nightly batch S3→Snowflake + incremental dbt** (right-sized + defensible). **Debezium CDC becomes the documented, seam-ready scale-up upgrade** (parallel to MWAA-as-future). Honest framing: *"right-sized to batch; documented the CDC upgrade path."* The built CDC/Debezium showcase lives in OrderFlow. → joins the seam-ready/build-later bucket.
- ✅ **TWEAK 2 — Data contracts + quality gates:** Pydantic schemas at every boundary + dbt contracts/tests + source-freshness checks. (DE-quality + reliability signal.)
- ✅ **TWEAK 3 — Reverse-ETL + metrics layer:** formalize marts → Notion as a named reverse-ETL pattern with a small metrics/semantic layer (define metrics once).
- ❌ **TWEAK 4 — Real streaming (Kinesis ingestion):** skipped for v1 (theater at 10–30/day); the Debezium path already supplies the streaming backbone.
- ✅ **Thresholds (Claude's pick):** **75 / 55 / 10** — threshold 75 (active-but-selective), hard floor 55, near-miss band 10 (→ near-miss 65–74). Config-editable.
- ✅ **Warehouse confirmed: Snowflake** (vs Databricks vs Postgres/DuckDB). Rationale: at ~10–30 rows/day the warehouse is a *portfolio/skill* choice, not a data-need; Snowflake directly fills the flagged SQL/warehouse-modeling+dbt gap, is cheapest/most-reliable at this scale (instant warehouses, $0 idle), cleanest dbt+AWS story, strong KSA/GCC demand. **Spark/Databricks/Delta signal is deliberately deferred to OrderFlow** (where data volume justifies it) — don't force Spark onto tiny data here. → ADR-worthy.

## 5. Open items

### BLOCKERS — must capture before writing final docs
- **Framing tweaks:** Tarig approved the two-plane design "with tweaks" — tweaks not yet specified.
- **Scoring thresholds:** Tarig chose "Adjust" — exact numbers not yet given (default was 70/50/10).

### Resolved
- C10: both true (site offline; "Cansa Group"). · G7: **Terraform.** · T10: **in-repo TODO + CHANGELOG.** · Region: **us-east-1.** · dbt: **dbt-core via Step Functions.** · J7: keep scam gate (assumed). · G6: Python primary + SQL (dbt).

### Still-open (low-stakes, can fold in or default)
A5 success metric · A8 deadline · B3 apps/week + how · B4 most painful step · T9 commit style · Snowflake account/region · V4 anything missed · V5 top-3.

## 6. The Plan (on approval)

**Phase D — Design & documentation (do first; "plan everything, then build").** Scaffold the repo and author the canonical doc set in `/docs`:
1. `docs/architecture.md` — the full design: two planes, Postgres+Debezium CDC, S3+Snowflake medallion, dbt marts, data contracts, reverse-ETL, component-by-component data flow, the marts/ERD, and a rendered architecture diagram.
2. `docs/adr/` — one ADR per significant decision (region, **warehouse: Snowflake vs Databricks vs Postgres/DuckDB**, Postgres-vs-DynamoDB, Debezium transport, dbt execution, CV renderer, calibration loop, secrets, observability scope, docs-in-repo). Each captures context / options / decision / consequences.
3. `CLAUDE.md` — a fresh, lean operating doc (replaces the old one): identity, the two-plane model, conventions, the "confirm major decisions only" working rule, correlation-IDs-everywhere, guards-where-they-earn-it, test-pyramid expectation, repo-as-source-of-truth.
4. `README.md` + diagram + demo-video outline (portfolio-facing).
5. `docs/build-plan.md` — the **exhaustive apply-sequence build plan** (P0→P8), each step with WHY / WAIT-FOR / FAILURE-MODE.
6. Resolve the two design sub-decisions inside the docs: Aurora-Serverless-v2-vs-RDS-Postgres, and Debezium-Server-→-Kinesis-vs-MSK.

**⏸ REVIEW GATE — after Phase D, STOP and hand the docs to Tarig for review before any code.** The docs are the deliverable he asked for ("reflect understanding in docs"); building starts only after he signs off on them.

**Phase 0→8 — Build** (per `docs/build-plan.md`): P0 foundation (repo/Terraform/CI/LocalStack) → P1 ingest+dedup → **P2 scoring (first usable value)** → P3 CV (reliable renderer + review gate) → P4 Debezium CDC + near-miss/graduation + reverse-ETL → **P5 Snowflake + dbt marts + contracts/tests** → P6 sector intelligence + analytics consumers → P7 observability + calibration loop → P8 CI/CD + README/diagram/demo + seam-ready stubs (multi-user, feedback hub, BI dashboard). Confirm major decisions only; track progress in-repo (TODO + CHANGELOG); commit style per Tarig (TBD).

**Verification:** unit tests (scoring/dedup/CV) + LocalStack/moto integration + dbt tests on marts + a live end-to-end smoke run; each build phase ends with its own verify checklist before moving on.

---

## 7. Architecture honesty audit + distribution (Tarig's challenge: "real, defendable architecture, not name-spinning")

**Governing principle (proposed north star):** At ~10–30 jobs/day, nothing is justified by *load*. Defend the stack ONLY as *"a personal-scale tool built to production standards — real patterns, modest scale, right-sized deliberately."* Never claim scale demands it. **Defense test per service:** name the trivial alternative + why this beats it, or cut it.

**Verdicts:**
- ✅ Defensible: Lambda + Step Functions (workflow w/ retries/observability > mega-cron); Bedrock (core LLM value); **Postgres** (relational data → relational DB; *more* defensible than the old DynamoDB/NoSQL choice); dbt + warehouse + medallion (good practice at any scale + the target skill); S3 / SES / Secrets Manager / Notion-UI (trivially right).
- 🚩 **Debezium CDC — AT RISK / recommend CUT.** Reverses TWEAK 1's CDC piece. At this volume, **nightly batch S3→Snowflake + incremental dbt** is simpler and right-sized; Debezium-on-30-rows is resume-driven. **Keep Postgres** as the operational store; **drop Debezium**; **keep the CDC/Debezium showcase in OrderFlow.** Depth here comes from warehouse modeling + entity resolution + data contracts instead.
- 🚩 **Full SLO suite + 6 alarms — AT RISK / recommend RIGHT-SIZE.** Keep 2–3 real alarms (pipeline-didn't-run, cost-spike, error-rate) + modest dashboard; *document* SLOs rather than over-build. (Revisits P "full suite".)
- ⚠️ Snowflake = a *showcase* choice, not scale necessity — fine if stated honestly.
- **Honesty ≠ less DE depth:** depth from defensible-deep things (warehouse modeling, measured entity resolution, data contracts, incremental/idempotent), not streaming-on-tiny-data.

**Distribution (Tarig torn: self-host-everyone vs centralized):**
- **Recommendation: self-hosted / open-source.** Goal = job-search tool + portfolio, NOT a company. Self-hosting makes IaC/reproducibility the portfolio value, zero ongoing liability, matches "some will clone & run."
- **Centralized SaaS = trap:** you'd own others' cost, auth, billing, support, multi-tenancy, and **their CVs/PII (legal/privacy liability)** + job-data ToS exposure. That's a startup; it explodes scope and distracts from landing a job.
- Keep multi-user as a **documented, seam-ready future** (already decided), not built now. SaaS = a deliberate later pivot if ever.

**Resolved:**
- ✅ **Distribution: self-hosted / open-source.** Multi-user/SaaS = documented future pivot, not built. (Centralized = liability/scope trap, rejected.)
- ✅ **Debezium: DEFER** (not cut) — Tarig: "we'll need it later." v1 = batch + incremental dbt; Debezium = documented scale-up upgrade; built CDC showcase in OrderFlow.
- ✅ **Observability: RIGHT-SIZE** — 2–3 real alarms + modest dashboard + documented SLOs (supersedes the earlier "full suite").

**✅ RESOLVED — "defensible" rubric adopted, BALANCED dial:** default to the simplest defensible option; keep the **2–3 highest-value showcases clearly labeled** (warehouse/dbt modeling + measured entity resolution as the headliners). Consequence: keep the reverse-ETL **metrics layer light** (dbt metrics, not a heavy semantic-layer framework); don't over-build for one user. Every component must pass a lens below + be honestly framed.

The rubric — a choice is **defensible** if you can answer an interviewer's *"why this and not the simpler thing?"* without "to put it on my resume." Concretely, ≥1 must hold:
1. **Right-tool fit** — best fit for the problem's *shape* (not size): relational data→Postgres, semantic match→LLM, blobs→S3.
2. **Real requirement** — satisfies an actual need of *your* use (reliability, reproducibility, $0-idle, observability you'll use).
3. **Honest showcase (labeled)** — there to demonstrate a target skill, stated openly, with a clear "when this is overkill vs needed."
4. **Documented scale path** — right-sized now + documented upgrade for when scale arrives (batch→CDC; Step Functions→Airflow).
**Theater (cut)** = the only reason is "looks impressive" AND you'd have to pretend scale needs it.
**The test:** *can you name the simpler alternative + the tradeoff?* Yes → defensible (even a labeled showcase). No / must pretend → theater.
**Key nuance:** defensible ≠ minimal. Breadth is fine if every piece passes a lens AND framing is honest. The enemy is *unjustifiable* complexity + *dishonest* framing — not complexity itself.

---

## 8. ⭐ EVOLUTIONARY ARCHITECTURE MODEL (Tarig's reframe — supersedes the §6 "build all phases" framing)

**Core idea:** Don't build the full system at once. Build the **minimal end-to-end thing that works**, then evolve through a **sequence of deliberate, observable migrations — each a clean GitHub release** introducing capability the previous lacked. The full system is the *end-state reached via migrations*, not a v1.

**Why it's the strongest possible framing for this project:**
- **Rare senior/staff portfolio signal:** controlled architectural *evolution* (ADR + migration guide per step) demonstrates judgment over time — far rarer than a finished repo.
- **Defensible by construction:** each capability arrives exactly when justified (clustering-dedup lands *with* source #2; Step Functions lands when the single Lambda is genuinely too big). Kills RDD at the root + perfectly satisfies the §7 rubric (esp. lens 4).
- **Leaner v0 ⇒ more (better) migrations** ⇒ more portfolio gold. The migrations ARE the showcase.
- All prior "deferred / seam-ready" items become **named migration releases**, not vague laters.

**✅ LOCKED minimal v0 (irreducible core):**
> EventBridge (daily) → **one Lambda**: fetch from **one source** → raw to **S3** + rows to **Postgres** → **Bedrock** score vs profile → **daily email digest**. Terraform + Secrets Manager + basic tests + **minimal CI** (lint+test). *No CV, no Notion, no Step Functions, no dedup-clustering, no warehouse.* It works (you wake to a scored shortlist); everything else is a migration. (CV = M1, email-only, single Lambda — all confirmed.)

**✅ Migration roadmap (Claude-optimized; Tarig "you optimize" — pending final confirm):**
- `v0.1` Minimal core (above) — light CI from day 1 (release model needs it).
- `M1 (v0.2)` **CV tailoring** — reliable renderer (no LibreOffice), one master CV, draft→review gate. *(Tarig's locked first migration.)*
- `M2 (v0.3)` **Multi-source + clustering dedup + Suspected-Duplicates** — boosts discovery (#1 time-save); source #2 *justifies* clustering dedup (defensible-by-construction).
- `M3 (v0.4)` **Single Lambda → Step Functions** — earned once the Lambda does fetch-multi→dedup→score→CV→email; monolith→orchestration story.
- `M4 (v0.5)` **Notion workspace + near-miss/graduation** — Status Tracker + Suspected-Dups + Near-Miss DBs; watch/re-score/graduate; stands up the calibration-correction surface.
- `M5 (v0.6)` **dbt analytics marts on Postgres** (staging→marts, tests/lineage/incremental) — the DE-modeling headline, *minimally* (no dedicated warehouse). **Snowflake is conditional** — added later ONLY if a real analytics bottleneck demands it (per §9 tool-minimalism).
- `M6 (v0.7)` **Skill Demand + Sector Intelligence** — on the dbt marts + Bedrock summaries grounded in mart data (depends on M5).
- `M7 (v0.8)` **Right-sized observability + scoring calibration loop** — calibration consumes M4's correction surface; dashboard + key alarms + documented SLOs.
- `M8 (v1.0.0)` **CI/CD hardening + polished README + architecture diagram + demo video + seam-ready stubs** → feature-complete single-user system.
- **Future (v1.x / v2.0):** Debezium CDC (batch→streaming), multi-user (v2.0), feedback hub, BI dashboard over marts, MWAA. Built if/when justified, each its own migration.
- **Ordering principles:** value-first · dependency-respecting · capability-arrives-when-justified · each migration = a coherent story chapter. **Semver (✅ confirmed):** v0.x per migration → **v1.0.0 at M8** → v1.x/v2.0 for future. Light CI from v0; CD hardening at M8.

**⚠️ ROADMAP IS DIRECTIONAL, NOT FIXED (Tarig's key refinement):** You can't draw the full roadmap before shipping — each stage's *implementation* is the bottleneck that reveals/unlocks the next capability. Therefore:
- **Commit firmly to only:** (1) **v0** (fully designed), (2) the **migratable architecture** (design *for* migration), (3) **release discipline**.
- **Roadmap M1→M8+ = a living hypothesis**, re-evaluated after EVERY release; shipping each stage reorders/replaces what's next.
- **Plan each migration just-in-time**, after shipping the prior. Don't over-plan later migrations now.
- **Refines "plan everything, then build" → "plan the foundation + current stage fully; plan each migration just-in-time."** (More honest + more defensible: "I designed for migration and let implementation learnings drive the sequence.")

**Migratability requirements (build v0 so migrations stay clean + observable):**
- Ports-&-adapters boundaries (add/swap sources, storage, notifier, scorer without rewrites).
- Config-driven **feature flags** (migration = enable + deploy).
- **First-class schema/data migrations:** Alembic (Postgres) + versioned S3 layout + dbt migrations; every release ships its migration.
- **Release discipline:** semver + git tag + CHANGELOG entry + **one ADR per migration** + UPGRADING note + migration script when data changes; backwards-compatible by default, breaking changes flagged.
- Additive **Terraform** modules → clean `plan` diffs per migration.
- **Migration tests:** data preserved + new capability works + old still works.
- Each release documents **before/after architecture diagram** + roll-forward/back.

**✅ v0 boundary resolved:** CV = M1 · email-only · single Lambda. **Roadmap optimized** (above) — pending Tarig's final confirm + semver-scheme confirm.

**Deliverable shift (refined for emergent roadmap):** The docs commit to:
- (a) the **migratable architecture + conventions** (permanent foundation),
- (b) **v0 in exhaustive detail** (the only fully-planned stage — apply-sequence with WHY/WAIT-FOR/FAILURE-MODE),
- (c) the **directional/living roadmap** + a **"how we decide the next migration" process** (signals/learnings that drive sequencing) — explicitly marked as hypothesis, not contract,
- (d) the **target end-state vision** (where it's heading, directionally).
Build = **ship v0 first**, then **design + ship each migration just-in-time** (re-plan after each release), with a **review gate at the doc stage AND before each migration release**. CLAUDE.md + ADRs + README per earlier. (Supersedes §6's "exhaustive P0→P8 upfront" — only v0 is exhaustively pre-planned.)

---

## 9. ⭐ GOVERNING PRINCIPLES (permanent — headline of the new CLAUDE.md / design-philosophy.md)

**P1 — Absolute minimalism.** Build the *minimal complexity that solves the present problem* — nothing for hypothetical futures. **Complexity is entropic: it accrues uninvited as the system grows**, so the default stance is *active resistance* to it; every addition must justify itself. Design cheap **seams** for the future; don't build the future. ("The minimal thing, the minimal complexity that solves my problem.")

**P2 — Bottleneck-driven evolution (the engine of the emergent roadmap).** After each stage ships: use it → identify the **top-3 bottlenecks** blocking the next *real* capability → rank by **leverage = capability unlocked ÷ complexity added** → solve the highest with the *minimal* migration → ship as a clean release → repeat. A migration must unlock a **true capability**, not just polish.

**P1 × P2 = one rule:** *add the minimum, and only to break the biggest real bottleneck → maximum capability per unit of complexity.*

**Migration decision protocol (ritual between releases):**
1. Ship stage → use + observe. 2. Surface top-3 bottlenecks to the next real capability. 3. Rank by leverage. 4. Design the *minimal* migration solving the top one. 5. Ship as a clean, labeled release (ADR: *bottleneck → capability unlocked → minimal solution*). 6. Repeat. → This is the real roadmap mechanism; the pre-drawn M1–M8 is only a hypothesis.

**Minimalism × portfolio reconciliation — ✅ DECIDED: TOOL-MINIMALISM WINS.**
- Only build what a real **tool** bottleneck justifies. Portfolio takes whatever the tool honestly produces. No building for signal alone.
- **Minimalism = the GATE; DE-depth = the TIEBREAKER** (when a bottleneck justifies a build and there are multiple ways, pick the more DE-signal-rich *minimal* option). DE-depth is no longer a license to add.
- **⚠️ SUPERSEDES earlier "locks": Snowflake is now CONDITIONAL, not planned.** §3-G4 / §4-analytical-plane / §8-M5 "Snowflake locked" → revised. Default analytics = **Postgres + dbt** (tiny data; Postgres suffices). A dedicated warehouse (Snowflake) is built ONLY if a real analytics bottleneck ever demands it.
- **DE-depth still fully served** (minimally + honestly): Postgres + **dbt modeling/marts/tests/lineage** (the flagged SQL/warehouse-modeling gap, on Postgres) + measured entity resolution + data contracts + the evolutionary-architecture story.
- Step Functions survives — it's *earned* by real Lambda complexity (M3), not a free pass.

---

## 10. Status: ✅ DESIGN LOCKED — ready to write docs

Tarig: "philosophy complete — lock & write docs." All major decisions captured (§3–§9). Governing principles set (§9). v0 locked; roadmap directional; warehouse conditional; tool-minimalism wins.

**Phase D deliverable (repo `/docs`, then STOP for review before building v0):**
1. `docs/design-philosophy.md` (or top of CLAUDE.md) — **P1 minimalism + P2 bottleneck-driven evolution + the defensibility rubric + tool-minimalism-wins** (the headline operating principles).
2. `docs/architecture.md` — two-plane model (**Postgres-default analytics; Snowflake conditional**), components, data flow, data model/ERD, dedup (cluster-and-surface), scoring, CV, diagrams.
3. `docs/roadmap.md` — **directional/living** roadmap + the **migration decision protocol** (top-3 bottlenecks → leverage → minimal migration) + target end-state vision. Explicitly hypothesis, not contract.
4. `docs/v0-build-plan.md` — **exhaustive** apply-sequence for v0 ONLY (WHY / WAIT-FOR / FAILURE-MODE per step).
5. `docs/adr/` — ADRs for the decided calls (region us-east-1, Postgres-over-DynamoDB, tool-minimalism/Snowflake-conditional, dedup model, CV renderer, self-hosted distribution, etc.).
6. `CLAUDE.md` (lean, principle-led), `README.md` + diagram, repo scaffold + Terraform skeleton + minimal CI.

Then: build & ship **v0**; afterward run the bottleneck protocol to choose the next migration. Review gate at doc stage AND before each release.

---

## 11. Methodology adoption (Tarig's Master Project Implementation Plan + Modern DE Best Practices) — right-sized

**Two pillars adopted wholesale:**
- **Documentation as infrastructure** — the repo is the memory; *any session resumes from the files alone* (= the context-survival requirement). **What / Why / So-what** on every doc; **placeholders are blockers**; **documentation is constructed, not described** (written live, from the field).
- **Four-layer pattern** for any project-wide standard: define once → inherit via template → enforce at gate → audit. + **Safety-first / Castle Principle:** build don't demolish · change-scope minimization · tag before risk · one change at a time · verify before+after · **document before delete** · **destructive ops require explicit approval**.

**ADOPT (memory-across-time value — cheap, high-leverage even solo):** ADRs **with rejected alternatives** (capture every decision from this session) · **error/incident log** (`ERR-NNN`: verbatim error + root cause + prevention + **Detection**; the Five Questions) · **interface contract ledger** (Produces→Consumes, one file) · **phase index** (⬜/🚧/✅) + **locked-decisions table** + **naming conventions** · **behavioral validation gates** (positive + negative; presence/liveness ≠ a gate) · small **procedure registry** · **fitness functions** for genuine invariants only · pre-commit + secret scan.

**RIGHT-SIZE:** per-phase docs collapsed to one project doc + short notes · chaos → a couple of targeted negative-injection tests (not the six-angle matrix) · meta-ADRs as short paragraphs · full DQ only where the data path warrants (dbt tests on Postgres marts: yes).

**ENFORCEMENT — ✅ EMERGENT (Tarig):** do NOT pre-decide the gate machinery. v0 process = the docs + manual discipline; **evaluate slash-commands vs Makefile vs checklists during implementation** and adopt what genuinely applies. (P1/P2 applied to the process itself — add machinery only when a real need justifies it.)

**CUT / label-as-deferred:** external PR reviewer as hard gate · `/audit-foundation` as standing automation (run ad hoc) · full templates library · full six-angle chaos. Each labeled "deferred → adopt when X", never silently dropped.

### Phase D deliverable — the `/docs` structure (methodology-aligned; captures THIS session for context survival)
Root: `CLAUDE.md` (lean orientation: identity, status, governing principles, navigation) · `README.md` (portfolio entry).
`docs/`:
- `00-design-philosophy.md` — P1 minimalism · P2 bottleneck-driven evolution · defensibility rubric · the two pillars · safety-first. (Headline.)
- `01-session-decision-journal.md` — **the full reasoning record of THIS session**: what we considered, chose, and rejected, and the live discussion. The context-survival core.
- `02-architecture.md` — two-plane (Postgres-default, Snowflake conditional) · data model/ERD · dedup cluster-and-surface · scoring · CV · diagrams.
- `03-roadmap.md` — directional/living roadmap + bottleneck-decision protocol + end-state vision.
- `04-v0-build-plan.md` — exhaustive v0 phase spec + validation gate (positive + negative).
- `05-methodology.md` — how we adopt/right-size/cut the Master Plan (labeled).
- `adr/` — one ADR per decision (region · Postgres-over-DynamoDB · tool-minimalism/Snowflake-conditional · dedup model · CV renderer · self-hosted distribution · batch-not-Debezium · evolutionary model · …).
- `ledgers/` — `phase-index.md` · `interface-contracts.md` · `procedure-registry.md` · `decisions-locked.md` · `errors.md`.

### ⚠️ Deletion scope — ✅ APPROVED: DELETE EVERYTHING (clean slate)
- Tarig: *"Delete everything in this project. I want to start clean fresh with nothing."*
- **DELETE all contents of `t:\Projects\jobfetcher\`:** `CLAUDE.md`, the PDF, `.lavish/`, `.agents/`, `.claude/` (incl. the 800-line write-hook + Notion perm allowlist), `skills-lock.json`. Totally clean tree.
- **Consequence (acknowledged):** loses project-level `.claude` hooks/permissions + skill config; re-establish only what's justified during setup.
- **Survives (outside the project):** this plan file (`C:\Users\V\.claude\plans\…`) + the memory dir → the session's thinking is NOT lost.
- Method: safety-first — list contents first, then delete. This is the one approved destructive op.
→ Open dial for Tarig: how many lens-3 "honest showcases" to allow (minimalist ↔ showcase-friendly).

---

## 12. Amendment — single user-configurable threshold (default 60)

**Context.** Tarig wants the threshold that decides CV writing to be **user-configurable and frequently changeable**, default **60%**. Decided (fork): **ONE threshold gates both the shortlist AND CV writing** — not two separate knobs. This supersedes the earlier `threshold = 75` / implicit two-gate model.

**Decision / approach.**
- A single `threshold` is a **config value** (default **60**) in the per-user config — the `profile` row (`profile.threshold`), same `user_id` seam as the other settings. It is **read at runtime** by the scorer/notifier (and `cv_tailor` from M1+). Changing it = editing one value; the next run obeys it. **No redeploy** — config is data, not code.
- It gates **both** the daily shortlist *and* CV generation (in v0, only the email shortlist exists; CV gating joins at M1).
- `hard_floor` (default **50**) and `near_miss_band` (default **10**) remain as the watch/honesty band *below* the threshold (floor must stay < threshold).
- **Measurement:** stamp the *active* threshold onto each run's records (correlation), so its effect on CV/shortlist volume is queryable later.
- **Editing surface (emergent):** v0 = the config value (config file / `profile` row); a nicer control (Notion settings / CLI) is a later, justified migration — not built now.

**Files to update (out of plan mode):**
- `docs/02-architecture.md` — Scoring section: single config `threshold` (default 60) gating shortlist + CV; floor 50 / near-miss 10.
- `docs/04-v0-build-plan.md` — replace 75/55/10 with **60/50/10**; add a VG: *changing the config threshold changes which jobs surface, with no code change/redeploy* (behavioral).
- `docs/ledgers/decisions-locked.md` — amend the thresholds row (75/55/10 → single threshold 60, floor 50, near-miss 10; one gate).
- `docs/01-session-decision-journal.md` — one-line amendment noting the change + why. (No new ADR — a config-default + single-gate clarification; recorded in the ledger + journal.)

**Verification (docs-only now; behavioral test lands with the build):** the scoring docs read "one config threshold, default 60, gates shortlist + CV, runtime-editable"; the locked-decisions row is updated; v0 plan uses 60/50/10. At build time, VG passes when editing the config value (not code) changes which jobs appear in the next run.

---

## 13. Ingestion-layer capture (doc edits from the ingestion think-out-loud)

**Context.** A long design discussion settled the ingestion layer, which the docs had hand-waved. Decisions to capture (repo-is-memory; constructed live):
- **Medallion landing:** bronze (land *all* raw, immutable: S3 `raw/` + a thin `bronze_posting` table) → silver (normalize to common schema + dedup) → gold (cheap, deterministic **profile filter** to candidates) → score (Bedrock on gold only). Below-bar rows stay in bronze/silver for analytics.
- **Immutable bronze ⇒ replay:** silver/gold/score are pure functions over bronze; change a filter or the profile and reprocess history with **zero new API calls**. Bronze is precious + append-only; silver/gold are disposable/rebuildable.
- **Quota/request-budget model:** charged per *request* (~10 jobs/page), not per job; the query (keywords + `country` + `date_posted`) is the source-side pre-filter that controls spend; bound pages/query; `requests/run = queries × pages × sources ≤ quota ÷ 30`.
- **Source = JSearch (probe-free→Pro).** JSearch rides Google-for-Jobs (covers major boards; supports GCC via `country=sa/ae/qa/om`). v0 = **single source** (no cross-source dedup; exact-id only). First build step **probes the free 200-req tier** on real Riyadh/GCC DE queries → confirm depth + full JD text → then subscribe **Pro ($25/mo)**. Adzuna **deferred** (candidate later source if a coverage gap appears; it returns truncated JDs).

**Files to update (out of plan mode):**
- `docs/02-architecture.md` — add an **"Ingestion — medallion landing"** subsection to the operational plane (bronze→silver→gold→score + immutable-bronze-replay); clarify the **operational medallion** (ingestion path) vs the **analytical medallion** (dbt marts); add a `bronze_posting` raw-landing table to the data model; note gold = profile-filtered candidate subset.
- `docs/adr/0010-job-source-jsearch.md` — **NEW ADR**: JSearch as the source (probe-free→Pro; single-source v0); rejected alternatives = Adzuna-only (coverage + truncated JDs), both-from-v0 (cost + premature cross-source dedup), JSearch-Pro-blind (pay before evidence). + update `docs/adr/README.md` index.
- `docs/ledgers/decisions-locked.md` — add rows: source=JSearch (probe→Pro, single-source v0, Adzuna deferred); ingestion medallion + immutable-bronze-replay; quota/request-budget model.
- `docs/04-v0-build-plan.md` — resolve **D-v0-2 → JSearch**; add **Step 0: free-tier coverage probe** (run real `country=sa/ae` DE queries on JSearch free 200-req; confirm depth + full JD; then subscribe Pro); reframe Step 4 as the **JSearch adapter + bronze landing**; state single-source ⇒ exact-id dedup only in v0; add request-budget + page-cap + date-window as config; the gold profile-filter as a v0 step before scoring.
- `docs/03-roadmap.md` (minor) — note multi-source + cross-source dedup stays **M2**; Adzuna is its candidate second source.

**Verification (docs-only):** 02 has the medallion-landing subsection + `bronze_posting`; ADR-0010 exists and is indexed; decisions-locked has the source + medallion rows; v0 build plan has the probe Step 0 + JSearch adapter + gold-filter step. Commit the capture.

---

## 14. Silver + dimensional-model capture (doc edits)

**Context.** The schema/transform/dimensional design discussion settled the silver layer and the analytical (dimensional) model. Capture into docs (repo-is-memory).

**Decisions to capture:**
- **JSearch response schema (bronze raw shape):** identity (`job_id`, `job_title`, `job_publisher`), employer (`employer_name/website/logo/company_type`), apply (`job_apply_link`, **`apply_options[]`**), **text (`job_description`, `job_highlights{Qualifications/Responsibilities/Benefits}`)**, employment (`job_employment_type(s)`, `job_is_remote`), location (`job_location/city/state/country/lat/long`), time (`job_posted_at_timestamp/datetime_utc`, expiration), salary (`min/max/period/currency` — often null in GCC), misc (`job_google_link`, `job_onet_*`). Exact set **pinned from the Step-0 probe** response.
- **Silver text pipeline (pure, versioned, ordered):** whitelist → clean (strip html/entities, normalize unicode+whitespace) → language-detect (English filter) → segment (opt; prefer `job_highlights`) → fingerprint → embed (pgvector). Mostly on `job_description`/`job_title`; rest of silver is field-mapping.
- **Origin-level lineage:** each silver record carries `bronze_id` + `pipeline_version`; field→source mapping is a documented constant (e.g. `posting.description ← raw.job_description`); immutable bronze + pure/versioned transforms ⇒ trace-to-origin + exact re-derive (replay).
- **Principle — never-discard → dimensional modeling:** retain everything (bronze lossless); **model into dimensions what compounds; decompose by *insight*, not by *field*; grow a dimension when a real question needs it** (retroactively over history via bronze replay). Avoids table-per-field sprawl.
- **Target analytical model (constellation, conformed dims):**
  - **Facts:** `fct_job_posting` (grain: posting/cluster) · **`fct_job_skill`** (bridge: posting × skill) · `fct_job_score` (posting × scoring-run) · `fct_application`.
  - **Dims:** `dim_date` · `dim_skill` · `dim_title` (raw→canonical+variants) · `dim_company` · `dim_sector` · `dim_location`; **profile as point-in-time** (SCD2/snapshot) for trends.
  - **Skills + canonical title are derived from the JD text** (LLM extraction + normalization) — the highest-value, hardest text pipeline; unifies the text pipeline and the model.
- **Priority order (Tarig: skill-demand/gaps · my-progress trends · sector intel):** `dim_skill` + `fct_job_skill` **first** (linchpin — powers skill-demand/gaps + sector) → point-in-time profile + `dim_date` + `fct_job_score`/`fct_application` (trends) → `dim_sector`. `dim_title`/`dim_company` supporting; company-intel lower priority.
- **Timing:** built at the analytics migration (**M5/M6**); v0/silver retains fields; bronze enables retroactive modeling.

**Files to edit:**
- `docs/02-architecture.md` — (a) add the **JSearch source schema** to the Ingestion section; (b) add the **silver text-pipeline + origin-level lineage**; (c) expand **Analytical plane — dbt marts** into the **constellation model** (facts + conformed dims + `fct_job_skill` bridge + point-in-time profile) with the **priority order**, "built at M5, grown by question", and the skills/title-derived-from-text note.
- `docs/adr/0011-dimensional-analytical-model.md` — **NEW ADR**: insight-driven dimensional/constellation model; decompose-by-insight; grow-per-question. Rejected: table-per-field sprawl · one-big-table · defer-all-design. + index in `adr/README.md`.
- `docs/ledgers/decisions-locked.md` — rows: silver text-pipeline + origin-level lineage; never-discard/dimensional principle; target model + priority order.
- `docs/00-design-philosophy.md` — short corollary under the principles: *never-discard; decompose-by-insight; model dims when a question justifies (grow per question)*.
- `docs/04-v0-build-plan.md` (minor) — silver retains all fields for future dims; no marts in v0.
- `docs/03-roadmap.md` (minor) — M5/M6: note the priority dimension order (skill bridge first).

**Verification (docs-only):** 02 carries the source schema + silver pipeline/lineage + the constellation model with priority order; ADR-0011 exists + indexed; decisions-locked + 00-philosophy carry the principle; v0 + roadmap notes added. Commit.

---

## 15. Diagrams — Mermaid-only in the repo (+ Eraser as personal aid)

**Context.** Compared Eraser (manual DSL) vs Mermaid vs Excalidraw/draw.io. **Decision: repo diagrams = Mermaid only** — text → GitHub-renders inline next to the decisions, versioned, never drifts, no binary/size bloat in git history. **Eraser stays as Tarig's personal visual aid** (authored via the free manual DSL path, viewed in Eraser's platform; link shareable later for the portfolio) — **not committed** to the repo. Excalidraw/draw.io dropped (manual-draw → can't keep current → would rot, violating "docs constructed live").

**Build:**
- New **`docs/diagrams.md`** — the visual index, canonical Mermaid diagrams:
  1. **Full-stack architecture** (External · Operational plane + medallion · Analytical plane) — the one already rendered for Tarig.
  2. **Roadmap / status** flow — v0 → M1…M8 with ✅/🚧/⬜ + the bottleneck-decision loop (so progress + next-bottleneck are visible).
  3. **Constellation / dimensional model** (facts + conformed dims + the `fct_job_skill` bridge) — DE-depth centerpiece, currently text-only.
  - Each diagram followed by a one-line "discussed in: [doc]" pointer.
- Link `docs/diagrams.md` from `CLAUDE.md` (docs map), `README.md`, and `docs/02-architecture.md`.
- Record the convention (one line) in `CLAUDE.md` + `decisions-locked`: *repo diagrams = Mermaid (canonical, in-repo); Eraser = optional personal/portfolio view, not committed.*
- (02-architecture already embeds the operational flowchart + ERD inline — keep; `diagrams.md` is the consolidated index.)

**Verification (docs-only):** `docs/diagrams.md` holds 3 valid Mermaid diagrams (render-check via GitHub preview / mermaid.live); links resolve; convention recorded. Commit.

---

## 16. Confirm Anthropic Bedrock works (inference-profile fix) + record AWS facts

**Context.** Investigation (read-only) found the "can't use Anthropic models" blocker is the **model-ID format**, *not* root/IAM: in account `198592435375` / `us-east-1`, the Claude models are **inference-profile-only** — the base ID (`anthropic.claude-…`) is rejected with a ValidationException, but the **`us.anthropic.…` cross-region inference-profile ID works**. Root authenticates and reaches Bedrock fine. **Tarig's call:** keep root for the dev phase (a non-root identity is *not* a bottleneck — defer to the hardening pass); prove the fix now. (`samareltayeb` = JobFetcher's dedicated profile; **us-east-1** = project region — both confirmed.)

**Actions (on approval):**
1. **Test-invoke to prove the fix** — `aws bedrock-runtime converse --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --messages '[{"role":"user","content":[{"text":"reply OK"}]}]' --inference-config '{"maxTokens":5}' --profile samareltayeb --region us-east-1`. Cheapest model, ~1 token (~$0). Expect a valid completion → Anthropic confirmed. (Optionally show the base-ID ValidationException for contrast.)
2. **Record AWS facts in docs:**
   - Confirm `samareltayeb` dedicated profile + `us-east-1` in [decisions-locked](docs/ledgers/decisions-locked.md) (region already in [ADR-0008](docs/adr/0008-region-us-east-1.md)).
   - **Inference-profile-ID requirement (config gotcha):** scorer + cv_tailor must call **`us.anthropic.*` inference-profile IDs**, never base model IDs → add to [ADR-0008](docs/adr/0008-region-us-east-1.md) + [04-v0-build-plan](docs/04-v0-build-plan.md) Step 5 (scorer) + the error log as a known gotcha. Default models: `us.anthropic.claude-sonnet-4-6` (scoring quality) / `us.anthropic.claude-haiku-4-5-20251001-v1:0` (cheap ops). Confirm exact picks at build.
   - **Deferred security step:** create a non-root IAM operator identity + least-privilege runtime roles **before production/portfolio-ready** (the M8-ish hardening; runtime roles come with Terraform regardless). Root used during dev (solo personal account) — keep keys safe. Record in decisions-locked / roadmap as deferred.
3. **Commit + push** the doc updates.

**Verification:** the `converse` call returns a completion from `us.anthropic.…`; docs carry the inference-profile gotcha + the deferred-IAM note; changes pushed to GitHub.

---

## 17. Model-agnostic LLM + Kimi K2 (in-flight — to formalize in docs)

**Context.** Anthropic is blocked by the new-account 0/non-adjustable daily-token quota (ERR-001). Tarig: *"Kimi K2 Thinking on Bedrock works fine — let's use it instead, and make sure switching models is never a bottleneck."* The LLM job is narrow: **analyze big text chunks → dissect → categorize** (scoring, skill/section extraction). No advanced needs.

**✅ Read-only verification (account `198592435375`, us-east-1):**
- `moonshotai.kimi-k2.5` and `moonshot.kimi-k2-thinking` are **ACTIVE** and **`ON_DEMAND`** — so, unlike Anthropic 4.x, **no inference-profile gotcha** (invoke the base model id directly). Good for simplicity.
- **BUT** their *"Model invocation max tokens per day"* quota also shows **`0.0` and `Adjustable=False`** — the *same* new-account wall. So the quota gate looks **account-wide, not Anthropic-specific.**
- ⚠️ **Open contradiction:** Tarig says Kimi "works fine," but the API daily quota reads 0. Likely paths: Bedrock **console playground** (separate limits) vs the **API** (subject to the 0 quota). **Unresolved — needs a 1-token Kimi `converse` invoke to confirm** (couldn't run it: plan mode). If the API invoke succeeds → the 0 is misleading / Kimi is exempt; if it throttles → switching models does NOT bypass the wall and we're still gated on account maturity (ERR-001).

**✅ Decision that IS resolved (do this regardless of the quota outcome): model-agnostic LLM.**
- One **`LlmClient` port** over Bedrock's **Converse API** (unified request/response across providers → model-swap is config, not code).
- **Model id(s) live in config**, ideally **per task** (`llm.scoring_model`, `llm.extraction_model`, …) so we can use a strong model for scoring and a cheap one for high-volume extraction, and swap any of them by editing config.
- **Structured output via prompt + Pydantic validation** (portable across models), not provider-specific JSON modes — keeps it model-agnostic.
- Current candidate: `moonshot.kimi-k2-thinking` (works for Tarig, ON_DEMAND); revert to `us.anthropic.claude-sonnet-4-6` when the account quota lifts. Either is a one-line config change.

**To formalize in docs (proposed — pending exit from plan mode):**
- **NEW `docs/adr/0012-model-agnostic-llm.md`** — provider-agnostic LLM via Bedrock Converse; model id in config (per task); prompt-based structured output; rejected: hardcoding one provider / provider-specific SDKs. + index in `adr/README.md`.
- **Update `02-architecture.md`** scoring/CV sections: "Bedrock (model-agnostic via Converse; model id in config; current candidate Kimi K2, Claude when quota lifts)."
- **Update `ERR-001`** (errors.md): add the Kimi finding — Kimi K2 ACTIVE + ON_DEMAND but daily quota also 0/non-adjustable → the new-account gate is account-wide; **open item: verify via a Kimi API invoke.**
- **Update `decisions-locked` + `04-v0-build-plan`** (Step 5 scorer): model id is config-driven (Converse); current candidate Kimi K2 Thinking.
- **OPEN, post-compaction:** run the 1-token Kimi `converse` test to resolve the works-fine-vs-0-quota contradiction.

---

## 18. AWS default profile = samareltayeb, us-east-1 (keyless)

**Context.** Tarig wants the **`samareltayeb`** account (`198592435375`) to be the **default** AWS identity at region **us-east-1**. He noticed no access key/secret and thought we needed to create them — but samareltayeb already authenticates via a **session login** (`login_session` + `~/.aws/sso|login/cache`), *not* static keys. **No keys to create**, and **root access keys are a hard no** (AWS's #1 security anti-pattern; a leak = total account takeover). The proper non-root IAM identity stays the deferred hardening item (§16 / ERR-001). ✅ Chosen: **keyless default.**

**Approach (lowest-risk; uses the proven-working profile; no edits to the open config file required):**
1. **Back up** `~/.aws/config` + `~/.aws/credentials` → timestamped `.bak` (Castle Principle — critical files, and `config` is open in the IDE).
2. **Set persistent *user* env vars** (PowerShell `[Environment]::SetEnvironmentVariable(name, value, 'User')`): `AWS_PROFILE=samareltayeb`, `AWS_DEFAULT_REGION=us-east-1`. → makes samareltayeb the default for all future shells/tools via the proven profile; **overrides the dead `[default]`/`[orderflow]` keys without touching them.**
3. **Verify:** read the vars back (`GetEnvironmentVariable(...,'User')`) + confirm `AWS_PROFILE=samareltayeb aws sts get-caller-identity` → `arn:aws:iam::198592435375:root`. (Note: a *brand-new* terminal is needed for the persistent default to take effect — already-running processes won't see it.)
4. **Optional tidy (only on Tarig's OK):** strip the dead `[default]` block from `~/.aws/credentials` via a script that removes the section *without printing secrets*; **leave `[orderflow]`** (separate project — won't delete another project's profile). If `config` is edited, Tarig reloads it in the IDE to avoid a save-conflict.
5. **Not doing:** create access keys · create root keys · create the non-root IAM identity (deferred).

**Verification:** a fresh terminal's `aws sts get-caller-identity` (no `--profile`) returns the samareltayeb root ARN at region us-east-1. **Reversible:** unset the env vars / restore `.bak`.

**Doc capture (after):** one line in `decisions-locked` — *default AWS profile = samareltayeb (session login, keyless), region us-east-1; no static/root keys; non-root IAM deferred.* ✅ **DONE** (row added, commit 8347334).

### 18b — Document the AWS authentication model (Tarig asked "no key/secret — how does auth work?", chose: document it)
**Context.** Verified via `aws configure list --profile samareltayeb`: a key+secret *do* exist but their **source is `login`** (TYPE `login`, not a credentials file) — i.e. **temporary STS session credentials** (access key + secret + session token) issued by Tarig's sign-in, cached in `~/.aws/login/cache/` (refreshed today, Jun 17 12:10). So "no static keys" ≠ "no auth" — it's the *temporary-credentials* model, the secure norm. Two-tier model to capture so the repo explains how JobFetcher authenticates with **zero static keys anywhere**:
- **Local (Tarig — CLI/Terraform):** session login → temporary STS creds; auto-used by every CLI/SDK call; **re-sign-in when they expire**. Nothing permanent on disk.
- **Production (deployed Lambdas):** **IAM execution roles** — AWS injects temporary role creds into each function at runtime; zero keys, zero config, nothing to expire/re-login. (Terraform already provisions the least-privilege Lambda role — build-plan Step 3 / line 52.)
- **Key reassurance:** an expired *local* session never affects the *deployed* pipeline (that's IAM roles). Third-party API keys (JSearch) are a separate concern → Secrets Manager (unchanged).

**Edits (out of plan mode — 2 touches; no new ADR — this explains *how* the already-locked keyless decision works, not a new decision):**
1. `docs/04-v0-build-plan.md` — **Prerequisites:** add an item *"AWS authentication (no static keys)"* stating the two-tier model (local session-login temporary creds, re-sign-in on expiry; runtime = Lambda IAM execution roles via Terraform). The "how do I actually run this" context a builder/cloner needs.
2. `docs/ledgers/decisions-locked.md` — **Security, cost, infra** section: add a row capturing the complete AWS-auth model (no static keys; local = session-login temp creds; runtime = IAM execution roles), owner `journal §18`.

**Verification (docs-only):** build-plan Prerequisites carries the auth-model item; decisions-locked Security section has the AWS-auth row; both read consistently with the §18 keyless row. Then commit + push.

### 19 — Make `jobfetcher-dev` the identity for ALL development (codify standard + close loose ends)
**Context.** Tarig: *"make sure all development is under the user of jobfetcher."* The credential layer is already switched **and verified** this session: `AWS_PROFILE=jobfetcher` (persistent, us-east-1), `[default]` mirrors the same key, and all paths (`default` / `jobfetcher` / `AWS_PROFILE` / new-shell) resolve to `arn:…:user/jobfetcher-dev`; root reachable only via explicit `--profile samareltayeb`. Repo is **docs-only** — no Terraform/CI/scripts pin any other identity, so nothing in-repo to fix. Remaining work is (a) **codify** it as a durable standard, (b) emphasize the **human-vs-machine boundary**, (c) the **restart** that closes the one live gap (open shells/VS Code still carry the old `samareltayeb` value → root until restarted).

**Edits (out of plan mode):**
1. **`CLAUDE.md`** — add ONE operating rule under *How Claude works here* (it currently has no AWS-identity line): *"AWS dev identity: all local development uses the non-root **`jobfetcher-dev`** IAM user (profile `jobfetcher`, also the `[default]`); the keyless **root** session (`samareltayeb`) is for rare root-only ops only; **CI/CD and Lambda runtime use their own IAM roles — never the personal key.** Full model in [decisions-locked]."* This is the always-in-context durable standard.
2. **`docs/ledgers/decisions-locked.md`** — light touch only if needed: ensure the human-vs-machine boundary (CI/runtime use own roles, never the personal key) is explicit; the default-identity (line 16), IAM-user (line 19), and auth-model (Security section) rows already cover most of it.
3. Commit + push.

**User-side verification (cannot be done from the shell — Tarig's actions):**
- **Reload/restart VS Code** (Command Palette → *Developer: Reload Window*, or full restart) so integrated terminals + the AWS Toolkit extension inherit `AWS_PROFILE=jobfetcher`.
- In a **fresh terminal**: `aws sts get-caller-identity` (no flags) → must show `…:user/jobfetcher-dev` (NOT `:root`).
- AWS Toolkit extension shows connected as the **`jobfetcher`** profile.
- *Out of scope (noted, not changed):* Amazon Q sign-in is separate (Builder ID / IAM Identity Center) and cannot use the IAM access key for its AI features; least-privilege tightening of `jobfetcher-dev` stays a deferred hardening item.

**Verification:** `CLAUDE.md` carries the dev-identity rule; a fresh terminal resolves to `jobfetcher-dev`; the human/machine boundary is documented; changes pushed.

**✅ DONE (all confirmed):** `CLAUDE.md` rule committed (`f643bc9`). Post-restart verification: default CLI resolution (no flags) → `…:user/jobfetcher-dev`; with `AWS_PROFILE` unset → still `jobfetcher-dev` (the `[default]` mirror). AWS Toolkit extension status bar shows **`AWS: profile:jobfetcher`** (screenshot-confirmed by Tarig). Every dev surface is on `jobfetcher-dev`. Amazon Q sign-in = separate, intentionally out of scope.

---

## 20 — JSearch query strategy + request budget (free-tier backfill probe)

**Context.** First concrete ingestion decision: how we query JSearch and how that fits the quota. Decided interactively this session. Principle: **free tier = learn the strategy; Pro = run it.** ([ADR-0010](docs/adr/0010-job-source-jsearch.md), [build-plan Step 0](docs/04-v0-build-plan.md).)

**Decided strategy (Tarig's picks):**
- **Matrix = 3 core titles × 6 GCC countries = 18 base queries.** Titles: `Data Engineer`, `Data Platform Engineer`, `Data Architect`. Countries: `sa, ae, qa, kw, bh, om`.
- **Window = 30 days (`date_posted=month`)** — explicitly the **backfill** window (not the daily window).
- **Mode = backfill only for now** — run the 30-day all-GCC sweep as the free probe, inspect the real data together, **then** decide the daily-incremental window + Pro upgrade.
- **Filters:** on-site oriented → `remote` off; `employment_types` / publisher-excludes deferred until probe shows the noise.

**Budget:**
- Free **Basic = 200 req/mo** (confirmed). One 30-day all-GCC sweep ≈ **40–70 requests** (18 queries × ~1–8 pages; deep for `sa`/`ae`, ~1 page for small markets) → **fits free**, room for 2–3 sweeps. *This is the probe.*
- Daily incremental (today/3days, all-GCC ≈ 18/run → ~540/mo) → **Pro (10k/mo, $25)** — deferred until after the probe.
- **Confirm at probe:** does `num_pages=N` bill as N requests or 1? `/search` pagination-depth cap vs `/search-v2` cursor.

**Probe runbook — the 5 metrics measured on free:** (1) **coverage** (relevant DE postings/day per country); (2) **JD completeness** (full `job_description` present? truncation = disqualifier); (3) **query precision** (best title/location phrasing per request); (4) **dedup reality** (`apply_options` pre-merge count; do reposts reuse `job_id`? → validates "exact-id dedup is enough for v0"); (5) **depth** (pages/query).

**Edits (out of plan mode):**
1. `docs/adr/0010-job-source-jsearch.md` — addendum: the concrete matrix (3×6=18, 30-day backfill), the budget split (backfill fits free / daily → Pro), `num_pages`-billing as a probe-confirm item.
2. `docs/04-v0-build-plan.md` Step 0 — concretize the probe: the 18-query matrix, params, the 5 metrics, the request cap.
3. Add a **`search_config` sketch** (titles, countries, `date_posted`, page-cap) — the matrix as config, not code.
4. *(Optional, ready-to-run)* `scripts/jsearch_probe.py` — runs the matrix against the free key (key from env/secret, hard request-cap), dumps raw JSON to inspect + prints the 5 metrics. **Never commits the key.**

**Gating prereq (Tarig):** register RapidAPI → subscribe **JSearch Basic (free)** → hand over the key (stored safely via the jobfetcher AWS Secrets Manager / local env, never committed).

**Verification:** the probe runs against the free key, returns the 5 metrics within the request cap (≤ ~70); the numbers decide the daily window + Pro timing.

---

## 21 — Search targeting as a validated, fully-explicit user spec

**Context.** Tarig: the search parameters (country, job title, city/location, state) must be **user-provided inputs**, asked of the user *before* any query runs — *"nothing taken for granted as input."* This formalizes a per-user **search spec** (same multi-user seam as `PROFILE.user_id` + `threshold`) that drives the query fan-out, the gold-filter target sets, and the per-user geo scope that flows into analytics/downstream. Decided interactively (3 forks): **state = keep as an optional gold-filter; strictness = everything explicit (no silent defaults); city = pull-by-country then filter cities in gold.** Deps confirmed present: `pydantic 2.11.7`, `pyyaml 6.0.3`.

**The model — `SearchSpec` (Pydantic data contract; every field required, validated):**
- **Targeting (the four fields):** `job_titles[]` → the `query` text · `countries[]` ISO-3166 alpha-2 → the `country` param (the reliable geo scope) · `cities[]` → **gold filter on `job_city`** · `states[]` → **gold filter on `job_state`** (kept for completeness / non-GCC; usually null for GCC).
- **Knobs (also explicit — no defaults):** `date_posted` (enum all/today/3days/week/month), `language`, `employment_types[]`, `remote` (off/include/only), `threshold`.
- **Budget:** `max_pages_per_query`, `request_budget_per_run`.
- **Validation = the "nothing assumed" gate:** every field must be present (no Pydantic defaults); `job_titles`/`countries` non-empty; `countries` valid ISO2; enums checked; `from_yaml()` **fails loudly** on any missing/invalid field. `cities`/`states`/`employment_types` may be an explicit `[]` (= "no extra filter") but must be present.

**Field → JSearch mechanism (honest):** country = `country` param · title = `query` text · **city + state = post-pull gold filters** (pull broadly by country, then filter to target cities/states — cheaper, and every city still lands in bronze for analytics).

**Intake (the seam):** v0 = the user fills `config/search_config.local.yml` (gitignored), **validated by `SearchSpec` at load** — incomplete/invalid → loud failure (this *is* the "ask the user to fill it first" enforcement). Committed `search_config.sample.yml` = the complete template (Tarig's current values pre-filled). Future multi-user = a form/Notion/CLI writing the same schema per `user_id`; schema unchanged.

**Downstream:** `countries`/`cities` = per-user geo scope → `country_queried` stamped on bronze/silver, matched in gold → `dim_location` + analytics inherently per-user-scoped; `job_titles` → query + gold title-match + `dim_title`. Multi-user = per-`user_id` spec → its own pulls/filters/analytics.

**Edits (out of plan mode):**
1. **`scripts/search_spec.py`** (new) — Pydantic `SearchSpec` (all-required + validators) + `from_yaml()` loader (loud failure). *Migrates to `src/jobfetcher/core/` at build Step 1.*
2. **`config/search_config.sample.yml`** — reshape into the complete spec template: `targeting` (titles/countries/cities/states) + knobs + budget, **every field present + commented with its JSearch mechanism**.
3. **`scripts/jsearch_probe.py`** — load + validate the spec (drop the hardcoded constants); build the matrix from `spec.job_titles × spec.countries`; pass knobs to `fetch`; carry `cities`/`states` as the gold-filter targets (applied in gold, not the probe). Reads `.local.yml` if present, else the sample.
4. **`docs/02-architecture.md`** — new subsection *"User search spec (input contract)"*: schema, all-explicit principle, field→mechanism mapping, the intake seam, downstream propagation; note the gold-filter targets come from the spec.
5. **`docs/ledgers/decisions-locked.md`** + **`procedure-registry.md`** — a row for the validated fully-explicit search spec (country=param / title=query-text / city+state=gold-filter; intake config→form); name `SearchSpec` under the data-contract procedure.

**Verification:** `SearchSpec.from_yaml('config/search_config.sample.yml')` loads clean; deleting any field → loud validation error (the gate); the probe rebuilds the same 18-query matrix from the spec; `--dry-run` honors the budget. Then (separately) run the full sweep.

---

## 22 — Reconcile the v0 build plan's last Anthropic-Claude references to the chosen Kimi model (small)

**Context.** Tarig asked "do we have a v0 implementation plan?" — **yes**, [`docs/04-v0-build-plan.md`](docs/04-v0-build-plan.md) is the exhaustive apply-sequence (scope lock + 5 prereqs + 2 sub-decisions + Steps 0–10 each with WHY/WAIT-FOR/FAILURE-MODE + validation gate VG1–8 + cost + completion ritual). Step 0 (probe) is in progress; the rest are designed-not-started; scoring is gated on the ERR-001 quota. Reading it surfaced **two Anthropic-Claude-specific spots the earlier exact-phrase grep missed** — they should reconcile to the chosen Kimi model (`moonshot.kimi-k2-thinking`, ON_DEMAND), preserving the real ERR-001 quota context.

**Edits (out of plan mode):**
1. **`docs/04-v0-build-plan.md` Prerequisite 1** — reframe "Bedrock-ready for **Anthropic Claude**" → "Bedrock-ready for the **chosen model** (Kimi K2 Thinking, `moonshot.kimi-k2-thinking`, ON_DEMAND)": (a) **model access** for the moonshot model; (b) account-wide **daily-token quota > 0** — the real gate (ERR-001); **note** the `us.anthropic.*` inference-profile requirement is **Anthropic-4.x-only** — Kimi is ON_DEMAND and needs no inference profile. `WAIT-FOR` = a 1-token Kimi `converse` returns a completion (today: still throttles → quota gate).
2. **`docs/04-v0-build-plan.md` cost estimate (line ~129)** — "Bedrock (… Claude) ~$3–8" → Kimi K2 Thinking (note: reasoning model → more tokens; swap a cheaper model for high-volume steps later, per ADR-0012).

**Not changing:** Step 5 already names Kimi (done); the plan's structure/steps are complete and correct — this is purely a model-reference reconciliation, not a re-plan.

**Verification:** a re-scan of `04-v0-build-plan.md` for `Anthropic Claude` / `(… Claude)` returns clean; Prereq 1 reads accurately for the Kimi/ON_DEMAND reality while keeping the ERR-001 quota gate as the WAIT-FOR.

---

## 23 — Adopt the gate-trio enforcement + branch/PR workflow (from the Master Plan)

**Context.** Tarig reviewed his **Master Project Implementation Plan** (Notion) and chose the delta to pull in now that we're crossing design → build: **(a) the full gate trio as Claude Code slash-commands**, and **(b) branch + PR + protected `main`** for v0 code. This *resolves* the previously-"emergent" enforcement-machinery decision ([05-methodology](docs/05-methodology.md) + CLAUDE.md "What NOT to do") — the trigger (building, inside Claude Code where a command is near-free) is now met. We've already adopted the Master Plan's core (ledgers, ADRs, behavioral-gate standard = VG1–8); this adds the *enforcement surface* + the two human checkpoints, **still right-sized** (the 3 gates, not the full 10-command catalog; self-review, not an external-reviewer hard gate; six-angle chaos stays cut).

**Deliverables (out of plan mode):**
1. **Gate-trio slash-commands** in `.claude/commands/` (committed — they're a portfolio signal), authored from the Master Plan's command template (Purpose · When · Steps reporting PASS/FAIL/SKIP · Allowed mutations · Output table), adapted to *our* build (the build-unit = a v0 step or a migration; ledgers = errors / interface-contracts / procedure-registry / phase-index; gate = VG1–8; behavioral + negative-case enforced):
   - **`/start-step`** (ENTRY) — prereqs closed · scope/spec clear (no `[TO BE FILLED]`) · deferred procedures authored · every validation criterion *strong* (behavioral + negative) · ensures the work is on a branch · sets phase-index 🚧.
   - **`/review-step`** (CODE) — `ruff`/static · secret scan · unit tests · **every negative case actually executed** · no hardcoded config/secrets.
   - **`/close-step`** (EXIT) — docs updated · ADRs present · validation gate run positive **and** negative · no open `ERR-NNN` · interface-contract *Consumes* verified vs upstream *Produces* + *Produces* appended · phase-index ✅ · ready to PR/tag.
2. **Branch + PR + branch-protection.** v0 *code* builds on a per-migration branch → self-reviewed PR + CI → tag the release (`main` never committed directly for code; docs may stay direct for speed or move too — decide at execution). **Protect `main`** (PR-only + required status checks) via `gh api` (or the GitHub UI if `gh` isn't admin-authed).
3. **Two human checkpoints** (formalized): **A** = spec/plan approved before code (= plan mode); **B** = approval before the irreversible merge/tag.
4. **[ADR-0013] — enforcement machinery + dev workflow:** gate-trio-as-skills + branch/PR/protected-main; rejected alternatives = manual checklists only · lean 1–2 skills · direct-to-main. + index in `adr/README.md`.
5. **Doc reconciliation:** `05-methodology.md` (flip "Enforcement is emergent" → **adopted**: gate trio + branch/PR + the 2 checkpoints; add the *weak-vs-robust* examples table — `Container running` → `INSERT then SELECT, value matches`); `CLAUDE.md` (replace the "don't pre-decide gate machinery" item → the now-decided workflow; add branch/PR to the operating rules); `procedure-registry.md` (the 3 commands now **Written**); `phase-index` legend note (entry/exit gates set 🚧/✅).

**Verification:** the 3 commands exist + invoke (`/start-step`, `/review-step`, `/close-step`); a dry `/close-step` on a trivial step prints the gate table (PASS/FAIL/SKIP) and refuses to pass if a negative case is missing (behavioral-gate standard self-applied); `gh api .../branches/main/protection` shows PR-only + required checks; `05-methodology` + `CLAUDE.md` + ADR-0013 read consistently; nothing committed directly to `main` for code thereafter.

**Not adopting (still cut, still right for solo scale):** the full 10-command catalog (scaffolders `/new-error`, `/write-adr`, `/audit-foundation` are optional follow-ons), per-phase doc ceremony, the six-angle chaos matrix, the external-reviewer hard gate.

---

## 24 — Re-verify Kimi via the bedrock-runtime API (settle the stale ERR-001)

**Context.** Tarig believes Kimi works (recent manual check); our "blocked" verdict rests on a **2026-06-17 API test (≈6 days old)**. The new-account Bedrock daily-token quota lifts over days → ~2 weeks, so that data may be **stale**. Console playground ≠ `bedrock-runtime` API (separate quotas). Re-run the exact pipeline call to settle it definitively.

**Action (out of plan mode):**
1. Run the 1-token API test (the pipeline path), default `jobfetcher` profile / us-east-1:
   `aws bedrock-runtime converse --model-id moonshot.kimi-k2-thinking --messages '[{"role":"user","content":[{"text":"reply OK"}]}]' --inference-config '{"maxTokens":5}' --region us-east-1` (~$0).
2. **Branch on the result:**
   - **Returns a completion** → the quota lifted. Mark **ERR-001 Resolved** in [`errors.md`](docs/ledgers/errors.md) (date + verbatim success), and reconcile the "scoring blocked / ERR-001 gates Step 5" framing across [`decisions-locked`](docs/ledgers/decisions-locked.md) (Bedrock-prereq row), [`04-v0-build-plan`](docs/04-v0-build-plan.md) (Prereq 1 + Step 5 WAIT-FOR), [`CLAUDE.md`](CLAUDE.md) status, [`phase-index`](docs/ledgers/phase-index.md). **Scoring (Step 5) becomes GO.** (Docs → may commit direct per ADR-0013.)
   - **Still `ThrottlingException`** → ERR-001 stays **Open**; stamp the fresh re-test date so the record is current; if Tarig's "works" was the console, that's the reconciliation. No false-resolve.

**Verification:** the `converse` result is the single source of truth; whichever branch, the docs end up matching reality and carry the **current** test date (not the stale 06-17 one).

---

## 25 — Daily reminder + re-check for the AWS Bedrock quota case (178220019100382)

**Context.** The Support case to lift the new-account Bedrock token quota (ERR-001) has **no fixed timeline** — Tarig wants a **daily reminder** to chase it and re-test until it clears. Mechanism = a **scheduled Claude Code routine** (cron), created via the **`schedule` skill**.

**Approach (out of plan mode):**
- Create a **daily scheduled routine**, default **09:00 Asia/Riyadh** (adjustable). Each fire, the agent:
  1. **Re-tests if it can run AWS** (local creds present): the 1-token `converse` → `moonshot.kimi-k2-thinking`, us-east-1.
     - **Completion** → "✅ QUOTA LIFTED — Kimi works via the API. Ready to mark ERR-001 Resolved + start v0 Step 5 (scoring)." → offer to update the docs (§24) and **cancel this reminder**.
     - **`ThrottlingException`** → "⏳ Still blocked (day N) — quota still 0; follow up on AWS case 178220019100382."
  2. **No AWS access (remote/cloud env)** → fall back to a plain nudge: *"Re-test the Kimi quota (say 're-test Kimi' in a session) and chase case 178220019100382."* — we **do not** put the personal AWS key into a cloud routine (keys stay out of cloud envs, per our security model).
- **Auto-stop:** cancel the routine the day the quota lifts (ERR-001 → Resolved), so it doesn't nag forever.

**Deliverable:** invoke the `schedule` skill to create the routine; confirm it's registered (a daily cron at the chosen time).

**Verification:** the routine shows in the schedule/cron list with the daily trigger; a manual "run now" produces the reminder/re-test message; it self-cancels on resolution.

---

## 26 — Capture the ingestion-architecture decisions (DB + silver internals)

**Context.** A design discussion on the ingestion AWS services produced concrete decisions; capture them (repo-is-memory, constructed-live) before continuing to other threads (compute/orchestration, scale-up palette — *not yet discussed*). Decisions made this session:
- **D-v0-1 resolved → Aurora Serverless v2 + RDS Data API** (Lambda **outside any VPC**, HTTP DB access). Rejected RDS `t4g.micro`+VPC: the **public JSearch fetch forces a ~$32/mo NAT gateway**, erasing the DB savings and adding VPC/endpoint/proxy complexity.
- **v0 silver = pure Python in the Lambda** (clean → lang-detect → fingerprint → field-map). **Exact-id dedup only ⇒ NO embeddings, NO Bedrock in v0** ⇒ the whole **ingestion→silver→gold half is quota-independent** (buildable + runnable now; only Step 5 scoring waits on ERR-001).
- **Language detection = `lingua` lib + behavioral gate** (positive+negative test on real `probe_output/` JDs; low-confidence ⇒ **flag, not drop**; replayable over immutable bronze). English-vs-Arabic ≈ 99.9% (disjoint scripts); the lib also catches non-English Latin-script (French/Spanish) a heuristic would miss.
- **Division of labor:** cheap deterministic work in silver (no LLM); the **semantic dissection** (skills/sections/score) is the LLM (Kimi) at **scoring (Step 5)** — Tarig's "LLM dissects the JD" instinct, correctly placed.
- **Embeddings + pgvector blocking = M2** (deferred); **pgvector-in-Aurora** is the vector store (no separate vector DB; no OpenSearch/Pinecone). No Glue/EMR/Comprehend (Python-in-Lambda + a lib suffice at 10–30/day).

**Edits (out of plan mode; docs → direct to `main` per ADR-0013):**
1. **NEW [ADR-0014] — Operational store: Aurora Serverless v2 + Data API (no VPC)** — resolves D-v0-1; alternatives rejected = RDS+VPC (NAT for the internet-bound fetch; complexity) and a separate vector DB. + index in `adr/README.md`; note in [ADR-0003](docs/adr/0003-postgres-over-dynamodb.md) that D-v0-1 is now resolved.
2. **`docs/02-architecture.md`** — silver section: v0 = pure-Python (`lingua` lang-detect, fingerprint), **no embeddings/Bedrock in v0** (exact-id dedup) ⇒ quota-independent; the deterministic-vs-LLM division; embeddings + pgvector-blocking = M2. Operational plane: **Aurora SLv2 + Data API, Lambda-outside-VPC** (no NAT/endpoints).
3. **`docs/04-v0-build-plan.md`** — D-v0-1 → **resolved** (link ADR-0014); Step 3 = Aurora SLv2 + Data API (no VPC); Step 4 silver = add the `lingua` lang-detect step; add a **language-detect VG** (behavioral + negative on real JDs); flag that the **ingestion→silver→gold half is buildable now, Bedrock-independent** (only Step 5 waits on ERR-001).
4. **`docs/ledgers/decisions-locked.md`** — rows: Aurora SLv2+Data API (D-v0-1); v0 silver pure-Python + quota-independent; `lingua` lang-detect; embeddings deferred → M2.
5. *(optional)* refresh the ingestion Mermaid (§2 [diagrams.md](docs/diagrams.md)) to show Aurora + the v0(no-embed) / M2(embed) split.

**Verification:** ADR-0014 exists + indexed; D-v0-1 reads "resolved" everywhere; 02-architecture silver says "v0 = pure Python, no Bedrock, `lingua`"; build-plan carries the lang-detect VG + the "buildable now" note; decisions-locked rows added; pushed. **Then** we can resume with the next thread (compute/orchestration or the scale-up palette) or start building the (unblocked) ingestion half.

---

## 27 — LLM dissection at silver (all postings) + gold LLM filter + type-replaceability tenet

**Context.** A deep design discussion settled the silver-layer strategy. **Driver:** the market-wide analytical tables (Skill-Demand, Sector Intelligence) need fields extracted from **all** postings — extracting only from gold (candidate-matched) would bias "demand" toward the candidate's own profile. Decisions:
- **Silver = a full LLM dissection on *every* deduped posting** (Tarig chose the purest option — no pre-gate). The LLM extracts the structured fields — `skills[]` + requirement level `{must|nice|implied}`, sector, normalized title, seniority, location, **language (just one byproduct field)**, … — that **populate the dimensional tables** ([ADR-0011](docs/adr/0011-dimensional-analytical-model.md)). This **replaces `lingua`** and **reverses "only gold reaches the LLM."**
- **Gold = LLM `FilterStrategy`** (filter the dissected candidates to "likely fit"); **Score = the strong model** (fit judgment on already-structured data). **Per-task models** ([ADR-0012](docs/adr/0012-model-agnostic-llm.md)): **cheap/fast model for the bulk silver dissection, strong model for scoring.**
- **Type-replaceability = a first-class tenet:** every stage = a swappable strategy behind a port — `SourceAdapter · Dissector · FilterStrategy · Embedder · Scorer` — config-selected, replaceable **by type, not just scaled**.
- **Variation across JDs** countered by LLM-understanding + a **canonicalization layer** (`dim_skill` synonyms: "Postgres"→"PostgreSQL") + a **structured-output contract** (Pydantic, temp 0) + **replay/calibration** — not by enumerating phrasings.

**Quota-scope reconciliation (supersedes §26 + parts of commit `62d7fbe`):** with the LLM at silver, **only bronze (fetch + land) is live-runnable without the quota**; **silver-dissection + gold + score** all need Bedrock (ERR-001). The whole pipeline stays **build + unit/integration-test-able now with Bedrock mocked.** The earlier "silver = pure Python / `lingua` / ingestion→silver→gold quota-independent" is **superseded — must be un-wound.**

**Edits (out of plan mode; docs → direct to `main`):**
1. **NEW [ADR-0015] — Type-replaceable pipeline stages.** Every stage = a Strategy behind a Port, config-selected, swappable by type (generalizes [ADR-0012](docs/adr/0012-model-agnostic-llm.md)). Ports: `SourceAdapter · Dissector · FilterStrategy · Embedder · Scorer` (+ `Notifier`, `CVRenderer`). Rejected: hardcoded per-stage impls / swap-by-rewrite. + index.
2. **NEW [ADR-0016] — LLM dissection at silver (all postings).** Every deduped posting is LLM-dissected at silver to populate the market-wide dimensional tables. Rejected: extract-at-gold-only (biases the market analytics) and deterministic/`lingua` silver (can't produce the structured matrices). Records the cost/quota trade + the cheap-model mitigation. + index.
3. **`docs/00-design-philosophy.md`** — add the **type-replaceability** tenet alongside P1/P2.
4. **`docs/02-architecture.md`** — rewrite the silver section: **LLM dissection (cheap model) on all deduped postings → structured fields → dimensional tables**; the canonicalization + structured-contract mechanism; **remove `lingua`**; gold = LLM FilterStrategy; the stage ports; corrected quota scope (bronze-only live; whole pipeline test-able mocked). Reconcile the §26 "v0 silver = pure Python, no Bedrock, quota-independent" lines.
5. **`docs/04-v0-build-plan.md`** — Step 4 silver = LLM dissection (cheap model, structured contract; mock for tests); Step 4b gold = LLM FilterStrategy; **reconcile the "ingestion→silver→gold buildable+runnable now"** → only bronze live-runnable, rest LLM-gated but test-able mocked; **drop the `lingua` lang-detect step + its VG.**
6. **`docs/ledgers/decisions-locked.md`** — rows: type-replaceability tenet; **silver = LLM dissection on all postings** (supersede the §26 lingua/pure-Python row); gold = LLM FilterStrategy. Remove the lingua/lang-detect references.

**Verification:** ADR-0015 + ADR-0016 exist + indexed; design-philosophy carries the replaceability tenet; 02-architecture silver describes the LLM dissection + canonicalization + ports + corrected quota scope; **grep finds no stale "lingua / pure-Python silver / silver quota-independent" claim** anywhere; build-plan Steps 4/4b updated; decisions-locked reconciled; pushed.

---

## 28 — Route around Bedrock: OpenAI-compatible `LlmClient`, v0 backend = DeepSeek API (Bedrock parked)

**Context.** Bedrock's new-account daily-token quota = **0** ([ERR-001](docs/ledgers/errors.md)) has blocked the LLM for weeks with no lift timeline. **Decision: stop waiting on Bedrock; route around it via the model-agnostic port ([ADR-0012](docs/adr/0012-model-agnostic-llm.md)) — the exact P2 bottleneck-break the architecture was built for.** Hardware checked (RTX 3050, **4 GB VRAM** → local caps at 3–7B, not serverless). Compared DeepSeek API vs local Ollama vs Anthropic-direct; **Tarig chose DeepSeek API** — cheapest (V4 Flash **$0.14/$0.28** per M, **5M free** signup tokens, **~$0.50–1/mo** at our volume), OpenAI-compatible, serverless-ready.

**The design (more minimal + universal than Bedrock Converse):**
- **One `OpenAICompatLlmClient` behind the `LlmClient` port.** OpenAI-compatible is the de-facto standard ⇒ the **backend is pure config**: `base_url` + `api_key` (Secrets Manager) + `model` per task. DeepSeek API, Ollama, OpenRouter, Anthropic (a thin 2nd adapter), and Bedrock-when-unblocked are all config swaps. **This permanently kills the single-provider fragility Bedrock just inflicted.**
- **v0 default backend = DeepSeek API** (`https://api.deepseek.com`, OpenAI-compatible). Per-task models ([ADR-0012](docs/adr/0012-model-agnostic-llm.md)): **`deepseek-v4-flash`** for the bulk silver `Dissector` + gold `FilterStrategy`; **`deepseek-v4-pro`** for `Scorer`. *(Use the v4 ids — `deepseek-chat`/`deepseek-reasoner` aliases retire 2026-07-24.)*
- **Structured output** via prompt + Pydantic (unchanged, portable).
- **Honest privacy note:** JD text + (at scoring) the profile go to DeepSeek's **China-hosted** servers, whose ToS permits training on API inputs. Accepted for v0; the port lets us flip *scoring* to local Ollama or Anthropic-direct later if the CV/PII privacy matters.
- **Bedrock → parked as one possible backend.** **ERR-001 → Mitigated/Worked-around** (the quota is still 0, but it no longer gates us); keep AWS case `178220019100382` open as "nice if it lifts." Kimi/Converse were only chosen because they were *on Bedrock* — now moot.

**Quota consequence (reverses §27's caveat):** with DeepSeek (no new-account gate), the **whole pipeline is live-runnable now** — bronze + silver-dissection + gold + score — once the DeepSeek key is in Secrets Manager. The "only bronze live until ERR-001" caveat is gone.

**Edits — Part A (docs → direct to `main`):**
1. **NEW [ADR-0017] — LLM transport = OpenAI-compatible API; v0 provider = DeepSeek (Bedrock parked).** Rejected: stay-on-Bedrock (blocked, AWS-coupled), Bedrock-Converse-only (single-provider fragility — the failure we just hit), Anthropic-direct (pricier; kept as the privacy/quality fallback), local-only (4 GB-limited, not serverless). Records the cost + privacy trade + the per-task model split. + index.
2. **[ADR-0012]** — retitle → "Model-agnostic LLM via OpenAI-compatible API (model + base_url in config)"; transport Bedrock Converse → OpenAI-compatible HTTP; default backend = DeepSeek API; Kimi removed as "chosen model"; Anthropic/Ollama/Bedrock = config-swap alternatives.
3. **[ADR-0015]** — ports table: `Dissector`/`FilterStrategy`/`Scorer` "Bedrock model" → "OpenAI-compatible LLM (DeepSeek v0)".
4. **`02-architecture.md`** — scoring/silver/gold + the Converse line (≈L222) + diagram labels: Bedrock → provider-agnostic OpenAI-compatible (v0 = DeepSeek); drop the Bedrock-quota-gated language (no gate on DeepSeek); whole pipeline live-runnable.
5. **`04-v0-build-plan.md`** — Prereq 1: Bedrock-quota → **DeepSeek API key in Secrets Manager (`jobfetcher/deepseek`)**, WAIT-FOR = a 1-call `deepseek-v4-flash` completion; Step 4 (Dissector) + Step 5 (Scorer): OpenAI-compatible client + DeepSeek model ids; cost estimate → DeepSeek (~$1/mo); remove the ERR-001 WAIT-FOR gating; note the whole pipeline is now live-runnable.
6. **`decisions-locked.md`** — replace the Bedrock-quota + Kimi/Converse rows → "LLM = OpenAI-compatible API; provider in config; v0 = DeepSeek API (Flash/Pro per task); Bedrock parked"; fix the silver/gold "only bronze live until ERR-001" rows → whole pipeline live on DeepSeek.
7. **`errors.md` (ERR-001)** — status Open → **Mitigated (worked around via ADR-0017)**; add the update note; AWS case stays open as optional.
8. **`CLAUDE.md`** — Current-status: drop "Open blocker: Bedrock quota = 0"; "Chosen LLM = Kimi via Converse" → "LLM = OpenAI-compatible API; v0 backend = DeepSeek API; Bedrock parked".
9. **`README.md`** — "Amazon Bedrock (Kimi)" → "LLM via OpenAI-compatible API (DeepSeek; provider-agnostic)".

**Part B — the live unblock (needs Tarig):** Tarig registers at `platform.deepseek.com` → hands over the API key → I store it in **Secrets Manager `jobfetcher/deepseek`** (never committed) → I write a minimal OpenAI-compatible **smoke test** (`scripts/deepseek_smoke.py`, key from Secrets Manager, 1 cheap call to `deepseek-v4-flash`) → a completion **proves ERR-001 is worked-around** and the pipeline is unblocked.

**Part C — deferred to a build unit (`/start-step`):** the `OpenAICompatLlmClient` port + the silver `Dissector` (cheap model, structured contract) + its behavioral gate, built as a proper v0 build step.

**Verification (Part A):** ADR-0017 exists + indexed; ADR-0012 retitled (OpenAI-compatible, DeepSeek default); **grep finds no stale "Bedrock quota blocks / Kimi chosen / only bronze live" framing** in the living docs; ERR-001 reads Mitigated; CLAUDE.md status shows no open blocker; pushed. *(Part B verified by the smoke-test completion once the key lands.)*

---

## 29 — Milestone: LLM verified LIVE on DeepSeek + milestone documentation

**Context.** Part B of §28 completed. Tarig registered DeepSeek, **rotated the key** (it had been pasted in plaintext), funded a **$2 balance** (DeepSeek's "free signup tokens" did **not** apply → `402 Insufficient Balance` until funded), and stored the key in Secrets Manager (`jobfetcher/deepseek`). `scripts/deepseek_smoke.py` → **HTTP 200 from `deepseek-v4-flash`** (2026-06-24). **The LLM path is LIVE; ERR-001 is worked around.** This is the project's first "it works" milestone after weeks blocked (committed `d3e1bb3`).

**Milestone documentation** (Tarig: *"document this milestone everywhere, thoroughly, so anyone continuing understands the why"*):
- **Journal Part 2 → new §18 "⭐ Milestone — the LLM goes live"** — the full arc: the decision to stop waiting, the option comparison (local / DeepSeek / Anthropic-direct) grounded in the real 4 GB hardware, the OpenAI-compatible-adapter insight, the `402` balance detour, the verified PASS, and *what it means* (Mitigated-not-Resolved · quota-scope inverted · the architecture paid for itself · the honest China-hosted/privacy cost). Renumbered pointers→§19, through-line→§20 (folded the pivot in).
- **NEW `CHANGELOG.md`** (repo root, Keep-a-Changelog; per [ADR-0013]/methodology) — `[Unreleased] v0.1` with the milestone headline + build-phase Added/Changed/Notes.
- **ADR-0017 status** → "✅ Verified live 2026-06-24"; **phase-index + CLAUDE.md status** → "verified live"; **errors.md** already carries the Verified note.
- Re-synced the working-document archive through §29.
- **Annotated git tag** `milestone/llm-live-2026-06-24` marks the moment.

**Verification:** journal §18 exists + renumbering clean (pointers §19, through-line §20); `CHANGELOG.md` at root; ADR-0017 / phase-index / CLAUDE read "verified live"; tag created + pushed; all on `main`.
