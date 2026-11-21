output "host" {
  description = "Hostname to connect to."
  value       = azurerm_postgresql_flexible_server.this.fqdn
}

output "port" {
  description = "Port to connect to."
  value       = 5432
}

output "database" {
  description = "Database name."
  value       = azurerm_postgresql_flexible_server_database.this.name
}

output "credentials_secret_name" {
  description = "Key Vault secret holding {username, password}."
  value       = azurerm_key_vault_secret.credentials.name
}
