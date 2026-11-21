# A Key Vault for one platform or stamp: Azure RBAC (no access policies), purge protection, soft delete, reachable
# only through a private endpoint and from the deployer's addresses, with a rotating customer-managed key.
data "azurerm_client_config" "current" {}

resource "azurerm_key_vault" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "premium"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = true
  soft_delete_retention_days    = 90
  public_network_access_enabled = length(var.deployer_ip_ranges) > 0

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
    ip_rules       = var.deployer_ip_ranges
  }

  tags = var.tags
}

# Whoever runs OpenTofu manages keys and secrets in this vault, and nothing else.
resource "azurerm_role_assignment" "deployer" {
  for_each             = toset(["Key Vault Crypto Officer", "Key Vault Secrets Officer"])
  scope                = azurerm_key_vault.this.id
  role_definition_name = each.key
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_private_endpoint" "this" {
  name                = "${var.name}-vault"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.endpoints_subnet_id

  private_service_connection {
    name                           = "vault"
    private_connection_resource_id = azurerm_key_vault.this.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "vault"
    private_dns_zone_ids = [var.vault_private_dns_zone_id]
  }

  tags = var.tags
}

resource "azurerm_key_vault_key" "this" {
  #checkov:skip=CKV_AZURE_40:Expiry is managed by the rotation policy (expire_after P2Y, rotated 30 days before)
  name         = "data"
  key_vault_id = azurerm_key_vault.this.id
  key_type     = "RSA-HSM"
  key_size     = 3072
  key_opts     = ["decrypt", "encrypt", "sign", "unwrapKey", "verify", "wrapKey"]

  rotation_policy {
    automatic {
      time_before_expiry = "P30D"
    }
    expire_after         = "P2Y"
    notify_before_expiry = "P60D"
  }

  depends_on = [azurerm_role_assignment.deployer]
}
