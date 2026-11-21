output "platform" {
  description = "Everything a stamp and the Kubernetes layer need to know about this platform."
  value = {
    cloud                      = "azure"
    name                       = var.name
    environment                = var.environment
    location                   = var.location
    resource_group_name        = azurerm_resource_group.this.name
    log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
    network = {
      vnet_id              = module.network.vnet_id
      nodes_subnet_id      = module.network.nodes_subnet_id
      postgres_subnet_id   = module.network.postgres_subnet_id
      endpoints_subnet_id  = module.network.endpoints_subnet_id
      private_dns_zone_ids = module.network.private_dns_zone_ids
    }
    cluster = {
      id              = module.cluster.id
      name            = module.cluster.name
      oidc_issuer_url = module.cluster.oidc_issuer_url
    }
    registry = var.create_registry ? { caseforge = "${module.registry[0].login_server}/caseforge" } : {}
  }
}

output "kubernetes" {
  description = "How to reach the cluster's API, for the Kubernetes and Helm providers."
  sensitive   = true
  value = {
    host           = module.cluster.host
    ca_certificate = module.cluster.ca_certificate
  }
}
