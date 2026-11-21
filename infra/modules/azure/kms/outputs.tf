output "key_vault_id" {
  description = "Id of the vault."
  value       = azurerm_key_vault.this.id
}

output "key_vault_uri" {
  description = "Vault URI, for External Secrets."
  value       = azurerm_key_vault.this.vault_uri
}

output "key_id" {
  description = "Versionless id of the customer-managed key."
  value       = azurerm_key_vault_key.this.versionless_id
}

output "key_resource_id" {
  description = "Resource id of the key, for role assignments."
  value       = azurerm_key_vault_key.this.resource_versionless_id
}
