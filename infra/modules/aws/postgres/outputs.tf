output "host" {
  description = "Hostname to connect to."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "Port to connect to."
  value       = aws_db_instance.this.port
}

output "database" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "credentials_secret_arn" {
  description = "Secrets Manager secret holding {username, password}, rotated by RDS."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
