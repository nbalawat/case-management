output "account_name" {
  description = "Storage account name."
  value       = azurerm_storage_account.this.name
}

output "account_id" {
  description = "Storage account id."
  value       = azurerm_storage_account.this.id
}

output "container_ids" {
  description = "Container resource ids by purpose, for role assignments."
  value       = { for k, c in azurerm_storage_container.this : k => c.id }
}

output "urls" {
  description = "Purpose to the URL the application is given."
  value       = { for k, c in azurerm_storage_container.this : k => "abfss://${c.name}@${azurerm_storage_account.this.name}.dfs.core.windows.net/caseforge" }
}
