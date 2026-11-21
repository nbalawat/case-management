output "state_bucket" {
  description = "Bucket for envs/aws/<env>/backend.hcl."
  value       = aws_s3_bucket.state.bucket
}

output "state_key_arn" {
  description = "KMS key for state_encryption_key_arn in every stack."
  value       = module.state_key.key_arn
}

output "github_role_arns" {
  description = "Roles to put in the GitHub environments as AWS_ROLE_ARN."
  value       = { for k, r in aws_iam_role.github : k => r.arn }
}
