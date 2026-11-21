output "client_id" {
  description = "Client id of the identity."
  value       = azurerm_user_assigned_identity.this.client_id
}

output "tenant_id" {
  description = "Tenant of the identity."
  value       = azurerm_user_assigned_identity.this.tenant_id
}

output "service_account_annotations" {
  description = "Annotations that bind the Kubernetes service account to the identity."
  value = {
    "azure.workload.identity/client-id" = azurerm_user_assigned_identity.this.client_id
    "azure.workload.identity/tenant-id" = azurerm_user_assigned_identity.this.tenant_id
  }
}
