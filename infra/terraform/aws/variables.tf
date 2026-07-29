variable "aws_region" {
  description = "AWS region used for AgentRail deployment support resources."
  type        = string
  default     = "us-east-1"
}

variable "github_owner" {
  description = "GitHub organisation or username that owns the repository."
  type        = string
}

variable "github_repo" {
  description = "GitHub repository name."
  type        = string
}

variable "environment" {
  description = "Environment name, for example staging or production."
  type        = string
  default     = "staging"
}

variable "artifact_bucket_name" {
  description = "Globally unique S3 bucket for benchmark and release artifacts."
  type        = string
}
