output "bootstrap_servers" {
  description = "Kafka endpoint of the namespace."
  value       = "${azurerm_eventhub_namespace.this.name}.servicebus.windows.net:9093"
}

output "credentials_secret_name" {
  description = "Key Vault secret holding {username, password} for SASL PLAIN."
  value       = azurerm_key_vault_secret.credentials.name
}
