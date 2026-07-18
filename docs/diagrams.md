# Diagrams

> The visual index. All repo diagrams are **Mermaid** — they render inline on GitHub and in VS Code's preview, version with the code, and never drift (the text *is* the picture). Edit any block here, or paste it into [mermaid.live](https://mermaid.live) to tweak.
>
> **Convention:** Mermaid is canonical and lives in the repo. [Eraser](https://app.eraser.io) is an optional *personal/portfolio* view (prettier AWS icons, authored via diagram-as-code) — it is **not** committed here to keep the repo text-light. A live link can be shared for the portfolio when wanted.

---

## 1 · Full-stack architecture (target)

The complete two-plane design (high-level). **What's live today is a subset — `v0.12.0`, running fully unattended** (daily 06:00 UTC EventBridge cron → one Lambda → fetch → dissect → gold → 7-factor score (+ shadow subscores/`code_total`, **boundary-resampled** near the cutoff, v0.11) → SES card-digest + a presigned S3 full-list report, on Aurora SLv2 via the RDS Data API + S3; **concurrent** dissect/score with a deadline guard; **config read from S3 at runtime**; a **`{"mode":"reassess"}` replay** path and a **`{"mode":"smoke"}` deploy gate**; **`score_event` + `application_event` lineage**; **2 CloudWatch alarms → SNS → email**; Terraform state in an S3 backend; **full S3 audit persistence** (every stage's results → S3 JSONL: `silver/`/`gold/`/`scores/`/`runs/`, v0.12); a **local Streamlit control panel** (browse/curate/config, v0.12); deployed + live-validated); the analytical plane + the other operational boxes arrive by migration. The **ingestion medallion is detailed in §2 below**; the LLM is **provider-agnostic** ([ADR-0012](adr/0012-model-agnostic-llm.md) · [ADR-0017](adr/0017-llm-transport-openai-compatible-deepseek.md)) and v0 runs on **DeepSeek** via the OpenAI-compatible API. Discussed in [02-architecture](02-architecture.md).

```mermaid
flowchart TB
  subgraph EXT["External"]
    JS["JSearch API<br/>(Google-for-Jobs)"]
    NO["Notion"]
    US["You (email)"]
  end

  subgraph OP["Operational plane — AWS serverless"]
    EB["EventBridge<br/>daily cron"] --> ORC["Lambda v0<br/>concurrent dissect/score + deadline guard (v0.2)<br/>→ Step Functions (M3)"]
    subgraph MED["Medallion"]
      BR["Bronze<br/>raw · immutable"]
      SI["Silver<br/>clean · LLM dissect · dedup"]
      GO["Gold<br/>profile filter<br/>(v0: deterministic default)"]
    end
    ORC --> BR --> SI --> GO --> SC["Score<br/>LLM · DeepSeek · 7-factor ATS (v0.8)<br/>+ 7 shadow subscores + weighted code_total (SHADOW, not the product number)"]
    SC -->|">= threshold"| CV["cv_tailor (M1)<br/>DOCX + PDF"]
    SC --> NT["notify<br/>SES card digest (v0.10) · new-vs-still-open split + graduation badges (v0.7)<br/>+ presigned S3 full-list report link (v0.10) (+ Notion M4)<br/>run_log send-once / day"]
    CV --> NT
    SC --> SE[("score_event lineage (0004, v0.7)<br/>application_event + human-override (0005, v0.7)")]
    NT --> RPT[("S3 reports/{date}/jobs-{run_id}.html<br/>all scored jobs · presigned https URL (v0.10)")]
    SC -.->|"reassess (v0.4)<br/>{mode:reassess} · re-score existing · no fetch"| SC
    EB -.->|"{mode:smoke} deploy gate (v0.9)<br/>Data-API connect + alembic_version check"| ORC
    PG[("Postgres — Aurora SLv2<br/>via RDS Data API · + pgvector")]
    S3[("S3<br/>raw + config (v0.3) + CVs + reports (v0.10)<br/>+ audit silver/gold/scores/runs (v0.12)")]
    SM["Secrets Manager"]
    CF["Profile / Config<br/>in S3, read at runtime (v0.3)<br/>edit + push_config.py · no redeploy"]
    EXP["export (v0.5)<br/>SQLite/CSV → Datasette/Excel"]
    PANEL["control panel (v0.12)<br/>panel.py · browse/curate/config<br/>(local Streamlit)"]
    subgraph OBS["Observability (v0.9)"]
      AL["2 CloudWatch alarms<br/>dead-man on daily rule + Lambda Errors"]
      SNS["SNS topic"]
      AL --> SNS
    end
    EB --> AL
    ORC --> AL
    SNS --> US
  end

  subgraph AN["Analytical plane — DE depth"]
    EL["batch extract-load"] --> DBT["dbt marts (M5/M6)"]
    DBT --> MA["constellation<br/>fct_job_skill + dims"]
    MA --> IN["Skill Demand<br/>+ Sector Intel"]
    SNF["Snowflake<br/>(conditional)"]
  end

  JS -->|paginated pull| BR
  SI <--> PG
  SC <--> PG
  S3 -->|config read at runtime| CF
  CF -->|runtime| SC
  SM -.->|creds| ORC
  SE --> PG
  RPT --> S3
  BR --> S3
  CV --> S3
  SI & GO & SC -.->|"audit jsonl (v0.12)"| S3
  ORC -.->|"run summary → runs/ (v0.12)"| S3
  PG --> PANEL
  PANEL -.->|"config push"| S3
  NT --> NO
  NT --> US
  PG --> EXP
  EXP --> US
  PG --> EL
  S3 --> EL
  IN --> NO
  DBT -.->|if bottleneck| SNF
```

