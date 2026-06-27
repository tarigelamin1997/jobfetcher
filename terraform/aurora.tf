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
  engine_version     = "16.6"        # supports Serverless v2 + Data API + scale-to-0 + pgvector
  database_name      = var.db_name

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
}

resource "aws_rds_cluster_instance" "main" {
  identifier         = "jobfetcher-${var.env}-1"
  cluster_identifier = aws_rds_cluster.main.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.main.engine
  engine_version     = aws_rds_cluster.main.engine_version
}
