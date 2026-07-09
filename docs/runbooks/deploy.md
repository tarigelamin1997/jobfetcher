# Runbook · Deploy / Release

> **What:** the standard release sequence (with the three migrate-order classes), the post-apply smoke gate, the ONE-TIME local→S3 state migration, and the SNS confirmation step. **Why:** deploy-order mistakes are the one error class tests can't catch (ERR-004/005 lesson — some failures only exist live); this runbook makes the order mechanical. **So-what:** any session (human or agent) deploys the same way, and a schema/code mismatch is caught by a 5-second invoke instead of the next morning's failed run. Procedure detail sources: [procedure registry](../ledgers/procedure-registry.md) rows *config-first*, *migrate-before-first-use*, *migrate-before-DEPLOY*, *invocation pattern*.

## 1 · Standard release sequence

1. **CI green** on the release PR → merge → tag. Never deploy a red branch.
2. **Config first** — *only if* the release adds/changes a **required `SearchSpec` field**: edit the local YAML → `python scripts/push_config.py`. Deploying code before the config makes every run fail loudly until the push (registry: *config-first*).
3. **Migrate** — `alembic upgrade head` (over the Data API, per Step-10 procedure). Order class decides *when it must happen*:
   - **migrate-before-DEPLOY** (run-fatal — the *pipeline* writes the new column unconditionally, e.g. 0006 `subscores`): the migration is a **precondition of step 5**, not of first use.
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

- **PASS:** `{"statusCode": 200, "mode": "smoke", "alembic_version": "0006_subscores", ...}` — the version must equal the `ALEMBIC_HEAD` env var terraform pinned (update both per migration: `lambda.tf` + `_EXPECTED_MIGRATION_HEAD` in `handlers/pipeline.py`; a unit test catches a stale constant).
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
