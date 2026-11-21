output "vnet_id" {
  description = "Id of the virtual network."
  value       = azurerm_virtual_network.this.id
}

output "nodes_subnet_id" {
  description = "Subnet for cluster nodes."
  value       = azurerm_subnet.nodes.id
}

output "postgres_subnet_id" {
  description = "Subnet delegated to PostgreSQL Flexible Server."
  value       = azurerm_subnet.postgres.id
}

output "endpoints_subnet_id" {
  description = "Subnet for private endpoints."
  value       = azurerm_subnet.endpoints.id
}

output "private_dns_zone_ids" {
  description = "Private DNS zone ids by purpose: postgres, blob, dfs, vault, eventhubs, acr."
  value       = { for k, z in azurerm_private_dns_zone.this : k => z.id }
}

output "api_server_subnet_id" {
  description = "Subnet delegated to the AKS API server."
  value       = azurerm_subnet.api_server.id
}
