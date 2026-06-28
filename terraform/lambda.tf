# lambda.tf — the single v0 pipeline Lambda.
#
# WHAT: zips the handler package (via the archive provider) and deploys it as the pipeline
#       function with config env vars (names/ARNs/paths only — NO secret values).
# WHY:  one Lambda is the minimal orchestration (build-plan Step 7). The `handler` entry point
#       now targets the real handler `jobfetcher.handlers.pipeline.handler`; actually building
#       the deployable package (vendoring src/ + deps + the config files) is the Step-10 deploy
#       concern. The stub package is kept here ONLY as a placeholder source so the infra stays
#       applyable/destroyable end-to-end until Step 10 replaces the packaging.
# SO-WHAT: Step 10 swaps `source_dir`/packaging for the real wheel; the surrounding infra is done.

data "archive_file" "lambda_stub" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_stub"
  output_path = "${path.module}/build/lambda_stub.zip"
}

resource "aws_lambda_function" "pipeline" {
  function_name = "jobfetcher-${var.env}-pipeline"
  role          = aws_iam_role.lambda.arn
  runtime       = var.lambda_runtime

  # The real entry point (Step 7). The deployable package that actually contains this module +
  # its deps is wired at Step 10; this name is what that package must expose.
  handler = "jobfetcher.handlers.pipeline.handler"

  # Placeholder package until Step 10 (the real packaging). Kept so the infra applies/destroys.
  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

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
