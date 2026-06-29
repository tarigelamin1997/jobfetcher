# Diagrams

> The visual index. All repo diagrams are **Mermaid** — they render inline on GitHub and in VS Code's preview, version with the code, and never drift (the text *is* the picture). Edit any block here, or paste it into [mermaid.live](https://mermaid.live) to tweak.
>
> **Convention:** Mermaid is canonical and lives in the repo. [Eraser](https://app.eraser.io) is an optional *personal/portfolio* view (prettier AWS icons, authored via diagram-as-code) — it is **not** committed here to keep the repo text-light. A live link can be shared for the portfolio when wanted.

---

## 1 · Full-stack architecture (target)

The complete two-plane design (high-level). **The shipped v0.1.0 is a small subset** (one Lambda → fetch → score → SES email, on Aurora SLv2 via the RDS Data API + S3 — **deployed + live-validated 2026-06-29**); everything else arrives by migration. The **ingestion medallion is detailed in §2 below**; the LLM is **provider-agnostic** ([ADR-0012](adr/0012-model-agnostic-llm.md) · [ADR-0017](adr/0017-llm-transport-openai-compatible-deepseek.md)) and v0 runs on **DeepSeek** via the OpenAI-compatible API. Discussed in [02-architecture](02-architecture.md).

```mermaid
flowchart TB
  subgraph EXT["External"]
    JS["JSearch API<br/>(Google-for-Jobs)"]
    NO["Notion"]
    US["You (email)"]
  end

  subgraph OP["Operational plane — AWS serverless"]
    EB["EventBridge<br/>daily cron"] --> ORC["Lambda v0<br/>→ Step Functions (M3)"]
    subgraph MED["Medallion"]
      BR["Bronze<br/>raw · immutable"]
      SI["Silver<br/>clean · LLM dissect · dedup"]
      GO["Gold<br/>profile filter<br/>(v0: deterministic default)"]
    end
    ORC --> BR --> SI --> GO --> SC["Score<br/>LLM · DeepSeek (provider-agnostic)"]
    SC -->|">= threshold"| CV["cv_tailor (M1)<br/>DOCX + PDF"]
    SC --> NT["notify<br/>SES digest (+ Notion M4)<br/>run_log send-once / day"]
    CV --> NT
    PG[("Postgres — Aurora SLv2<br/>via RDS Data API · + pgvector")]
    S3[("S3<br/>raw + CVs")]
    SM["Secrets Manager"]
    CF["Profile / Config<br/>threshold 60"]
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
  CF -->|runtime| SC
  SM -.->|creds| ORC
  BR --> S3
  CV --> S3
  NT --> NO
  NT --> US
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
  DDM["⤷ M2 — multi-source + clustering:<br/>fingerprint → pgvector blocking → apply-URL / canonical-id<br/>→ company-canon → time-window → confidence bands<br/>→ LLM adjudication → human merge"]

  PAGE -->|"all raw, untouched"| S3R
  PAGE -->|"all raw, untouched"| BP
  BP --> ADP
  DD --> PF
  PF --> SC
  DD --> CL
  SC --> CL
  PF -.->|"below-bar rows kept for analytics"| AN
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
- **Immutable bronze ⇒ replay.** Change a filter, the threshold, or your profile → re-derive silver→gold→score over existing bronze with **zero new API calls**.
- **v0 vs migration.** v0 = single source + exact-id dedup. **M2** grows dedup into full clustering and adds source #2 (the dotted box).

---

## 3 · Roadmap & evolution

The directional roadmap — a **living hypothesis**, not a contract. Live status is the source of truth in [ledgers/phase-index](ledgers/phase-index.md); this is the *shape*. Discussed in [03-roadmap](03-roadmap.md).

**v0 is SHIPPED** — all 10 steps (probe → dissect → schema → infra → fetch/silver → gold → score → notify → handler → CI → deploy) built and verified through the agentic gate pipeline ([ADR-0019](adr/0019-agentic-build-orchestration.md)), then **deployed to AWS, validated live end-to-end, and torn down to ~$0** (tag **`v0.1.0`**, 2026-06-29). The migration markers (M1–M8) below are still all ⬜ — **next = the bottleneck protocol → M1**.

```mermaid
flowchart LR
  v0["v0.1 ✅ shipped (v0.1.0)<br/>fetch → score → email<br/>deployed · live-validated · torn down to $0"] --> M1["M1 ⬜<br/>CV tailoring"]
  M1 --> M2["M2 ⬜<br/>multi-source + dedup"]
  M2 --> M3["M3 ⬜<br/>Step Functions"]
  M3 --> M4["M4 ⬜<br/>Notion + near-miss"]
  M4 --> M5["M5 ⬜<br/>dbt marts"]
  M5 --> M6["M6 ⬜<br/>skill + sector intel"]
  M6 --> M7["M7 ⬜<br/>observability + calibration"]
  M7 --> M8["M8 ⬜ → v1.0.0<br/>CI/CD + README + demo"]
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

*The operational data model (ERD) and the operational flow live inline in [02-architecture](02-architecture.md). Add new diagrams here as the design evolves — keep them Mermaid.*
