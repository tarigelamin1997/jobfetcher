# ADR-0008 — Region: us-east-1

## Status
Accepted

## Context
The stack needs a region with reliable **Bedrock model availability** (Claude), low cost, and easy co-location with any future warehouse. Data residency is **not** a hard constraint (Tarig did not select it). The old plan used eu-north-1 with an EU cross-region inference profile.

## Decision
Deploy in **us-east-1**.

## Alternatives Considered
- **eu-north-1 (the old region).** Rejected (for the fresh build): cheaper marginally, but Bedrock model/inference-profile availability is broadest and best-documented in us-east-1, removing a class of "model not available / ValidationException" gotchas. Residency isn't required.
- **me-central-1 (UAE — closest to KSA).** Rejected: Bedrock model availability is limited there; latency is irrelevant for a daily batch job.

## Consequences
- **Easier:** widest Bedrock model choice + most examples/docs; easy Snowflake co-location if/when conditional warehouse is adopted.
- **Harder:** none material at this scale.
- **Impact:** if residency ever becomes a requirement (e.g. a multi-user KSA pivot), this is revisited via a new ADR.
