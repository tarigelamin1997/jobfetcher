# Runbook · Deploy / Release

> **What:** the standard release sequence (with the three migrate-order classes), the post-apply smoke gate, the ONE-TIME local→S3 state migration, and the SNS confirmation step. **Why:** deploy-order mistakes are the one error class tests can't catch (ERR-004/005 lesson — some failures only exist live); this runbook makes the order mechanical. **So-what:** any session (human or agent) deploys the same way, and a schema/code mismatch is caught by a 5-second invoke instead of the next morning's failed run. Procedure detail sources: [procedure registry](../ledgers/procedure-registry.md) rows *config-first*, *migrate-before-first-use*, *migrate-before-DEPLOY*, *invocation pattern*.

## 1 · Standard release sequence

1. **CI green** on the release PR → merge → tag. Never deploy a red branch.
2. **Config first** — *only if* the release adds/changes a **required `SearchSpec` field**: edit the local YAML → `python scripts/push_config.py`. Deploying code before the config makes every run fail loudly until the push (registry: *config-first*).
   > ⚠️ **A new required field is a COUPLED change — there is no safe ordering, only a short window.** `SearchSpec` is `extra="forbid"` with every field required, so the config and the code that reads it must match *exactly*: push the config first and the still-deployed old code rejects it as an unknown field; deploy the code first and it fails on the missing required field. Either way the next run breaks. So run the push and the apply **back-to-back, and not near 06:00 UTC** — the only thing that can land in the gap is the daily cron. (Not applicable to a release that adds no required field, e.g. migration 0007 / [ADR-0036](../adr/0036-gold-filter-rejection-lineage.md), which deliberately avoided one.)
3. **Migrate** — `alembic upgrade head` (over the Data API, per Step-10 procedure). Order class decides *when it must happen*:
   - **migrate-before-DEPLOY** (run-fatal — the *pipeline* writes the new column unconditionally, e.g. 0006 `subscores`): the migration is a **precondition of step 5**, not of first use.
     Also **0007 `gold_filter_hash`** — the gold filter stamps it on every rejection, so the column must exist before the first run. And bump `ALEMBIC_HEAD` in **both** `terraform/lambda.tf` **and** `_EXPECTED_MIGRATION_HEAD` in `handlers/pipeline.py`: the unit test pins only the second, while the env var **overrides** it at runtime — a stale `lambda.tf` lets the smoke gate pass against the wrong schema (backlog **B-7**).
   - **migrate-before-first-use** (script-consuming, e.g. 0005 `application_event`): before the first `track.py`/new-export use is enough.
   - When in doubt: migrate first — our migrations are additive, safe ahead of the code.
4. **Build** — `python scripts/build_lambda.py` (build/lambda/ must be current before apply).
5. **Deploy** — `terraform apply` (from `terraform/`).
6. **Smoke gate** — run the §2 one-liner; **200 or you stop**. 400 = schema behind code → go back to step 3. 500 = DB unreachable → fix before anything else runs.
7. **First apply with alarms only** — SNS confirmation click (§4).
8. Check the **live-smoke watch items** row in the procedure registry for release-specific checks.

## 2 · Post-apply smoke gate

The `{"mode":"smoke"}` invoke proves *the deployed Lambda reaches the DB and the schema is at the head this code expects* — zero side effects (no fetch, no LLM, no email, no writes).

```bash
AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name jobfetcher-dev-pipeline \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 120 \
  --payload '{"mode":"smoke"}' smoke-out.json && cat smoke-out.json
```

- **PASS:** `{"statusCode": 200, "mode": "smoke", "alembic_version": "0007_gold_filter_hash", ...}` — the version must equal the `ALEMBIC_HEAD` env var terraform pinned (update both per migration: `lambda.tf` + `_EXPECTED_MIGRATION_HEAD` in `handlers/pipeline.py`; a unit test catches a stale constant).
- **400** (`migration mismatch`): the DB is migrated, but to the **wrong head** → `alembic upgrade head`, re-invoke.
- **500:** the Lambda can't reach the DB (Aurora paused + timeout, IAM, ARNs) — **or** the DB is reachable but was **never migrated at all** (missing/empty `alembic_version` table makes the SELECT itself fail). The `error` field tells them apart: an `UndefinedTable`/`NoResultFound` = run the first `alembic upgrade head`; a connect/timeout error = infra.
- `AWS_MAX_ATTEMPTS=1` + `--cli-read-timeout 120` per the *invocation pattern* registry row (ERR-008: the CLI silently re-invokes slow sync calls); the smoke itself is one `SELECT`, but a scale-to-0 Aurora resume can take ~30 s.

## 3 · ONE-TIME state migration (local → S3) — human-present

The backend block ships in the repo (`terraform/providers.tf`); moving the *existing* state is a one-time manual procedure. The bucket is deliberately **unmanaged** (never a resource in this config): state must survive `terraform destroy`.