---

## 2 · Ingestion — medallion landing (detail)

A zoom-in on the operational plane's first half — **how a day's jobs get from the source API to a scored shortlist**, and why each stage exists. The guarantee is *land-everything-first*: everything downstream is a **pure, replayable function of immutable bronze**. Discussed in [02-architecture · Ingestion](02-architecture.md) · [ADR-0010](adr/0010-job-source-jsearch.md).

```mermaid
flowchart TB
  subgraph SRC["① Source — JSearch (official API · single source in v0)"]
    Q["query = keywords + country + date_posted<br/>(the cheap source-side pre-filter)"]
    PAGE["paginated pull<br/>budget: queries × pages × sources ≤ quota ÷ 30"]
    Q --> PAGE
  end

  subgraph BRZ["② Bronze — land everything, immutable (no filtering)"]
    S3R[("S3<br/>raw/{source}/{date}/{id}.json")]
    BP[("bronze_posting<br/>raw_payload · 1 row / raw posting")]
  end

  subgraph SLV["③ Silver — conform · clean · dedup (pure, versioned)"]
    ADP["source adapter → common schema<br/>(Pydantic data contract)"]
    subgraph TP["text pipeline · job_description / job_title"]
      direction LR
      T1["whitelist"] --> T2["clean<br/>html·unicode·ws"] --> T3["LLM dissect · DeepSeek<br/>skills+levels · sector · title · lang"] --> T5["fingerprint"] --> T6["embed<br/>pgvector (M2)"]
    end
    DD["dedup · cluster-and-surface<br/>v0: exact source-id only"]
    ADP --> TP
    TP --> DD
  end

  subgraph GLD["④ Gold — filter to candidates (FilterStrategy port)"]
    PF["filter · likely-fit vs profile<br/>v0 default: deterministic<br/>(LLM strategy built · config-selectable)"]
  end

  SC["⑤ Score — LLM · DeepSeek<br/>runs on GOLD only"]
  CL[("cluster<br/>score + CV attach once per real job")]
  AN["analytics marts (M5/M6)"]
  AUD[("S3 audit (v0.12)<br/>silver/gold/scores jsonl<br/>+ runs/ run summary")]
  DDM["⤷ M2 — multi-source + clustering:<br/>fingerprint → pgvector blocking → apply-URL / canonical-id<br/>→ company-canon → time-window → confidence bands<br/>→ LLM adjudication → human merge"]

  PAGE -->|"all raw, untouched"| S3R
  PAGE -->|"all raw, untouched"| BP
  BP --> ADP
  DD --> PF
  PF --> SC
  DD --> CL
  SC --> CL
  PF -.->|"below-bar rows kept for analytics"| AN
  TP -.->|"silver jsonl (v0.12)"| AUD
  PF -.->|"gold jsonl (v0.12)"| AUD
  SC -.->|"scores jsonl (v0.12)"| AUD
  DD -.->|"lineage: bronze_id + pipeline_version"| BP
  BP ==>|"immutable ⇒ replay · zero new API calls"| SLV
  DD -.->|"grows at M2"| DDM
```

