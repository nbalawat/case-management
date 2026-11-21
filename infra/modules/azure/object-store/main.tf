# The stamp's storage account (ADLS Gen2): containers for landing, evidence and quarantine. No shared keys (Entra ID
# only), no public network (private endpoints for blob and dfs), TLS 1.2, infrastructure double encryption plus the
# stamp's customer key, soft delete, and a time-based retention policy on evidence when worm is on.
resource "azurerm_user_assigned_identity" "this" {
  name                = "${var.name}-storage"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "key" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

resource "azurerm_storage_account" "this" {
  #checkov:skip=CKV_AZURE_59:public_network_access is Disabled (the newer attribute)
  #checkov:skip=CKV_AZURE_206:ZRS in non-production and GZRS in production, chosen by the stamp
  #checkov:skip=CKV_AZURE_33:No queues are used; blob logs go to Log Analytics (azurerm_monitor_diagnostic_setting.blob)
  #checkov:skip=CKV2_AZURE_21:Blob read, write and delete logs go to Log Analytics (azurerm_monitor_diagnostic_setting.blob)
  name                              = var.account_name
  location                          = var.location
  resource_group_name               = var.resource_group_name
  account_kind                      = "StorageV2"
  account_tier                      = "Standard"
  account_replication_type          = var.replication
  is_hns_enabled                    = true
  https_traffic_only_enabled        = true
  min_tls_version                   = "TLS1_2"
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  public_network_access             = "Disabled"
  cross_tenant_replication_enabled  = false
  infrastructure_encryption_enabled = true
  local_user_enabled                = false
  sftp_enabled                      = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.this.id]
  }

  customer_managed_key {
    key_vault_key_id          = var.key_id
    user_assigned_identity_id = azurerm_user_assigned_identity.this.id
  }

  blob_properties {
    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 30
    }
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices", "Logging", "Metrics"]
  }

  tags       = var.tags
  depends_on = [azurerm_role_assignment.key]
}

resource "azurerm_storage_container" "this" {
  #checkov:skip=CKV2_AZURE_21:Blob read, write and delete logs go to Log Analytics (azurerm_monitor_diagnostic_setting.blob)
  for_each              = toset(["landing", "evidence", "quarantine"])
  name                  = each.key
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

resource "azurerm_storage_container_immutability_policy" "evidence" {
  count                                 = var.evidence_worm ? 1 : 0
  storage_container_resource_manager_id = azurerm_storage_container.this["evidence"].id
  immutability_period_in_days           = var.evidence_retention_days
  protected_append_writes_enabled       = false
  locked                                = var.lock_evidence_policy
}

resource "azurerm_private_endpoint" "this" {
  for_each            = var.private_dns_zone_ids
  name                = "${var.account_name}-${each.key}"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.endpoints_subnet_id

  private_service_connection {
    name                           = each.key
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = [each.key]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = each.key
    private_dns_zone_ids = [each.value]
  }

  tags = var.tags
}

resource "azurerm_storage_management_policy" "this" {
  storage_account_id = azurerm_storage_account.this.id
  rule {
    name    = "expire-landed-files"
    enabled = true
    filters {
      prefix_match = ["landing/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.landing_expiry_days
      }
    }
  }
}

# Blob read, write and delete logs to Log Analytics.
resource "azurerm_monitor_diagnostic_setting" "blob" {
  count                      = var.log_analytics_workspace_id == null ? 0 : 1
  name                       = "blob-access"
  target_resource_id         = "${azurerm_storage_account.this.id}/blobServices/default"
  log_analytics_workspace_id = var.log_analytics_workspace_id
  enabled_log {
    category = "StorageRead"
  }
  enabled_log {
    category = "StorageWrite"
  }
  enabled_log {
    category = "StorageDelete"
  }
}
