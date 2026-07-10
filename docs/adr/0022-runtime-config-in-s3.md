# ADR-0022 — Runtime config in S3 (settings without a redeploy)

**Status:** Accepted · shipped v0.3.0 (2026-07-03)
**Date:** 2026-07-03

## Context

After the settings work ([plan §37](../../)), the "normal job-seeker" knobs (search targeting, the three shortlist-strictness knobs, the profile) are all user-editable — but only by editing a YAML that is **bundled into the Lambda deployment zip**, which means **rebuild + `terraform apply`** to change *any* value. Tarig: *"this is not efficient or user-friendly — I shouldn't reconfigure and redeploy every time."*

The root cause is not "literals vs variables" — it is that **config lives inside the immutable deployment artifact**. Any value baked into the zip needs a redeploy to change. Variable-substitution (his first idea) only helps if the variables resolve at *runtime*; as Lambda env vars they'd be flat strings that fight the list/nested fields (`job_titles`, `skills`, `avoid_keywords`), cap at 4 KB, and still require an AWS API call — not user-friendly.

The wiring made a clean fix cheap: the Lambda role **already** grants `s3:GetObject` on the whole data bucket (`iam.tf`), the bucket is **already** in the Lambda env (`JOBFETCHER_DATA_BUCKET`), and YAML parsing is separable from file-reading (both loaders reduce to `model_validate(yaml.safe_load(text))`).

## Decision

**Move the two config YAMLs out of the Lambda zip and into S3; the Lambda reads them at runtime, on every run.** Changing a setting becomes: **edit the local YAML → `python scripts/push_config.py`** (a one-command upload, seconds) → the next run uses it. **No rebuild, no `terraform apply`.**

- **Loaders:** `SearchSpec.from_yaml_text(text)` / `Profile.from_yaml_text(text)` (parse + validate a string); `from_yaml(path)` funnels through them (local dev/tests unchanged).
- **`adapters/s3_config.py`:** `S3ConfigStore` (mirrors `S3RawStore`; `get_object` → text; a missing object → `ConfigNotFound` with the actionable next step) + `read_config_text(location)` — a **scheme dispatch**: an `s3://bucket/key` URI reads from S3, anything else is a local file path.
- **Handler:** `SearchSpec.from_yaml_text(read_config_text(resolve_search_config_path(env)))` (and the profile likewise). The env var (`SEARCH_CONFIG_PATH` / `PROFILE_PATH`) is now an `s3://` URI in deployment, a local path in tests.
- **`scripts/push_config.py`:** the everyday "apply my settings" command — **validates** both YAMLs (a broken edit fails here, never reaches S3) then uploads them to `s3://<data-bucket>/config/{search_config,profile}.yml`.
- **Build:** `scripts/build_lambda.py` no longer bundles `config/*.yml` (dropped `copy_config` + the presence guards). The package is config-free.
- **Terraform:** the `SEARCH_CONFIG_PATH` / `PROFILE_PATH` env vars become `s3://${data-bucket}/${key}` URIs; two `aws_s3_object` resources **seed** the config on first apply from the local files, with **`lifecycle { ignore_changes = all }`** so a later `apply` **never clobbers** a runtime edit (the update path is `push_config.py`, not Terraform). No IAM change (GetObject is already bucket-wide).

## Alternatives considered

- **Keep config bundled in the zip** (status quo). Rejected: every settings change needs a rebuild + redeploy — the exact pain this removes.
- **Config as Lambda env vars** (Tarig's variable-substitution idea). Rejected: env vars are flat strings — awkward/ugly for the list + nested fields (`job_titles`, `skills`), a 4 KB total cap, and changing them is still an `update-function-configuration` AWS call, not user-friendly. It only half-decouples.
- **SSM Parameter Store / AppConfig.** Rejected for now: AWS-native runtime config, but more moving parts (params/profiles, extra IAM, deploy config) than one S3 object read — overkill at this scale (P1). Reconsider if we need config validation/rollout gates.
- **DB-authority + a settings writer (CLI/API).** Deferred: the `profile` row already caches settings, so making the DB the *source* + a writer is the natural seam toward a real settings **UI** and multi-user — but it's a bigger build. S3 is the minimal decoupling that removes the redeploy today; the UI grows from here (it would write the same S3 object or the DB).

## Consequences

- **A settings change needs no redeploy** — edit the YAML, `push_config.py`, done. This is the "no-redeploy settings surface" flagged as a follow-on in the §37 work, delivered.
- **The Lambda package is config-free** (smaller, and the same artifact serves any config).
- **New failure mode, handled:** a missing S3 config → `ConfigNotFound` (a clear, actionable error), surfaced as the handler's `500`.
- **Seam to a UI:** an eventual settings form writes the same S3 object (or the DB) — no pipeline change needed.
- **Config is not versioned** in S3 (bucket versioning is OFF) — a bad edit has no S3 rollback yet; `push_config.py`'s pre-upload validation is the current guard. S3 versioning for config-rollback is an easy later add.

Full reasoning: [journal](../01-session-decision-journal.md) · plan §38. Related: [ADR-0015](0015-type-replaceable-pipeline-stages.md) (ports), [ADR-0020](0020-lambda-deployment-packaging.md) (packaging).