**Stage by stage**
- **① Source (JSearch).** The query (`keywords + country + date_posted`) *is* the pre-filter — the API won't pre-filter for us, so the query is how we pay only for plausibly-relevant pages. Cost is bounded by a **request budget**, not storage.
- **② Bronze.** Every raw result is written **untouched** — S3 `raw/…json` + a `bronze_posting` row (`raw_payload`). No filtering, ever: *whatever the API returned today is captured and replayable.*
- **③ Silver.** A source **adapter** normalizes each payload into one common schema (the Pydantic **data contract**). The heavy step is the **LLM `Dissector`** (DeepSeek — [ADR-0016](adr/0016-llm-dissection-at-silver.md)) that extracts `skills[]`+levels, sector, normalized title, language from `job_description`/`job_title`; the rest is field-mapping. Every row carries `bronze_id + pipeline_version` (**origin-level lineage**). Dedup is **cluster-and-surface** — v0 is exact source-id only.
- **④ Gold.** Silver **LLM-dissects every posting** (the market-wide analytics need *all* postings, not just gold); the **gold `FilterStrategy`** then selects the likely-fit subset for scoring. **v0's default is a *deterministic* filter** — at 10–30 jobs/day an LLM gold-filter is redundant with the Scorer (P1); the **LLM strategy is built and config-selectable** behind the same port (a defended deviation from the build plan — see journal §23). Below-bar rows stay in bronze/silver for analytics.
- **⑤ Score.** The **strong DeepSeek model** runs on gold only; score + CV attach to the **cluster** — done once per real job, every platform's apply-link kept.

**Two properties worth discussing**
- **Immutable bronze ⇒ replay.** Change a filter, the threshold, or your profile → re-derive silver→gold→score over existing bronze with **zero new API calls**. **This is live as of `v0.4.0`:** a **`{"mode":"reassess"}`** invocation re-scores the already-scored postings against the *current* profile (no fetch) so a job **graduates** `stretch`→`strong_fit` as your skills grow — `previous_score` tracks the before→after ([ADR-0023](adr/0023-reassess-replay.md)).
- **v0 vs migration.** v0 = single source + exact-id dedup. **M2** grows dedup into full clustering and adds source #2 (the dotted box).

---

## 3 · Roadmap & evolution

The directional roadmap — a **living hypothesis**, not a contract. Live status is the source of truth in [ledgers/phase-index](ledgers/phase-index.md); this is the *shape*. Discussed in [03-roadmap](03-roadmap.md).

