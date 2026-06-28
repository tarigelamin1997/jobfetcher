# lambda.tf — the single v0 pipeline Lambda.
#
# WHAT: zips the REAL handler package (built by `scripts/build_lambda.py` into build/lambda/) via
#       the archive provider and deploys it as the pipeline function with config env vars
#       (names/ARNs/paths only — NO secret values).
# WHY:  one Lambda is the minimal orchestration (build-plan Step 7). The `handler` entry point
#       targets `jobfetcher.handlers.pipeline.handler`; Step 10 vendors src/ + the Linux runtime
#       deps (pydantic, pyyaml, SQLAlchemy, sqlalchemy-aurora-data-api) + the config YAMLs into
#       build/lambda/. boto3/botocore are runtime-provided (pruned from the package); the Data-API
#       path (ADR-0018) means psycopg2 is unneeded; alembic is migrations-only (run from local).
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

  timeout     = 300 # 5 min — daily batch with LLM calls + ~15s Aurora cold-resume
  memory_size = 512

  # Config only — NO secret VALUES. The handler fetches secret values at runtime
  # via the Data API / Secrets Manager using the names/ARNs below; config files are
  # read from the SEARCH_CONFIG_PATH / PROFILE_PATH paths (bundled at Step 10).
  environment {
    variables = {
      ENV = var.env
      # The env-var name the S3RawStore adapter actually reads (s3_raw.py: $JOBFETCHER_DATA_BUCKET).
      JOBFETCHER_DATA_BUCKET = aws_s3_bucket.data.id
      DB_CLUSTER_ARN       = aws_rds_cluster.main.arn
      DB_SECRET_ARN        = aws_rds_cluster.main.master_user_secret[0].secret_arn
      DB_NAME              = var.db_name
      DEEPSEEK_SECRET_NAME = var.deepseek_secret_name
      JSEARCH_SECRET_NAME  = var.jsearch_secret_name
      SES_SENDER           = var.sender_email
      RECIPIENT_EMAIL      = var.recipient_email
      SEARCH_CONFIG_PATH   = var.search_config_path
      PROFILE_PATH         = var.profile_path
    }
  }
}
