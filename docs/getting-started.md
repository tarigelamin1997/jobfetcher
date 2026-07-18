# Getting Started — clone to first run

> A single, self-contained walkthrough: from `git clone` to a scored digest in your inbox, then teardown to ~$0. Everything you need is here; the links go **deeper**, they're never *required*.
>
> **Audience:** anyone with an AWS account who wants to run JobFetcher (an interviewer evaluating the repo, or a new user). Comfortable with the terminal; you do **not** need to know this codebase.
>
> **Time / cost:** ~30–45 min the first time. Idle cost is **~$0** (Aurora scales to zero); a run costs pennies of LLM (DeepSeek) and fits JSearch's free tier.

---

## 0 · What you'll have + what you need

**End state:** one scheduled AWS Lambda that, daily, fetches jobs → scores them against *your* profile with an LLM → emails you a ranked, explained shortlist, with a link to a full filterable page of every scored job.

**Accounts (all have free tiers):**
- An **AWS account** (this guide uses region **us-east-1**).
- A **DeepSeek** account for the LLM — sign up at [platform.deepseek.com](https://platform.deepseek.com), create an API key. *(Free signup tokens may not apply; a ~$2 balance covers a lot of runs.)*
- A **JSearch** subscription (job data) via RapidAPI — subscribe to **JSearch Basic (free, 200 req/mo)** and copy your key. *(Detail: [build-plan Step 0](04-v0-build-plan.md) · [ADR-0010](adr/0010-job-source-jsearch.md).)*

**Local tools:** **Python 3.11**, **Terraform ≥ 1.10**, **AWS CLI v2** (configured — see step 2), **git**. *(Docker is optional — only for the integration tests in [`tests/README.md`](../tests/README.md). Two optional local extras: `[panel]` = the Streamlit control panel, `[query]` = the Datasette viewer — neither ships in the Lambda; see [§12](#12--day-to-day-no-redeploy).)*

---

## 1 · Clone + Python

```bash
git clone https://github.com/tarigelamin1997/jobfetcher.git
cd jobfetcher
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
pip install pre-commit && pre-commit install           # optional (ruff + gitleaks on commit)

python -m pytest -m "not integration" -q               # sanity: unit suite is green, no AWS needed
```

## 2 · AWS credentials

Configure an AWS identity in **us-east-1** with permission to create the stack (Aurora, S3, Lambda, EventBridge, SES, IAM, CloudWatch/SNS, Secrets Manager reads). *(The project's own dev identity is `jobfetcher-dev`; you use **your own** credentials.)*

```bash
aws configure                       # or SSO / a named profile
aws sts get-caller-identity         # confirm the account id + region
```

## 3 · Get the two API keys

- **DeepSeek** — sign in at [platform.deepseek.com](https://platform.deepseek.com) → create an API key (and fund a small balance if needed).
- **JSearch** — on RapidAPI, subscribe to **JSearch → Basic (free)** → copy the `X-RapidAPI-Key`.

## 4 · Store the keys in Secrets Manager

The names are what the Lambda + Terraform expect (defaults in [`terraform/variables.tf`](../terraform/variables.tf)). **Never commit a key** — these live only in Secrets Manager.

```bash
aws secretsmanager create-secret --region us-east-1 \
  --name jobfetcher/deepseek --secret-string '<YOUR_DEEPSEEK_KEY>'
aws secretsmanager create-secret --region us-east-1 \
  --name jobfetcher/jsearch  --secret-string '<YOUR_JSEARCH_KEY>'
```

## 5 · Verify your SES emails

SES starts in **sandbox** (fine for personal use), where **both** the sender **and** the recipient must be verified. Verify each, then click the confirmation link AWS emails you.

```bash
aws ses verify-email-identity --region us-east-1 --email-address <sender@example.com>
aws ses verify-email-identity --region us-east-1 --email-address <you@example.com>   # can be the same address
```

## 6 · Configure your search + profile

Copy the committed samples to the **gitignored** local files and fill them in. Every field is required — the `SearchSpec` fails loudly on anything missing/invalid, and your real profile/PII never enters the repo.

```bash
cp config/search_config.sample.yml config/search_config.local.yml   # your titles · countries · threshold · budget
cp config/profile.sample.yml       config/profile.local.yml         # your CV/profile — the scoring source of truth
```

## 7 · ⚠️ Bootstrap your own Terraform state bucket

Terraform state lives in a **remote S3 bucket that must survive `terraform destroy`**, so it's created **once, by you, outside the stack**. The repo ships with the *owner's* bucket name pinned — **you must change it to your own.**

```bash
# 7a — create YOUR state bucket (globally-unique; account id keeps it unique) + turn on versioning
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws s3api create-bucket --region us-east-1 --bucket "jobfetcher-tfstate-${ACCOUNT}"
aws s3api put-bucket-versioning --bucket "jobfetcher-tfstate-${ACCOUNT}" \
  --versioning-configuration Status=Enabled
```

**7b — edit [`terraform/providers.tf`](../terraform/providers.tf)**: in the `backend "s3"` block, change `bucket = "jobfetcher-tfstate-198592435375"` to **`jobfetcher-tfstate-<your-account-id>`**. *(This is the #1 thing a cloner must change — the committed value is the project owner's account.)*

## 8 · Set the two required Terraform variables

Everything else has a sensible default ([`variables.tf`](../terraform/variables.tf)); only the two emails are required. Put them in a **gitignored** `terraform/terraform.tfvars`:

```hcl
# terraform/terraform.tfvars
sender_email    = "<sender@example.com>"      # verified in step 5
recipient_email = "<you@example.com>"         # verified in step 5 (sandbox needs both)
```

## 9 · Deploy

```bash
python scripts/build_lambda.py                 # package the Lambda (vendors Linux wheels; no Docker)
terraform -chdir=terraform init                # connects to YOUR S3 backend
terraform -chdir=terraform apply               # ~21 resources; also SEEDS your config/*.local.yml → S3

# migrate the schema on Aurora, over the Data API. Grab the ARNs terraform just created:
aws lambda get-function-configuration --function-name jobfetcher-dev-pipeline --region us-east-1 \
  --query 'Environment.Variables.{cluster:DB_CLUSTER_ARN,secret:DB_SECRET_ARN,db:DB_NAME}'
# then point Alembic at Aurora (fill the two ARNs from above) and migrate:
export JOBFETCHER_DB_URL="postgresql+auroradataapi://:@/jobfetcher?aurora_cluster_arn=<CLUSTER_ARN>&secret_arn=<SECRET_ARN>"
alembic upgrade head
```

Then **confirm the SNS alarm email** AWS sends to your recipient address (click *Confirm subscription* — alarms deliver nothing until you do; [runbook §4](runbooks/deploy.md)).

> **Ordering matters** (the deploy-order rules the runbook makes mechanical): `apply` creates the infra **and** seeds your config; the schema **must** be migrated before the pipeline runs; a settings change later is just `scripts/push_config.py`, no redeploy. Full sequence + the three migrate-order classes: [runbooks/deploy.md](runbooks/deploy.md).

## 10 · Verify — the smoke gate + first run

```bash
# smoke gate: proves the deployed code reaches the DB at the schema it expects (zero side effects)
AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name jobfetcher-dev-pipeline \
  --cli-binary-format raw-in-base64-out --cli-read-timeout 120 \
  --payload '{"mode":"smoke"}' smoke-out.json && cat smoke-out.json
# → {"statusCode": 200, "mode": "smoke", "alembic_version": "0006_subscores", ...}   200 or you stop.

# a real run now (or just wait for the 06:00 UTC cron):
AWS_MAX_ATTEMPTS=1 aws lambda invoke --function-name jobfetcher-dev-pipeline \
  --cli-read-timeout 900 --payload '{}' run-out.json && cat run-out.json
```

A `statusCode 200` and a **digest email** means you're live. *(New senders often land in **Spam** the first time — open it, mark **"Not spam"**, add the sender to contacts; Gmail learns. See [B-2 in the backlog](ledgers/backlog.md).)* Then snapshot your data:

```bash
python scripts/export.py            # → export/jobs.sqlite + jobs.csv — open in Datasette / DB Browser / Excel
```

## 11 · Teardown (back to ~$0)

```bash
terraform -chdir=terraform destroy  # tears the stack down; Aurora scales to 0 between runs regardless
```

Your **state bucket and Secrets Manager keys survive by design** (the state bucket must outlive the stack; the keys are reused). Delete them manually only if you're done for good.

---

## 12 · Day-to-day (no redeploy)

Once it's up, the routine loop is config + invokes — the Lambda zip stays put. See the README [Day-to-day](../README.md#day-to-day-no-redeploy-needed) for the full set:

- **Change any setting** → edit `config/*.local.yml` → `python scripts/push_config.py` (takes effect next run, no rebuild).
- **Re-score on a better profile** → add a skill, push config, invoke `{"mode":"reassess"}` → `stretch` roles graduate to `strong_fit` ([ADR-0023](adr/0023-reassess-replay.md)).
- **Record outcomes / override a score** → `python scripts/track.py applied <posting_id>` · `track.py override <posting_id> <score>` ([ADR-0026](adr/0026-outcome-tracking-override-lineage.md)).
- **Preview the email** → `python scripts/preview_digest.py`.
- **Browse + curate live (control panel)** → `pip install -e '.[panel]'` → `streamlit run scripts/panel.py`: a **local Streamlit app** to browse/filter every scored job, override a score / record an outcome, and edit your search config → push to S3 — all against the live DB, no redeploy ([ADR-0033](adr/0033-local-control-panel.md)).
- **Query a portable snapshot** → `pip install -e '.[query]'` → `python scripts/export.py` → open `export/jobs.sqlite` in Datasette ([ADR-0024](adr/0024-query-via-export.md)).

---

## 13 · Troubleshooting

| Symptom | Cause → fix |
|---|---|
| Smoke gate returns **400** (`migration mismatch`) | Schema behind the code → run `alembic upgrade head` (step 9) and re-invoke. |
| Smoke gate returns **500** | DB unreachable (a scale-to-0 Aurora resume can take ~30 s — it's waited out, [ERR-009](ledgers/errors.md); or check the ARNs/IAM), **or** never migrated (run the first `alembic upgrade head`). The `error` field disambiguates. |
| A run fails with a **`SearchSpec`/`Profile` ValidationError** | A required config field is missing/invalid → fix `config/*.local.yml`, `python scripts/push_config.py`. |
| DeepSeek **401** / `model not found` | Wrong key in `jobfetcher/deepseek`, or a bad model id → recheck step 4. |
| No email arrives | SES sender **or** recipient not verified (step 5), or still `PendingConfirmation` on SNS; in sandbox both must be verified. |
| The digest is in **Spam** | Expected for a new SES sender with no domain → mark "Not spam" + add to contacts. The real fix needs a sending domain ([B-2](ledgers/backlog.md)). |
| `terraform init` errors on the backend | You skipped step 7b — point the `backend "s3"` bucket at **your** state bucket. |

---

*Next: the [architecture](02-architecture.md) · the [roadmap](03-roadmap.md) · why every choice was made — the [decision journal](01-session-decision-journal.md).*
