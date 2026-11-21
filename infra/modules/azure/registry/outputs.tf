output "login_server" {
  description = "Registry host to push to and pull from."
  value       = azurerm_container_registry.this.login_server
}

output "id" {
  description = "Registry resource id."
  value       = azurerm_container_registry.this.id
}
