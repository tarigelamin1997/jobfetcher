# 01 · Session Decision Journal

> **Purpose:** preserve *why* JobFetcher's design is what it is. This is the narrative record of the design session that produced this repo — the reasoning, the alternatives rejected, the pivots, and the order they happened in. If you're a future session (human or agent) and you only read one doc to understand the *intent* behind the architecture, read this one. The crisp *decisions* are in [the locked-decisions ledger](ledgers/decisions-locked.md) and [`adr/`](adr/); this is the *reasoning*.

Format: each entry is **Decision · Why · Rejected · So-what.**

---

## 0. Origin & mandate

**Where we started.** A prior, very elaborate JobFetcher design existed (a 23 KB rules `CLAUDE.md`, a 75-page planning PDF, an 8-phase Notion plan, multiple standards docs, a portfolio assessment). It described a full AWS-serverless pipeline: EventBridge → Step Functions → 7 Lambdas → Bedrock scoring + CV tailoring → DynamoDB + Streams CDC → Notion (4 DBs) → SES → CloudWatch, all in Terraform, plus near-miss graduation, skill/sector analytics, a feedback hub, and a multi-user vision.

**Mandate (Tarig).** Scrap it and start fresh "in a better way." First *absorb* all existing knowledge, then *interrogate* (100–300 questions) to ground the rebuild in real intent rather than inherited assumptions, then reflect the understanding into **documentation that survives session loss**, then build.

**So-what.** This session is the interrogation + design. The deliverable is this doc set. The old artifacts were fully absorbed (their substance lives on where it earns its place) and then deleted for a clean slate.

---

## 1. The four framing forks (these reordered everything)

1. **Primary goal → "Both equally": the tool IS the portfolio.** *Why:* Tarig wants a tool he genuinely uses *and* a repo that lands a job; neither dominates. *So-what:* every component must earn *dual* value — daily-useful AND real portfolio signal. This became the knife used to cut "portfolio theater."
2. **v1 scope → "Full original system."** *Why:* he wants completeness, not a lean cut. *Rejected:* lean MVP, DE-only subset. *So-what:* discovery became *re-justify + improve* each part, not remove — later reconciled with minimalism via the evolutionary model (§9).
3. **Architecture authority → "Claude recommends (defended); Tarig approves."** *So-what:* I bring defended recommendations, not menus.
4. **Working style → "Co-design now, then I build."** *So-what:* the whole design was settled interactively before any code.

Discovery method chosen: **section-by-section interactive** (≈190 questions organized A–V).

---

## 2. Goals & what "better" means (Section A)

