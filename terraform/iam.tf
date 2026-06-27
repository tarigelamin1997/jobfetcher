# iam.tf — least-privilege execution role for the pipeline Lambda.
#
# WHAT: a role assumable by Lambda + a tightly-scoped inline policy.
# WHY:  CLAUDE.md / decisions-locked: runtime IAM = least-privilege, NO Bedrock
#       (the LLM is DeepSeek over HTTPS — ADR-0017). Each grant is the exact action
#       on the exact ARN the handler needs; a runtime AccessDenied is fixed by
#       adding one specific permission, never `*`.
# SO-WHAT: the security signal of the portfolio + the safe default.

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "jobfetcher-${var.env}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda_policy" {
  # ── Secrets: read ONLY the three secrets this pipeline uses ────────────────
  # deepseek + jsearch (app keys) and the Aurora-managed master-password secret.
  statement {
    sid     = "ReadAppSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      data.aws_secretsmanager_secret.deepseek.arn,
      data.aws_secretsmanager_secret.jsearch.arn,
      aws_rds_cluster.main.master_user_secret[0].secret_arn,
    ]
  }

  # ── RDS Data API: query the cluster (no bedrock, no rds:* admin) ───────────
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

  # ── S3: object R/W on this bucket only + list (for prefix scans) ───────────
  statement {
    sid       = "S3Objects"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    sid       = "S3List"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }

  # ── SES: send the daily digest ─────────────────────────────────────────────
  statement {
    sid       = "SesSend"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"] # SES send is gated by verified identities, not resource ARN
  }

  # ── CloudWatch Logs: basic Lambda execution logging ────────────────────────
  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/jobfetcher-${var.env}*"]
  }

  # NOTE: deliberately NO bedrock:* (ADR-0017) and NO rds:* admin actions.
}

resource "aws_iam_role_policy" "lambda" {
  name   = "jobfetcher-${var.env}-lambda"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}
