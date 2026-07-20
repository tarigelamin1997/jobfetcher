# capture.tf — the public capture endpoint (INV-001 Rung 2).
#
# WHAT: a second Lambda (SAME deployment zip, a different handler entry point) exposed via a
#       public Lambda Function URL, plus the HMAC signing key it verifies against. The digest/
#       report "Mark applied / interview / …" links hit this URL → the handler verifies the
#       short-lived signed token → records ONE outcome via the existing write path.
# WHY:  the dark-feedback-loop (INV-001): outcome capture was CLI-only, so the log stayed empty.
#       One click from the inbox now lands a row. Auth is the TOKEN, not the network — the URL is
#       `authorization_type = "NONE"` (public) but every request must carry a valid HMAC token
#       scoped to {posting_id, status} with a TTL (the v0.10.0 presigned-report pattern).
# SO-WHAT: closes the loop that blocks M7 scoring calibration, at the cost of exactly one small
#       public surface whose blast radius is bounded (single-status, posting-scoped, expiring).
#
# The signing key is generated + owned by Terraform (a SELF-owned secret, not a third-party
# credential — unlike deepseek/jsearch which are CLI-created data sources). State lives in the
# private S3 backend, so `random_password` in state is acceptable here.

resource "random_password" "capture_key" {
  length  = 48
  special = false # alphanumeric — a clean HMAC secret with no shell/URL-escaping surprises
}

resource "aws_secretsmanager_secret" "capture_key" {
  name        = var.capture_key_secret_name
  description = "HMAC signing key for JobFetcher capture-link tokens (INV-001). Terraform-owned."
}

resource "aws_secretsmanager_secret_version" "capture_key" {
  secret_id     = aws_secretsmanager_secret.capture_key.id
  secret_string = random_password.capture_key.result
}

# ── Least-privilege execution role for the capture Lambda ────────────────────
# Narrower than the pipeline role: it needs the Data API + the two secrets the DB engine and the
# token verify require + Logs. NO S3, NO SES, NO deepseek/jsearch, NO bedrock.
resource "aws_iam_role" "capture" {
  name               = "jobfetcher-${var.env}-capture"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json # reuse the pipeline's assume doc
}

data "aws_iam_policy_document" "capture_policy" {
  # Read ONLY the two secrets this endpoint needs: the token signing key + the Aurora master
  # password secret (the Data API authenticates the cluster call with it).
  statement {
    sid     = "ReadCaptureSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.capture_key.arn,
      aws_rds_cluster.main.master_user_secret[0].secret_arn,
    ]
  }

  # RDS Data API: exactly the statements track_application_event's transaction issues.
  statement {
    sid = "DataApi"
    actions = [
      "rds-data:ExecuteStatement",
      "rds-data:BatchExecuteStatement",
      "rds-data:BeginTransaction",
      "rds-data:CommitTransaction",
      "rds-data:RollbackTransaction",
    ]
    resources = [aws_rds_cluster.main.arn]
  }

  # CloudWatch Logs for the capture function's own log group.
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/jobfetcher-${var.env}-capture*"]
  }
}

resource "aws_iam_role_policy" "capture" {
  name   = "jobfetcher-${var.env}-capture"
  role   = aws_iam_role.capture.id
  policy = data.aws_iam_policy_document.capture_policy.json
}

# ── The capture Lambda — the SAME zip as the pipeline, a different handler ─────
resource "aws_lambda_function" "capture" {
  function_name = "jobfetcher-${var.env}-capture"
  role          = aws_iam_role.capture.arn
  runtime       = var.lambda_runtime

  # INV-001 entry point, in the same package built by scripts/build_lambda.py (build/lambda/).
  handler = "jobfetcher.handlers.capture.handler"

  # Reuse the pipeline's build zip — one artifact, two entry points (P1: no second package).
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  timeout     = 30  # one token verify + one small Data-API write (+ Aurora cold-resume headroom)
  memory_size = 256 # tiny work; no LLM, no S3, no concurrency

  # Cap the public endpoint's concurrency: real use is a human clicking a few links/day, so 5 is
  # generous. Reserving it also (a) bounds the cost/blast-radius of token-spam against the public
  # URL, and (b) walls the capture Lambda off from the account concurrency pool so it can never
  # starve the pipeline Lambda. The DB is already protected (verify fails before any DB touch).
  reserved_concurrent_executions = 5

  environment {
    variables = {
      ENV = var.env
      # Data API connection (no JOBFETCHER_DB_URL → resolve_db_url builds the Aurora URL).
      DB_CLUSTER_ARN = aws_rds_cluster.main.arn
      DB_SECRET_ARN  = aws_rds_cluster.main.master_user_secret[0].secret_arn
      DB_NAME        = var.db_name
      # The signing-key secret the handler verifies tokens against.
      CAPTURE_KEY_SECRET_NAME = aws_secretsmanager_secret.capture_key.name
      LOG_LEVEL               = "INFO"
    }
  }
}

# ── The public Function URL — auth is the TOKEN, not IAM ──────────────────────
resource "aws_lambda_function_url" "capture" {
  function_name      = aws_lambda_function.capture.function_name
  authorization_type = "NONE" # public: every request carries a signed, expiring HMAC token
}

# A public Function URL still needs an explicit resource policy allowing the (unauthenticated)
# InvokeFunctionUrl action — without it the URL returns 403.
resource "aws_lambda_permission" "capture_url" {
  statement_id           = "AllowPublicFunctionUrlInvoke"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.capture.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