- **Nothing failed — "it was fine, want it better."** The restart is a smarter second pass, not a rescue. **Truly greenfield** (old ideas as inspiration only).
- **"Better" = (1) more DE depth** (SQL/warehouse/dbt — the assessment's flagged gap) **+ (2) more reliable.** These are the two north stars.
- **"Keep it all"** — Tarig initially saw nothing as overkill; complexity acceptable *if justified*. *So-what:* the burden landed on me to justify each retained piece — which later (via the defensibility audit and evolutionary model) became the project's backbone rather than a tension.

## 3. The real job-search situation (Section B)

- **Active-but-selective**, urgency **in weeks**. *So-what:* he'll actually use this soon → it must be genuinely good, and value must arrive fast. This created the first "full system but fast" tension, resolved by **MVP-first sequencing**.
- **Biggest time-saves = discovery + filtering/scoring + CV** (the core pipeline). **Not** tracking/market-intel. *So-what — key insight:* the analytics layer is *portfolio/strategic* value, not daily time-save. So sequence the core pipeline first; analytics is the DE-depth layer behind it.
- **Auto-CV = core value.** *So-what:* CV tailoring stays first-class.

## 4. Target roles, market, profile (Section C)

- **DE / Data Platform / Data Architect only** (no spread to Analytics-Eng/Cloud/ML). **Riyadh → GCC → open to relocate; NOT remote-global** (on-site oriented). **Profile accurate as-is** (use it unchanged as scoring source of truth). **English only.** Old sensitivities still hold (site offline; "Cansa Group" naming; honesty rules).
- *So-what:* simplifies search config, scoring, and CV; no Arabic handling; no re-intake of profile.

## 5. Portfolio positioning (Section D) — a pivotal lock

- **Headline = "end-to-end system design."** *So-what:* **breadth IS the portfolio point** — which makes "keep it all" *coherent* rather than greedy: the complete working system is the story.
- **Must-demonstrate = all four:** LLM/AI, warehouse/dbt modeling, AWS-serverless/IaC, streaming/CDC. *So-what:* this justified several complex pieces (CDC is a *required* signal, not theater) **and** confirmed adding a real warehouse+dbt layer.
- **Engagement = "some will clone & run"** → must be genuinely reproducible. **Presentation = README + diagram + demo video, all required.**
- **Portfolio thesis (locked):** *a complete, reproducible, production-style system across LLM + warehouse/dbt + serverless/IaC + streaming/CDC, fully documented, clone-and-runnable.* (Note: §8 honesty audit + §9 minimalism later *right-sized* how much of this is built up-front vs. reached by migration — the thesis is the destination, not the v1.)

## 6. Scope, timeline, working process (Sections E, T, U)

- **Full-tilt (20+ h/wk).** Approach initially **"plan everything, then build"** → later refined (§9) to *"plan the foundation + current stage fully; plan each migration just-in-time."*
- **Multi-user + feedback hub = design-for, build-later** (seam-ready). *So-what:* capability designed in, implementation deferred — pragmatic scoping inside "keep it all."
- **Autonomy = "confirm major decisions only."** **Testing = full pyramid** (unit + LocalStack/moto + dbt tests + live smoke). **Docs = everything (CLAUDE.md + design doc + ADRs + README + exhaustive build plan), exhaustive detail.**
- **Docs location** — a conflict surfaced (Notion vs repo) and was **resolved: repo is canonical** for all design/plan/ADR docs; Notion only hosts the operational user-facing databases. *Why:* design docs versioned with code = part of the portfolio + the context-survival requirement.

## 7. Pipeline internals (Sections H–K, M, N–S)

- **Data:** multi-source via **official APIs only** (JSearch + Adzuna to start), no scraping → a **pluggable source-adapter** layer. Volume **modest (10–30/day)** → Bedrock cost trivial; dedup is about *quality* not scale.
- **Scoring:** keep the **7-factor ATS framework** (tune weights), **explainability is critical** (strengths/gaps/strategic assessment), thresholds **75/55/10** (active-but-selective), a **lightweight calibration loop** (capture score corrections → tune prompt + a scoring-accuracy SLO), keep the scam-gate + poster-type label.
- **CV:** **drop LibreOffice-in-Lambda** (the old #1 reliability risk) → a deterministic, dependency-light renderer (one content model → DOCX via python-docx + PDF via a pure-Python/HTML path). Keep the *tarig-cv* template as the refined base. **One master CV.** **Strict honesty + human-review gate** (CV is a draft you approve; the review gate doubles as calibration capture).
- **UI:** **email + Notion both** first-class; status tracking in Notion; a BI dashboard over the marts is *designed-for, build-later*.
- **Near-miss/graduation:** keep the full watch→re-score→graduate loop. **Observability:** initially "full suite," later **right-sized** (§8). **Orchestration:** Step Functions (AWS-native) — but it became a *migration* (M3), not a v0 component (§9).

## 8. The architecture synthesis, then four deep pivots

I synthesized a **two-plane architecture** — an **operational plane** (the daily serverless tool) cleanly separated from an **analytical plane** (DE-depth: warehouse + dbt). *Why:* lets us push hard on the DE-depth prime directive without diluting the serverless story; realizes the medallion as a *real dbt-modeled warehouse*, not just S3 folders. Tarig approved it "with tweaks," then drove four deep pivots:

### 8a. Warehouse: Snowflake vs Databricks vs Postgres
*Decision:* **Snowflake** as the (eventual, conditional) warehouse — **not** Databricks. *Why:* the assessment's flagged gap is **SQL/warehouse modeling + dbt**, which is Snowflake's lane; at 10–30 rows/day the warehouse is a *portfolio/skill* choice, not a data need, and Snowflake is the cheapest/most-reliable/cleanest-with-dbt option, with strong KSA/GCC demand. *Rejected:* **Databricks** — Spark-on-tiny-data is unconvincing and a weaker fit; the Spark/Delta signal belongs in the sibling **OrderFlow** project where volume justifies it. *Crucial reframe:* since we already run **Postgres**, the entire medallion could live there for $0 — so the warehouse choice is purely about signal, which later (§9 tool-minimalism) made **Snowflake conditional** and **Postgres+dbt the default**. See [ADR-0004](adr/0004-warehouse-strategy.md).

### 8b. Operational store: Postgres + (deferred) Debezium CDC, replacing DynamoDB+Streams
*Decision:* **managed PostgreSQL** as the operational store. *Why:* the data is inherently relational (clusters → postings → scores → applications); a relational DB is the *right-tool fit*, and it mirrors Tarig's real DE expertise. *Rejected:* DynamoDB + Streams — NoSQL for relational data was the *weaker* original choice. **Debezium CDC** was adopted then **deferred**: at this volume, nightly batch → warehouse + incremental dbt is right-sized; Debezium becomes the *documented scale-up path* ("we'll need it later"), and the built CDC showcase lives in OrderFlow. See [ADR-0003](adr/0003-postgres-over-dynamodb.md), [ADR-0009](adr/0009-batch-not-debezium-v0.md).

### 8c. Deduplication: cluster-and-surface, never hide
*The 99.99%-accuracy question.* Tarig wanted near-perfect dedup across multiple APIs. *Reframe (honest):* a literal 99.99% guarantee on fuzzy job-text matching isn't honest. There are two error types — a **missed duplicate** (trivial cost: one extra cheap score) and a **wrong merge** (the dangerous one: a real job gets hidden). *Decision (Tarig's, and it overrides the old "pick one canonical + hide duplicates"):* **never silently hide a posting.** Instead **cluster** suspected-same postings and **surface the whole group** with every platform's apply-link + a "suspected same as X/Y/Z" note; the **user decides** whether to apply via one or several (hiring teams sometimes favor one platform's pipeline). Uncertain clusters go to a dedicated **Suspected-Duplicates** surface to confirm/split. *Engineering:* precision-first + fail-safe + **measured precision/recall** (entity resolution with real numbers — a strong DE signal); JD-body embeddings (pgvector) + apply-URL match + company canonicalization + time-window scoping; **score + tailor a CV once per cluster** (identical content) but show all links. See [ADR-0005](adr/0005-dedup-cluster-and-surface.md).

