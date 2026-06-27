# lambda.tf — the single v0 pipeline Lambda (STUB package for now).
#
# WHAT: zips a tiny inline `handler.py` (via the archive provider) and deploys it as
#       the pipeline function with config env vars (names/ARNs only — NO secret values).
# WHY:  the real handler (fetch->dissect->filter->score->notify) is a later build
#       step; deploying a stub now makes the infra complete + applyable + destroyable
#       end-to-end so IAM/EventBridge/SES wiring is validated. Runtime python3.11.
# SO-WHAT: a real deploy at Step 10 only swaps the package; the surrounding infra is done.

data "archive_file" "lambda_stub" {
  type        = "zip"
  source_dir  = "${path.module}/lambda_stub"
  output_path = "${path.module}/build/lambda_stub.zip"
}

resource "aws_lambda_function" "pipeline" {
  function_name = "jobfetcher-${var.env}-pipeline"
  role          = aws_iam_role.lambda.arn
  runtime       = var.lambda_runtime
  handler       = "handler.handler"

  filename         = data.archive_file.lambda_stub.output_path
  source_code_hash = data.archive_file.lambda_stub.output_base64sha256

  timeout     = 300 # 5 min — daily batch with LLM calls + ~15s Aurora cold-resume
  memory_size = 512

  # Config only — NO secret VALUES. The handler fetches secret values at runtime
  # via the Data API / Secrets Manager using the names/ARNs below.
  environment {
    variables = {
      ENV                  = var.env
      DATA_BUCKET          = aws_s3_bucket.data.id
      DB_CLUSTER_ARN       = aws_rds_cluster.main.arn
      DB_SECRET_ARN        = aws_rds_cluster.main.master_user_secret[0].secret_arn
      DB_NAME              = var.db_name
      DEEPSEEK_SECRET_NAME = var.deepseek_secret_name
      JSEARCH_SECRET_NAME  = var.jsearch_secret_name
      SES_SENDER           = var.sender_email
      RECIPIENT_EMAIL      = var.recipient_email
    }
  }
}
