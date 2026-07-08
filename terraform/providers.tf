# providers.tf — provider + backend configuration
#
# WHAT: pins the AWS + archive providers and selects the S3 remote state backend.
# WHY:  reproducibility is the portfolio value (CLAUDE.md); pinning prevents silent
#       provider drift. AWS provider >= 5.80 is required for Aurora Serverless v2
#       `min_capacity = 0` (scale-to-0) — earlier versions reject 0 ACU.
# SO-WHAT: a clone-and-`terraform init` lands on a known-good provider set.
#
# BACKEND: S3 remote state (Run 5 — replaces the v0 local-state file). The state bucket is
#   deliberately UNMANAGED — created ONCE via the CLI, never a resource in this config —
#   because the state must OUTLIVE the stack: a `terraform destroy` (the end-of-day teardown
#   cadence) that deleted its own state bucket would orphan every future apply. Locking is
#   Terraform's native S3 lockfile (`use_lockfile`, TF >= 1.10) — no DynamoDB table needed.
#   The ONE-TIME migration off local state (backup → create bucket → `terraform init
#   -migrate-state` → zero-drift plan → remove local files) is a human-present procedure:
#   docs/runbooks/deploy.md §3.
#   NOTE for CI/validate: `terraform init -backend=false && terraform validate` still works
#   without AWS creds — the backend block is parsed, not initialized.

terraform {
  required_version = ">= 1.10" # native S3 state locking (`use_lockfile`) shipped in 1.10

  backend "s3" {
    bucket       = "jobfetcher-tfstate-198592435375"
    key          = "jobfetcher/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

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
