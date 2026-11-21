output "repository_urls" {
  description = "Repository name to push URL."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "repository_arns" {
  description = "Repository name to ARN, for pull and push policies."
  value       = { for k, r in aws_ecr_repository.this : k => r.arn }
}