### 8d. Distribution: self-hosted / open-source (not SaaS)
*Decision:* **self-hosted / open-source.** *Why:* the goal is a job-search tool + a portfolio, **not a company.** Self-hosting makes the IaC + reproducibility *itself* the portfolio value, with zero ongoing liability. *Rejected:* **centralized SaaS** — it would mean owning others' cost, auth, billing, support, multi-tenancy, and **other people's CVs/PII** (real legal/privacy liability) plus job-data ToS exposure: a startup, not a portfolio piece, and a distraction from landing a job. Multi-user stays a documented future pivot. See [ADR-0007](adr/0007-self-hosted-distribution.md).

## 9. The honesty audit, the evolutionary reframe, and the governing principles

**Honesty audit (Tarig's challenge: "can you defend this architecture, or are we spinning service names?").** I audited every component against a **defensibility rubric** (now in [00-design-philosophy](00-design-philosophy.md)). Outcome: most pieces are defensible as right-tool-fit; **two were flagged** — Debezium-CDC (right-sized to batch, deferred) and the full SLO/alarm suite (**right-sized** to a few real alarms + documented SLOs). Adopted dial: **BALANCED** — simplest defensible option by default, 2–3 labeled showcases (warehouse/dbt + measured entity-resolution).

**Evolutionary architecture reframe (Tarig — the single biggest idea).** Don't build the full system at once. Build the **minimal working core (v0)**, then evolve through a **sequence of deliberate, observable migrations — each a clean GitHub release.** *Why it's the strongest framing:* controlled architectural *evolution* (an ADR + migration guide per step) is a rare senior/staff portfolio signal; it makes every piece defensible *by construction* (capability arrives exactly when justified); and a leaner v0 yields *more, better* migrations — the migrations ARE the showcase. *So-what:* "full system" becomes the *destination reached via migrations*, fully reconciling "keep it all" with "absolute minimalism."

**The roadmap is directional, not fixed (Tarig).** You can't draw the full sequence before shipping — each stage's *implementation* is the bottleneck that reveals the next capability. So we commit firmly to only: v0, the migratable architecture, and release discipline. The rest is a living hypothesis. This refined "plan everything then build" → **"plan the foundation + current stage fully; plan each migration just-in-time."**

**Governing principles P1/P2 (Tarig).** **P1 absolute minimalism** (minimal complexity for the present problem; complexity is entropic → resist it) + **P2 bottleneck-driven evolution** (solve the top bottleneck with the minimal migration; the migration-decision protocol). Together: *add the minimum, only to break the biggest real bottleneck.*

**Tool-minimalism wins (Tarig).** Only build what a real **tool** bottleneck justifies; DE-depth is the *tiebreaker*, not a license to add. *So-what — this revised an earlier "lock":* **Snowflake became conditional**, with **Postgres + dbt as the default** analytics; Step Functions survives only because it's *earned* by real Lambda complexity (M3). DE-depth is still fully served, minimally and honestly (Postgres + dbt modeling/tests/lineage + measured entity resolution + data contracts + the evolutionary-architecture story itself).

## 10. v0 boundary & the roadmap

- **v0 (locked) = the irreducible core:** EventBridge (daily) → **one Lambda**: fetch from **one source** → raw to **S3** + rows to **Postgres** → **Bedrock** score vs profile → **daily email digest**. Terraform + Secrets Manager + tests + minimal CI. *No CV, no Notion, no Step Functions, no dedup-clustering, no warehouse.* It works (you wake to a scored shortlist); everything else is a migration.
- **CV = M1** (first migration), **email-only** for v0, **single Lambda** for v0 — chosen *leanest*, because a leaner v0 creates more (better) migration stories.
- **Roadmap (directional):** v0 → M1 CV → M2 multi-source+clustering-dedup → M3 Step Functions → M4 Notion+near-miss → M5 dbt marts on Postgres → M6 skill/sector intel → M7 observability+calibration → M8 CI/CD+README/diagram/demo (= v1.0.0). Future: Debezium CDC, multi-user, feedback hub, BI dashboard, Snowflake (conditional). **Semver:** v0.x per migration → v1.0.0 at M8. Full detail + the bottleneck protocol in [03-roadmap](03-roadmap.md).

## 11. Methodology & clean slate

- **Methodology adopted (right-sized)** from Tarig's *Master Project Implementation Plan* + *Modern DE Best Practices*: documentation-as-infrastructure, What/Why/So-what, ADRs-with-rejected-alternatives, the Five-Questions error log, ledgers (phase index, contracts, locked decisions), behavioral validation gates (positive + negative), safety-first. Right-sized for solo/minimalist scale; **enforcement machinery (slash-commands vs Makefile vs checklists) is emergent** — evaluated during implementation. Full breakdown in [05-methodology](05-methodology.md).
- **Clean slate (Tarig, explicit):** delete the entire old project tree and start with nothing but this doc set. Done. The plan file and memory live outside the project and survived.

---

## Appendix — the through-line

The session repeatedly converged on one idea from multiple directions: **earn complexity, don't assume it.** "Both equally" → every piece earns dual value. "Defensibility rubric" → every piece beats its simpler alternative. "Evolutionary architecture" → every piece arrives only when a bottleneck justifies it. "Tool-minimalism wins" → the tool's needs gate what's built. These are four expressions of the same discipline, now encoded in [00-design-philosophy](00-design-philosophy.md).
