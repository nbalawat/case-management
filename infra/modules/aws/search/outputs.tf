output "url" {
  description = "Address for SEARCH_URL."
  value       = "opensearchs://${aws_opensearch_domain.this.endpoint}:443"
}

output "credentials_secret_arn" {
  description = "Secrets Manager secret holding {username, password}."
  value       = aws_secretsmanager_secret.master.arn
}