1. **Backup first** (Castle: document/copy before anything destructive): copy `terraform/terraform.tfstate` (and `.backup` if present) to a safe location outside the repo.
2. **Create the bucket ONCE** (CLI, `jobfetcher` profile; us-east-1 takes no LocationConstraint). Versioning on — state history is the recovery path:
   ```bash
   aws s3api create-bucket --bucket jobfetcher-tfstate-198592435375 --region us-east-1
   aws s3api put-bucket-versioning --bucket jobfetcher-tfstate-198592435375 \
     --versioning-configuration Status=Enabled
   ```
3. The `backend "s3"` block is already in `providers.tf` (this release) — nothing to edit.
4. **Migrate:** `terraform init -migrate-state` (from `terraform/`), answer `yes` when it offers to copy local state to S3.
5. **Verify: `terraform plan` must show ZERO changes.** Any drift = the state didn't carry — **STOP**, restore the backup, investigate. Do not apply.
6. Only after a zero-drift plan: delete the local `terraform.tfstate` / `terraform.tfstate.backup` (destructive — the backup from step 1 stays).

## 4 · SNS alarm subscription — confirmation click

The first `terraform apply` with `alarms.tf` creates the topic + email subscription, but **email subscriptions deliver nothing until confirmed** — the alarms fire into the void.

1. Open the *"AWS Notification - Subscription Confirmation"* email at the digest recipient address → click **Confirm subscription**.
2. Verify: `aws sns list-subscriptions-by-topic --topic-arn <topic arn from terraform output/console>` — the subscription must show a real ARN, not `PendingConfirmation`.
3. Re-check after any `destroy`→`apply` cycle: recreating the subscription re-requires the click.

## 5 · Encrypting Aurora at rest (ONE-TIME, destroys the cluster)

> **Deferred by decision, 2026-09-01 — [ADR-0038](../adr/0038-aurora-unencrypted-at-rest.md).**
> The cluster is unencrypted and that is deliberate while the data is experimental. There is
> **no commented-out setting in `terraform/aurora.tf`** to uncomment: a destroy trigger one
> keystroke away is a hazard, not documentation. This section is the procedure for when the
> decision is revisited — it lives here precisely because it cannot be executed by accident.

**Why it is not simply enabled.** `storage_encrypted` cannot change in place — Terraform
**destroys and recreates** the cluster. With `skip_final_snapshot = true` and
`deletion_protection = false` (both set, deliberately, for the teardown cadence) that happens
silently and unrecoverably: adding the argument turns the next routine code deploy into total
data loss. Encryption itself is **free** — the AWS-managed `aws/rds` key has no monthly charge
— so cost is not what defers this. The recreate is.

**Why do it at all, given the risk.** Not threat-driven — encryption at rest only defends
against reading raw storage without authenticating (AWS's disks, a copied snapshot). It is
transparent to anyone holding credentials, so it does nothing against a leaked AWS key or a
leaked DB secret. The database holds one ~1.5 KB profile row (no contact PII) and public job
postings. The real reasons: it is free, "unencrypted RDS" is the first thing any AWS security
review flags, and the migration cost only grows with the data.

**The cheap window (use it while the data is still experimental):**

1. `python scripts/export.py` — snapshot anything worth keeping to SQLite/CSV. Everything else
   is reproducible: bronze re-fetches, scores re-derive.
2. Add `storage_encrypted = true` to `aws_rds_cluster.main` in `terraform/aurora.tf`
   (replacing the do-not-add note there).
3. `terraform -chdir=terraform destroy` → `terraform -chdir=terraform apply`.
4. `alembic upgrade head` · `python scripts/push_config.py` · §2 smoke gate (**200 or stop**).
5. Confirm: `aws rds describe-db-clusters --query 'DBClusters[0].StorageEncrypted'` → `true`.

Once the data stops being replaceable this becomes a snapshot → encrypted-copy → restore
migration instead, which is a different and much longer procedure.

## 6 · Aurora MAJOR version upgrades (the `ignore_changes` caveat)

`terraform/aurora.tf` carries `lifecycle { ignore_changes = [engine_version] }` on both the
cluster and its instance ([ERR-012](../ledgers/errors.md)). That is what stops Terraform
fighting AWS's `auto_minor_version_upgrade` — which had it attempting to **downgrade the live
database on every apply**, blocking a deploy outright.

The cost: **a major upgrade will not apply while it is there.** Minor versions are AWS's and
need no action. For a major (e.g. 16 → 17):

1. Read the Aurora PostgreSQL major-upgrade notes and take a manual snapshot first — this one
   is not the throwaway-data path.
2. Temporarily remove `ignore_changes = [engine_version]` from **both** `aws_rds_cluster.main`
   and `aws_rds_cluster_instance.main`.
3. Set the new major in `engine_version`, `terraform plan`, and **read it** — a major upgrade
   is a real, disruptive operation, not an in-place attribute change.
4. Apply, verify, then put `ignore_changes` back.

Never leave it removed: without it the next AWS minor patch re-creates ERR-012.

