# variables.tf — all tunable inputs for the v0 stack.

variable "region" {
  description = "AWS region. us-east-1 per ADR-0008 (widest model availability; residency not required)."
  type        = string
  default     = "us-east-1"
}

variable "env" {
  description = "Environment slug, used in resource names + tags (e.g. dev, prod)."
  type        = string
  default     = "dev"
}

variable "sender_email" {
  description = "Verified SES sender identity for the daily digest. REQUIRED — no default (set in gitignored terraform.tfvars)."
  type        = string
}

variable "recipient_email" {
  description = "Destination for the daily digest. REQUIRED — no default (set in gitignored terraform.tfvars). NOTE: in SES sandbox the recipient must ALSO be a verified identity (see ses.tf), so verify it manually before the first send."
  type        = string
}

variable "db_name" {
  description = "Initial database name created inside the Aurora cluster."
  type        = string
  default     = "jobfetcher"
}

variable "db_max_acu" {
  description = "Aurora Serverless v2 max capacity (ACU). min is 0 (scale-to-0). Daily batch needs little headroom."
  type        = number
  default     = 2
}

variable "schedule_expression" {
  description = "EventBridge schedule for the daily pipeline run. Default 06:00 UTC (~09:00 AST Riyadh)."
  type        = string
  default     = "cron(0 6 * * ? *)"
}

variable "lambda_runtime" {
  description = "Python runtime for the pipeline Lambda."
  type        = string
  default     = "python3.11"
}

# Names of the app secrets that ALREADY EXIST in Secrets Manager (created via CLI).
# Terraform only READS their ARNs (data sources) — it never creates/owns them.
variable "deepseek_secret_name" {
  description = "Name of the existing DeepSeek API-key secret."
  type        = string
  default     = "jobfetcher/deepseek"
}

variable "jsearch_secret_name" {
  description = "Name of the existing JSearch API-key secret."
  type        = string
  default     = "jobfetcher/jsearch"
}

# S3 object KEYS for the two config YAMLs (ADR-0022). The handler reads them from S3 at runtime
# (env vars below become s3://<data-bucket>/<key>), so a settings change = edit the YAML +
# `scripts/push_config.py` — no Lambda rebuild/redeploy.
variable "search_config_key" {
  description = "S3 object key (in the data bucket) for the SearchSpec YAML the handler reads."
  type        = string
  default     = "config/search_config.yml"
}

variable "profile_key" {
  description = "S3 object key (in the data bucket) for the Profile YAML the handler reads."
  type        = string
  default     = "config/profile.yml"
}
