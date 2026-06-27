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
