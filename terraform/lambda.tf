# lambda.tf — the single v0 pipeline Lambda.
#
# WHAT: zips the REAL handler package (built by `scripts/build_lambda.py` into build/lambda/) via
#       the archive provider and deploys it as the pipeline function with config env vars
#       (names/ARNs/paths only — NO secret values).
# WHY:  one Lambda is the minimal orchestration (build-plan Step 7). The `handler` entry point
#       targets `jobfetcher.handlers.pipeline.handler`; Step 10 vendors src/ + the Linux runtime
#       deps (pydantic, pyyaml, SQLAlchemy, sqlalchemy-aurora-data-api) into build/lambda/. The
#       config YAMLs are NOT bundled (ADR-0022) — they live in S3, read at runtime. boto3/botocore
#       are runtime-provided (pruned); the Data-API path (ADR-0018) means psycopg2 is unneeded.
# SO-WHAT: the staged dir is ~37 MB unzipped → the zip is well under the 50 MB direct-upload limit,
#       so the function takes the zip directly via `filename` (no S3 object indirection needed).
#
# PREREQUISITE: run `python scripts/build_lambda.py` before `terraform apply` so build/lambda/
# exists and is current. (build/ is gitignored — it is a generated artifact, never committed.)

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/build/lambda"
  output_path = "${path.module}/build/lambda.zip"
}

resource "aws_lambda_function" "pipeline" {
  function_name = "jobfetcher-${var.env}-pipeline"
  role          = aws_iam_role.lambda.arn
  runtime       = var.lambda_runtime

  # The real entry point (Step 7), exposed by the package built into build/lambda/ (Step 10).
  handler = "jobfetcher.handlers.pipeline.handler"

  # The real deployment package (built by scripts/build_lambda.py). Direct zip upload — the
  # archive is comfortably under the 50 MB direct-upload limit, so no S3 indirection.
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  timeout = 900 # 15 min (max) — each posting is an LLM dissect/score + a Data-API write
  # (~30s over the network), so the daily batch needs real headroom (vs the Aurora cold-resume too).
  # H-2: dissect/score run up to $PIPELINE_MAX_WORKERS (default 8) concurrent LLM calls, and the
  # handler's deadline guard stops new work ~60s before this timeout (partial runs resume).
  memory_size = 1024 # Lambda CPU scales with memory — 8 worker threads + TLS need the headroom

  # Config only — NO secret VALUES. The handler fetches secret values at runtime
  # via the Data API / Secrets Manager using the names/ARNs below; the search spec + profile
  # are read from S3 at runtime via the SEARCH_CONFIG_PATH / PROFILE_PATH s3:// URIs (ADR-0022).
  environment {
    variables = {
      ENV = var.env
      # The env-var name the S3RawStore adapter actually reads (s3_raw.py: $JOBFETCHER_DATA_BUCKET).
      JOBFETCHER_DATA_BUCKET = aws_s3_bucket.data.id
      DB_CLUSTER_ARN         = aws_rds_cluster.main.arn
      DB_SECRET_ARN          = aws_rds_cluster.main.master_user_secret[0].secret_arn
      DB_NAME                = var.db_name
      DEEPSEEK_SECRET_NAME   = var.deepseek_secret_name
      JSEARCH_SECRET_NAME    = var.jsearch_secret_name
      SES_SENDER             = var.sender_email
      RECIPIENT_EMAIL        = var.recipient_email
      # Config is read from S3 at runtime (ADR-0022) — s3://<data-bucket>/<key>. Change a
      # setting with `scripts/push_config.py` (no rebuild/redeploy).
      SEARCH_CONFIG_PATH = "s3://${aws_s3_bucket.data.id}/${var.search_config_key}"
      PROFILE_PATH       = "s3://${aws_s3_bucket.data.id}/${var.profile_key}"
      # Update per migration — the {"mode":"smoke"} gate pins deployed code to the migrated
      # schema: post-apply it compares the DB's alembic_version to this (200 match / 400 not).
      ALEMBIC_HEAD = "0006_subscores"
      # Telemetry verbosity for the `jobfetcher` package logger (ERR-009 rider) — the code
      # defaults to INFO when unset; this entry just makes the knob IaC-visible.
      LOG_LEVEL = "INFO"
      # INV-001: the capture endpoint the "Mark applied" links point at + the signing-key secret
      # name. The pipeline signs the links (build_capture_link); the capture Lambda verifies them.
      # An empty CAPTURE_BASE_URL would just disable the links (graceful) — here it is always set.
      CAPTURE_BASE_URL        = aws_lambda_function_url.capture.function_url
      CAPTURE_KEY_SECRET_NAME = aws_secretsmanager_secret.capture_key.name
    }
  }
}

# H-2 / ERR-007: a timed-out async invoke must NEVER be blind-retried by AWS — the live P2 run
# showed the default (2 retries) re-fetching the whole sweep after a timeout, burning JSearch
# quota + LLM tokens on a run that would only time out again. Retries are the pipeline's own
# job (idempotent resume via EventBridge tomorrow / a manual re-invoke), not the platform's.
resource "aws_lambda_function_event_invoke_config" "pipeline" {
  function_name          = aws_lambda_function.pipeline.function_name
  maximum_retry_attempts = 0
}
