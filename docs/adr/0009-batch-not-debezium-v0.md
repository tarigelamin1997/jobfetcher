# ADR-0009 — Batch EL now; Debezium CDC as a documented scale-path

## Status
Accepted

## Context
"Streaming/CDC" is a portfolio signal Tarig wants, and Postgres + Debezium mirrors his real DE expertise (Barakah/OrderFlow). A tweak to adopt **Debezium CDC** (Postgres → warehouse) was considered for moving data into the analytical plane. But at ~10–30 rows/day, real-time CDC is not justified by need, and full Kafka/MSK is disproportionately costly/complex. Debezium-on-a-trickle is the textbook resume-driven red flag.

## Decision
Move data from the operational plane into analytics via **simple batch extract-load + incremental dbt**. **Debezium CDC is the documented scale-up path** — adopted only if a real latency/volume bottleneck appears (then via its own ADR + migration). The *built* CDC/Debezium showcase lives in the sibling **OrderFlow** project, where streaming is genuinely warranted.

## Alternatives Considered
- **Debezium CDC now (the tweak).** Deferred: not justified by load; honest framing "I right-sized to batch and documented the CDC path" is a *stronger* interview answer than running CDC over 30 rows/day.
- **Debezium Server → Kinesis (no Kafka cluster).** This is the *preferred form* of the future migration (avoids MSK cost) — recorded here as the scale-path, not built now.
- **Full Kafka/MSK CDC.** Rejected outright at this scale (cost/complexity); noted only as the production-scale end of the path.

## Consequences
- **Easier:** v0/early stack stays cheap and reliable; analytics is plain, idempotent batch + incremental dbt.
- **Harder:** the "live CDC" keyword isn't demonstrated *in this repo* until the conditional trigger — accepted (it's demonstrated in OrderFlow).
- **Impact:** consistent with [ADR-0002] (tool-minimalism) and [ADR-0004] (Snowflake conditional): both Debezium and Snowflake are documented scale-paths, ready when a real bottleneck calls for them.
