# ADR-0020 — Lambda deployment packaging: Linux wheels, no Docker

## Status
Accepted · **✅ Validated live (v0.1.0, 2026-06-29)** — `scripts/build_lambda.py` staged a Linux/x86-64 package on Windows (no Docker); the resulting zip deployed via Terraform and ran end-to-end on AWS (`statusCode 200`, fetch → … → notify).

## Context
The single v0 pipeline Lambda ([Step 7](../04-v0-build-plan.md), `handlers/pipeline.py`) has a compiled dependency (`pydantic_core`, a native `.so`/`.pyd`) and several pure-Python deps. It must be packaged for the **Linux/x86-64** Lambda runtime — but the build machine is **Windows**, and the operator avoids Docker (thermal limits on the dev laptop). So the build step has to vendor *Linux* wheels from a Windows host without containers. The package must also stay small: a leaked Windows binary, or bundling deps the runtime already provides, are the two ways this goes wrong. ADR-0018 put DB access on the **Data API** (no `psycopg2` at runtime); ADR-0017 put the LLM on an HTTPS API (boto3 only for SES/Secrets/S3, which the runtime ships).

## Decision
A pure-Python **`scripts/build_lambda.py`** build step, run **before `terraform apply`**:
- **Vendor Linux wheels via pip** — `pip install --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.11` into a staging dir (`terraform/build/lambda/`), so the compiled `pydantic_core` is the Linux `.so`, never the local Windows `.pyd`. A **guard asserts** no forbidden top-level Windows/runtime artifact leaked (`FORBIDDEN_TOP_LEVEL`).
- **Bundle** the `jobfetcher` source package + the two config YAMLs alongside the deps.
- **Prune `boto3`/`botocore`** (and `s3transfer`/`jmespath`/`dateutil`/`six`/`urllib3`) **after install** — they arrive only transitively (via `aurora-data-api`) but the Lambda runtime already provides them; pruning keeps the zip under the **50 MB direct-upload limit** and avoids shadowing the runtime's newer boto3. `psycopg2` (Data-API path) and `alembic` (migrations-only, run from local at deploy) are excluded entirely.
- **Direct `filename` zip** — Terraform's archive provider zips the staging dir and uploads it directly (<50 MB), no S3 object or layer.

## Alternatives Considered
- **Docker / AWS SAM build (`sam build --use-container`).** The standard way to get correct Linux binaries. **Rejected** — the dev machine has thermal limits and the operator deliberately avoids Docker; `pip --platform manylinux2014_x86_64 --only-binary` gets the same Linux wheels with no container.
- **A Lambda layer for the dependencies.** Rejected — more moving parts (a separate artifact + version + attach) for one function whose deps already fit a <50 MB direct zip; layers earn their place when deps are large or shared across many functions.
- **A container-image Lambda (ECR).** Rejected — reintroduces Docker (the thing we're avoiding) and a heavier deploy for no benefit at this size.
- **Bundle boto3/botocore in the package.** Rejected — ~40 MB of dead weight the runtime already provides, and it can shadow the runtime's newer boto3; pruning them is what keeps the package under the direct-upload limit.

## Consequences
- **Easier:** no Docker/containers in the build; reproducible Linux package from a Windows host; small zip → cheap cold start; one `python scripts/build_lambda.py` before `terraform apply`.
- **Harder:** the build step must **target Linux explicitly even when built on Windows** (the `--platform`/`--only-binary` flags + the leaked-binary guard are load-bearing); a new compiled dep that lacks a `manylinux` wheel would force a rethink (then a layer or container becomes the justified migration).
- **Impact:** adds the `scripts/build_lambda.py` build step ahead of every deploy; pairs with [ADR-0014](0014-operational-store-aurora-serverless-data-api.md) (Data API ⇒ no `psycopg2`) and [ADR-0017](0017-llm-transport-openai-compatible-deepseek.md) (HTTPS LLM ⇒ boto3 is runtime-provided). The full-backfill scale finding (a single Lambda can't do the 18-query × 30-day backfill in 15 min) is a *runtime* limit, not a packaging one → reinforces M3 (Step Functions), tracked in the [phase index](../ledgers/phase-index.md).
