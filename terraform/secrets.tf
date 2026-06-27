# secrets.tf — REFERENCE existing app secrets (do NOT create them).
#
# WHAT: data sources resolve the ARNs of the DeepSeek + JSearch secrets that were
#       created out-of-band via the AWS CLI under jobfetcher-dev.
# WHY:  secrets are created/rotated via CLI, never owned by Terraform and never in
#       the repo (CLAUDE.md: no secrets in code; one secret per service
#       `jobfetcher/<service>`). Terraform only needs their ARNs to scope IAM.
# SO-WHAT: the Lambda role grants GetSecretValue on exactly these two ARNs (plus the
#       Aurora-managed master-password secret, defined in aurora.tf) — least-privilege.

data "aws_secretsmanager_secret" "deepseek" {
  name = var.deepseek_secret_name
}

data "aws_secretsmanager_secret" "jsearch" {
  name = var.jsearch_secret_name
}