**v0 shipped, then a P2-driven capability burst.** v0 (`v0.1.0`, 2026-06-29) deployed + live-validated + torn down to ~$0. Since then the **bottleneck protocol re-ranked the roadmap from real usage** — the pre-drawn M1–M8 was hypothesis, not contract. Shipped so far (all live-validated on the deployed stack): `v0.2.0` **M1 pipeline hardening** (the P2 protocol overruled the pre-drawn *M1 = CV tailoring*), `v0.3.0` **user-customizable settings + runtime config in S3** (change settings via `push_config.py`, no redeploy), `v0.3.1` employment_types enum, `v0.4.0` **reassess/replay** (re-score on an updated profile, no re-fetch — the graduation half of the old M4, early), `v0.5.0` **query/filter access** (export → SQLite/CSV), `v0.6.0` **email UX** (card digest + prominent Apply button), `v0.7.0` **score lineage + outcome tracking** (`score_event`/`application_event` + truthful digest), `v0.8.0` **scorer integrity** (7 bounded subscores + weighted `code_total` in shadow mode), `v0.9.0` **ops hardening** (Terraform S3 backend + 2 CloudWatch alarms → SNS + `{mode:smoke}` deploy gate), `v0.10.0` **reachable full-list** (presigned S3 report page of all scored jobs), `v0.11.0` **scorer boundary self-consistency** (median-of-N re-score near the cutoff + honest graduations), `v0.12.0` **full S3 audit persistence + a local control panel** (every stage's results → S3 JSONL; `panel.py` browse/curate/config) — the pipeline now runs **fully unattended** (daily 06:00 UTC cron; first solo flight 2026-07-10). **Next = the bottleneck protocol picks from real use** (the ⬜ below are re-derived hypotheses, not committed).

```mermaid
flowchart LR
  v0["v0.1 ✅ (v0.1.0)<br/>fetch → score → email<br/>deployed · live · $0"] --> H["M1 ✅ (v0.2.0)<br/>pipeline hardening<br/>concurrency · retry · precision"]
  H --> S3C["✅ (v0.3.0)<br/>settings + config-in-S3<br/>no-redeploy"]
  S3C --> RA["✅ (v0.4.0)<br/>reassess / replay<br/>graduation, no re-fetch"]
  RA --> QF["✅ (v0.5.0)<br/>query / filter export"]
  QF --> EU["✅ (v0.6.0)<br/>email UX · card digest"]
  EU --> LN["✅ (v0.7.0)<br/>score + outcome lineage<br/>truthful digest"]
  LN --> SI2["✅ (v0.8.0)<br/>scorer integrity<br/>7 subscores · shadow code_total"]
  SI2 --> OP["✅ (v0.9.0)<br/>ops hardening · S3 state<br/>alarms → SNS · smoke gate"]
  OP --> FL["✅ (v0.10.0)<br/>reachable full-list<br/>presigned S3 report · unattended"]
  FL --> BR2["✅ (v0.11.0)<br/>scorer boundary self-consistency<br/>median-of-N · honest graduations"]
  BR2 --> AU["✅ (v0.12.0)<br/>S3 audit persistence<br/>+ local control panel"]
  AU --> NX{{"next = P2 protocol<br/>picks from real use"}}
  NX -.-> HYP["⬜ hypotheses:<br/>CV tailoring · multi-source+dedup (M2)<br/>Step Functions (M3) · Notion+near-miss (M4)<br/>dbt marts + analytics (M5–M6)<br/>observability+calibration (M7) · v1.0 polish (M8)"]
```

Each migration is chosen by the **bottleneck-decision protocol**, not the list above:

```mermaid
flowchart LR
  S["ship a stage"] --> U["use it · observe"]
  U --> B["surface top-3 bottlenecks<br/>to the next real capability"]
  B --> R["rank by leverage<br/>capability ÷ complexity"]
  R --> D["design the minimal<br/>migration that breaks it"]
  D -->|"ADR: bottleneck → capability → solution"| S
```

---

## 4 · Analytical constellation (dimensional model)

How accumulated data compounds into insight: **conformed dimensions** shared across **facts**; insights are *joins* over them. Built at M5/M6, grown per question. Skills + canonical title are **derived from the JD text**. Discussed in [02-architecture](02-architecture.md#analytical-plane--dbt-marts-adr-0004) · [ADR-0011](adr/0011-dimensional-analytical-model.md).

```mermaid
flowchart TB
  subgraph DIMS["Conformed dimensions"]
    DD["dim_date"]
    DS["dim_skill"]
    DT["dim_title<br/>raw → canonical"]
    DC["dim_company"]
    DSE["dim_sector"]
    DL["dim_location"]
    PP["profile<br/>point-in-time (SCD2)"]
  end
  subgraph FACTS["Facts"]
    FP["fct_job_posting<br/>grain: posting/cluster"]
    FS["fct_job_skill<br/>(bridge: posting × skill)"]
    FSC["fct_job_score"]
    FA["fct_application"]
  end
  DD --> FP & FSC
  DT --> FP
  DC --> FP
  DSE --> FP
  DL --> FP
  FP --> FS
  DS --> FS
  FP --> FSC
  PP --> FSC
  FP --> FA
```

> **Priority order** (Tarig's): `dim_skill` + `fct_job_skill` first (powers skill-demand/gaps *and* sector intel) → point-in-time profile + score facts (progress trends) → `dim_sector`. `dim_title` / `dim_company` are supporting.

---

## 5 · Reassess / replay — the graduation loop (`v0.4.0`)

The medallion's **immutable-bronze → replay** property, made concrete. When your profile improves (a new skill), you re-score the jobs already in the system against the *current* profile — with **zero JSearch calls** — and a posting that was a `stretch` **graduates** to `strong_fit`. Discussed in [ADR-0023](adr/0023-reassess-replay.md) · [02-architecture · Ingestion](02-architecture.md).

```mermaid
flowchart LR
  P["you learn a skill →<br/>edit profile.local.yml"] --> PC["scripts/push_config.py<br/>validate + upload"]
  PC --> S3[("S3 · profile.yml")]
  S3 -->|read at runtime| RE["Lambda · {mode:reassess}<br/>re-score the already-scored postings<br/>against the CURRENT profile"]
  BR[("bronze · immutable<br/>already fetched")] -.->|"no new fetch — replay only"| RE
  RE --> SS["score rows updated<br/>previous_score ← old · score ← new<br/>+ score_event appended — history + lineage (0004)"]
  SS --> G{"crossed the threshold upward?"}
  G -->|yes| GR["GRADUATED<br/>stretch / near-miss → strong_fit"]
  G -->|no| UN["unchanged / downgraded"]
```

> Live-proven: bumped Spark `learning → expert` → `push_config` → `{mode:reassess}` → **180 re-scored, 15 graduated** (e.g. Data Platform Engineer @ Saudi Aramco 35→85), **bronze unchanged** (no re-fetch).

---

## 6 · Runtime config in S3 — change settings, no redeploy (`v0.3.0`)

Config is read from **S3 at runtime**, not baked into the Lambda zip — so changing any setting is one command, not a rebuild + `terraform apply`. Discussed in [ADR-0022](adr/0022-runtime-config-in-s3.md).

```mermaid
flowchart TB
  subgraph OLD["Before — config bundled in the Lambda zip"]
    direction LR
    E1["edit YAML"] --> B1["rebuild pkg"] --> T1["terraform apply"] --> R1["redeploy (slow)"]
  end
  subgraph NEW["v0.3.0 — config in S3, read at runtime"]
    direction LR
    E2["edit *.local.yml"] --> P2["push_config.py<br/>validate → upload"] --> S2[("S3 · config/*.yml")]
    S2 -->|"next run reads it"| L2["handler<br/>from_yaml_text"] --> EFF["takes effect<br/>NO rebuild · NO terraform"]
  end
```

> The same seam powers the reassess loop (§5) and is the foundation for a settings UI — now **realized by the v0.12.0 control panel's Config tab** (`scripts/panel.py`), which validates the edit then writes the same S3 object ([ADR-0033](adr/0033-local-control-panel.md)).

---

## 7 · Read + curate surfaces (`v0.5.0` export · `v0.12.0` control panel)

Two surfaces over your records: **`export.py`** snapshots to a portable file for a purpose-built viewer (no custom UI), and the **`v0.12.0` local control panel** is the live browse/curate/config view. Discussed in [ADR-0024](adr/0024-query-via-export.md) · [ADR-0033](adr/0033-local-control-panel.md) · [querying.md](querying.md).

```mermaid
flowchart LR
  PG[("Aurora · posting · score<br/>bronze · run_log · profile<br/>score_event · application_event")] --> EX["scripts/export.py<br/>flatten JSONB→text · join"]
  EX --> SQ[("export/jobs.sqlite")]
  EX --> CSV[("export/jobs.csv")]
  SQ --> DS["Datasette<br/>faceted filter · full-text search"]
  SQ --> DB2["DB Browser / sqlite3"]
  CSV --> XL["Excel / Sheets"]
  PG <-->|"live browse + curate"| PN["scripts/panel.py (v0.12)<br/>Streamlit · browse/filter · override/outcome · config form<br/>(local, optional panel extra)"]
```

---

*The operational data model (ERD) and the operational flow live inline in [02-architecture](02-architecture.md). Add new diagrams here as the design evolves — keep them Mermaid.*
