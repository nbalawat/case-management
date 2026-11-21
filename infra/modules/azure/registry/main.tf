# Azure Container Registry: Premium for private endpoints and zone redundancy, no admin user, no anonymous pull,
# untagged manifests purged, and only the cluster's kubelet identity may pull.
resource "azurerm_container_registry" "this" {
  #checkov:skip=CKV_AZURE_164:Images are signed with cosign and verified at admission by Kyverno, not Docker Content Trust
  #checkov:skip=CKV_AZURE_165:One region per platform; add georeplications for multi-region platforms
  #checkov:skip=CKV_AZURE_166:Images are scanned in CI before push and by Defender for Containers
  #checkov:skip=CKV_AZURE_139:Public access is on only for the CI runner ranges in allowed_ip_ranges
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "Premium"
  admin_enabled                 = false
  anonymous_pull_enabled        = false
  public_network_access_enabled = length(var.allowed_ip_ranges) > 0
  zone_redundancy_enabled       = true
  export_policy_enabled         = length(var.allowed_ip_ranges) > 0
  data_endpoint_enabled         = true
  retention_policy_in_days      = 14
  quarantine_policy_enabled     = false

  network_rule_set {
    default_action = "Deny"
    ip_rule = [for cidr in var.allowed_ip_ranges : {
      action   = "Allow"
      ip_range = cidr
    }]
  }

  tags = var.tags
}

resource "azurerm_private_endpoint" "this" {
  name                = "${var.name}-registry"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.endpoints_subnet_id

  private_service_connection {
    name                           = "registry"
    private_connection_resource_id = azurerm_container_registry.this.id
    subresource_names              = ["registry"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "registry"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "pull" {
  for_each             = toset(var.pull_principal_ids)
  scope                = azurerm_container_registry.this.id
  role_definition_name = "AcrPull"
  principal_id         = each.key
}
