# outputs.tf — the handful of values the operator / later steps need.

output "data_bucket_name" {
  description = "S3 bucket for raw bronze landing + CVs."
  value       = aws_s3_bucket.data.id
}

output "aurora_cluster_arn" {
  description = "Aurora cluster ARN — the resource the Data API (rds-data) targets."
  value       = aws_rds_cluster.main.arn
}

output "aurora_cluster_identifier" {
  description = "Aurora cluster identifier."
  value       = aws_rds_cluster.main.cluster_identifier
}

output "aurora_http_endpoint_enabled" {
  description = "Whether the RDS Data API (HTTP endpoint) is enabled — readiness signal."
  value       = aws_rds_cluster.main.enable_http_endpoint
}

output "db_master_secret_arn" {
  description = "ARN of the AWS-managed Aurora master-password secret (used by the Data API)."
  value       = aws_rds_cluster.main.master_user_secret[0].secret_arn
}

output "lambda_function_name" {
  description = "Name of the pipeline Lambda."
  value       = aws_lambda_function.pipeline.function_name
}

output "lambda_role_arn" {
  description = "Execution role ARN of the pipeline Lambda."
  value       = aws_iam_role.lambda.arn
}

output "ses_sender_identity" {
  description = "SES sender identity (must be verified before sending)."
  value       = aws_ses_email_identity.sender.email
}
