output "host" {
  description = "Private IP to connect to."
  value       = google_sql_database_instance.this.private_ip_address
}

output "port" {
  description = "Port to connect to."
  value       = 5432
}

output "database" {
  description = "Database name."
  value       = google_sql_database.this.name
}

output "credentials_secret_id" {
  description = "Secret Manager secret holding {username, password}."
  value       = google_secret_manager_secret.credentials.secret_id
}

output "credentials_secret_name" {
  description = "Full resource name of the secret, for IAM."
  value       = google_secret_manager_secret.credentials.id
}
