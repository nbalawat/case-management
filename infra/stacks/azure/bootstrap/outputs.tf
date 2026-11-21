output "backend" {
  description = "Values for envs/azure/<env>/backend.hcl."
  value = {
    resource_group_name  = azurerm_resource_group.state.name
    storage_account_name = azurerm_storage_account.state.name
    container_name       = azurerm_storage_container.state.name
  }
}

output "github_client_ids" {
  description = "Client ids to put in the GitHub environments as AZURE_CLIENT_ID."
  value       = { for k, a in azuread_application.github : k => a.client_id }
}

output "tenant_id" {
  description = "AZURE_TENANT_ID for GitHub."
  value       = data.azurerm_client_config.current.tenant_id
}
