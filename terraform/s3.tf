# s3.tf — one bucket: raw bronze landing (raw/...) + CV artifacts (cv/...).
#
# WHAT: a single private, encrypted bucket; `force_destroy = true` so a later
#       `terraform destroy` returns the bill to ~$0 with no manual emptying.
# WHY:  v0 lands raw JSearch payloads (immutable bronze) + (M1) CVs. One bucket,
#       prefix-separated, is the minimal store (P1). Globally-unique name via the
#       account id avoids collisions without a random suffix.

locals {
  bucket_name = "jobfetcher-${var.env}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name

  # Destroyable: empties objects on destroy so the stack tears down to $0.
  force_destroy = true
}

# Block ALL public access — this bucket is internal-only.
resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Default server-side encryption (SSE-S3 / AES256). No KMS key needed at v0 scale.
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Versioning OFF at v0: bronze is already immutable-by-convention (date-partitioned,
# never overwritten) and versioning adds storage cost + teardown friction. (P1)

# ── Runtime config (ADR-0022) ──────────────────────────────────────────────
# The two config YAMLs live in S3 (read by the Lambda at runtime), NOT bundled in the zip —
# so a settings change is `scripts/push_config.py`, no rebuild/redeploy. Terraform SEEDS them
# on first apply from the local files; `ignore_changes = all` means a later `apply` NEVER
# clobbers a runtime edit (the update path is push_config.py / the eventual UI, not Terraform).
resource "aws_s3_object" "search_config" {
  bucket = aws_s3_bucket.data.id
  key    = var.search_config_key
  source = "${path.module}/../config/search_config.local.yml"

  lifecycle {
    ignore_changes = all
  }
}

resource "aws_s3_object" "profile" {
  bucket = aws_s3_bucket.data.id
  key    = var.profile_key
  source = "${path.module}/../config/profile.local.yml"

  lifecycle {
    ignore_changes = all
  }
}
