output "github_deploy_role_arn" {
  description = "IAM role ARN for GitHub Actions OIDC deployments."
  value       = aws_iam_role.github_deploy.arn
}

output "artifact_bucket_name" {
  description = "S3 bucket for frozen benchmark and release artifacts."
  value       = aws_s3_bucket.artifacts.bucket
}
