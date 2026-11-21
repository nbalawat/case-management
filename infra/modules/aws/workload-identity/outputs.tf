output "role_arn" {
  description = "Role the service account assumes."
  value       = aws_iam_role.this.arn
}

output "service_account_annotations" {
  description = "Annotations that bind the Kubernetes service account to the role."
  value       = { "eks.amazonaws.com/role-arn" = aws_iam_role.this.arn }
}
