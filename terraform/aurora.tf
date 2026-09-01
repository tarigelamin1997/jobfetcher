# aurora.tf — Aurora PostgreSQL Serverless v2 + RDS Data API (ADR-0014).
#
# WHAT: a single-instance Aurora SLv2 cluster reachable over the HTTPS Data API,
#       scaling to 0 ACU when idle, with the master password managed by AWS.
# WHY:  ADR-0014 — the Lambda stays OUTSIDE any VPC and calls the DB over the Data
#       API (no Postgres wire protocol, no NAT/endpoints). `min_capacity = 0`
#       (scale-to-0) → ~$0 idle between daily runs. `manage_master_user_password`
#       → AWS stores the master password in Secrets Manager (NO password literal).
#       pgvector ships with the engine; `CREATE EXTENSION vector` is an M2 concern
#       (run as SQL then, NOT here).
# SO-WHAT: serverless, VPC-free, destroyable-to-$0 operational store.

# ── Networking: default VPC (P1 — no custom networking) ──────────────────────
# The cluster needs a subnet group + a security group. Aurora is reached only via
# the Data API HTTPS endpoint, so this SG can stay closed (no ingress); it exists
# because RDS requires one. The Lambda is NOT in this VPC.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "aurora" {
  name       = "jobfetcher-${var.env}-aurora"
  subnet_ids = data.aws_subnets.default.ids
}

# Closed security group: no ingress rules. Data API access is IAM-authorized over
# AWS's managed HTTPS endpoint, not via a network path into this SG.
resource "aws_security_group" "aurora" {
  name        = "jobfetcher-${var.env}-aurora"
  description = "Aurora SLv2 cluster SG (Data API only; no direct ingress)."
  vpc_id      = data.aws_vpc.default.id

  egress {
    description = "Allow all outbound (default)."
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── Cluster ──────────────────────────────────────────────────────────────────
resource "aws_rds_cluster" "main" {
  cluster_identifier = "jobfetcher-${var.env}"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # required for Serverless v2
  # MAJOR ONLY, deliberately. `auto_minor_version_upgrade` is true (below/default), so AWS
  # owns the minor: it bumped this cluster 16.6 -> 16.11 on its own. A hard minor pin here
  # contradicts that — Terraform then tries to DOWNGRADE the live database on every apply,
  # which AWS rejects ("Cannot find upgrade target from 16.11 with requested version 16.6")
  # and which blocked the ERR-010 deploy on 2026-09-01. Tracking the major lets AWS patch
  # minors while a MAJOR upgrade stays deliberate and explicit. 16 supports Serverless v2 +
  # Data API + scale-to-0 + pgvector.
  engine_version = "16"
  database_name  = var.db_name

  master_username = "jobfetcher_admin"
  # NO password literal — AWS generates + stores it in Secrets Manager and rotates ownership.
  manage_master_user_password = true

  # RDS Data API (HTTPS) — lets the out-of-VPC Lambda query without the wire protocol.
  enable_http_endpoint = true

  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  serverlessv2_scaling_configuration {
    min_capacity = 0 # scale-to-0 → ~$0 idle (ADR-0014)
    max_capacity = var.db_max_acu
  }

  # Destroyable: no deletion protection, skip the final snapshot so destroy → $0.
  deletion_protection = false
  skip_final_snapshot = true
  apply_immediately   = true

  lifecycle {
    # ERR-012. `auto_minor_version_upgrade` is true, so AWS owns the minor version and moves
    # it without us (it took this cluster 16.6 -> 16.11 on its own). Terraform reads that as
    # drift and tries to push it back: at best a pointless ModifyDBCluster, at worst a
    # downgrade AWS refuses outright -- which is exactly what blocked the ERR-010 deploy.
    # A major-only `engine_version` does NOT suppress the diff on this provider (verified
    # 2026-09-01: it still plans `16.11 -> "16"`), so the reconciliation must be turned off.
    #
    # The literal above is still load-bearing: it governs cluster CREATION, so a fresh apply
    # after the destroy cadence still lands on 16.x (Data API + scale-to-0 + pgvector). This
    # only stops Terraform re-asserting the version on an EXISTING cluster.
    #
    # A MAJOR upgrade (16 -> 17) will NOT apply while this is here. That is deliberate --
    # major upgrades are planned, human-present operations -- but it means this
    # `ignore_changes` has to be removed for the duration of one. See docs/runbooks/deploy.md.
    ignore_changes = [engine_version]
  }

  # ── Encryption at rest: DELIBERATELY NOT ENABLED (decided 2026-09-01, Tarig) ───────────
  # NOTE TO ANY FUTURE READER, HUMAN OR AGENT: do NOT "helpfully" add `storage_encrypted`
  # here. Its absence is a recorded decision, not an oversight, and enabling it is NOT a
  # safe edit — the argument cannot change in place, so Terraform DESTROYS and RECREATES the
  # cluster, and with `skip_final_snapshot = true` + `deletion_protection = false` above it
  # does so silently and unrecoverably. It would turn the next routine deploy into data loss.
  #
  # The decision: this is experimental data — one ~1.5 KB profile row (no contact PII, per
  # the config/profile.sample.yml convention) plus public job postings. Encryption at rest
  # only defends against reading raw storage without authenticating (AWS's disks, a copied
  # snapshot); it is transparent to anyone holding credentials, so it does nothing against
  # the realistic risks here — a leaked AWS key, a leaked DB secret, an app bug. We know it
  # is the expected production default and we are choosing not to pay the recreate for it
  # yet. Rationale in full: docs/adr/0038-aurora-unencrypted-at-rest.md.
  #
  # When this data stops being throwaway, the procedure is docs/runbooks/deploy.md §5.
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "jobfetcher-${var.env}-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version

  lifecycle {
    # Mirrors the cluster (ERR-012): the instance inherits the cluster's version, so it
    # inherits the same fight with AWS auto-minor-upgrade.
    ignore_changes = [engine_version]
  }
}
