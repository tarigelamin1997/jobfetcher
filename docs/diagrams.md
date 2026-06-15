# Diagrams

> The visual index. All repo diagrams are **Mermaid** — they render inline on GitHub and in VS Code's preview, version with the code, and never drift (the text *is* the picture). Edit any block here, or paste it into [mermaid.live](https://mermaid.live) to tweak.
>
> **Convention:** Mermaid is canonical and lives in the repo. [Eraser](https://app.eraser.io) is an optional *personal/portfolio* view (prettier AWS icons, authored via diagram-as-code) — it is **not** committed here to keep the repo text-light. A live link can be shared for the portfolio when wanted.

---

## 1 · Full-stack architecture (target)

The complete two-plane design. **v0 is a small subset** (one Lambda → fetch → score → email); everything else arrives by migration. Discussed in [02-architecture](02-architecture.md).

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
      SI["Silver<br/>clean · dedup · embed"]
      GO["Gold<br/>profile filter"]
    end
    ORC --> BR --> SI --> GO --> SC["Score<br/>Bedrock"]
    SC -->|">= threshold"| CV["cv_tailor (M1)<br/>DOCX + PDF"]
    SC --> NT["notify<br/>SES + Notion"]
    CV --> NT
    PG[("Postgres<br/>+ pgvector")]
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

## 2 · Roadmap & evolution

The directional roadmap — a **living hypothesis**, not a contract. Live status is the source of truth in [ledgers/phase-index](ledgers/phase-index.md); this is the *shape*. Discussed in [03-roadmap](03-roadmap.md).

```mermaid
flowchart LR
  v0["v0.1 ⬜ next<br/>fetch → score → email"] --> M1["M1 ⬜<br/>CV tailoring"]
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

## 3 · Analytical constellation (dimensional model)

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
