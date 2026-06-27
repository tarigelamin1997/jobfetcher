# providers.tf — provider + backend configuration
#
# WHAT: pins the AWS + archive providers and selects the local state backend for v0.
# WHY:  reproducibility is the portfolio value (CLAUDE.md); pinning prevents silent
#       provider drift. AWS provider >= 5.80 is required for Aurora Serverless v2
#       `min_capacity = 0` (scale-to-0) — earlier versions reject 0 ACU.
# SO-WHAT: a clone-and-`terraform init` lands on a known-good provider set.
#
# BACKEND (P1 minimalism): LOCAL state for v0 — a single operator, one workstation,
#   `terraform.tfstate` gitignored. A documented LATER HARDENING is an S3 remote
#   backend (+ DynamoDB lock table) for multi-operator / CI state — NOT built now
#   (no real bottleneck yet). To migrate later: add a `backend "s3"` block here and
#   `terraform init -migrate-state`.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.80"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "jobfetcher"
      Env       = var.env
      ManagedBy = "terraform"
    }
  }
}

# Account id is used to build a globally-unique S3 bucket name and resource ARNs.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
