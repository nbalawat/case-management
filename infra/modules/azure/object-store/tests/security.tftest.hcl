mock_provider "azurerm" {
  mock_resource "azurerm_user_assigned_identity" {
    defaults = {
      id           = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.ManagedIdentity/userAssignedIdentities/mock"
      principal_id = "33333333-3333-3333-3333-333333333333"
      client_id    = "44444444-4444-4444-4444-444444444444"
      tenant_id    = "11111111-1111-1111-1111-111111111111"
    }
  }
  mock_resource "azurerm_resource_group" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock"
    }
  }
  mock_resource "azurerm_key_vault" {
    defaults = {
      id        = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.KeyVault/vaults/kvmock"
      vault_uri = "https://kvmock.vault.azure.net/"
    }
  }
  mock_resource "azurerm_key_vault_key" {
    defaults = {
      id                      = "https://kvmock.vault.azure.net/keys/data/0123456789abcdef0123456789abcdef"
      versionless_id          = "https://kvmock.vault.azure.net/keys/data"
      resource_versionless_id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.KeyVault/vaults/kvmock/keys/data"
    }
  }
  mock_resource "azurerm_storage_account" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.Storage/storageAccounts/stmock"
    }
  }
  mock_resource "azurerm_storage_container" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.Storage/storageAccounts/stmock/blobServices/default/containers/mock"
    }
  }
  mock_resource "azurerm_postgresql_flexible_server" {
    defaults = {
      id   = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.DBforPostgreSQL/flexibleServers/pgmock"
      fqdn = "pgmock.postgres.database.azure.com"
    }
  }
  mock_resource "azurerm_eventhub_namespace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.EventHub/namespaces/ehmock"
    }
  }
  mock_resource "azurerm_kubernetes_cluster" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.ContainerService/managedClusters/aksmock"
    }
  }
  mock_resource "azurerm_log_analytics_workspace" {
    defaults = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-mock/providers/Microsoft.OperationalInsights/workspaces/lamock"
    }
  }
}

variables {
  name                 = "cf-dev-acme"
  account_name         = "stcfdevacme000000"
  location             = "uksouth"
  resource_group_name  = "rg-cf-dev-acme"
  key_vault_id         = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv"
  key_id               = "https://kv.vault.azure.net/keys/data"
  endpoints_subnet_id  = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/e"
  private_dns_zone_ids = { blob = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net", dfs = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.dfs.core.windows.net" }
}

run "private_keyless_encrypted" {
  command = plan

  assert {
    condition     = azurerm_storage_account.this.shared_access_key_enabled == false
    error_message = "No shared keys: Entra ID only."
  }
  assert {
    condition     = azurerm_storage_account.this.public_network_access == "Disabled"
    error_message = "No public network access; private endpoints only."
  }
  assert {
    condition     = azurerm_storage_account.this.min_tls_version == "TLS1_2" && azurerm_storage_account.this.https_traffic_only_enabled
    error_message = "HTTPS with TLS 1.2 or later only."
  }
  assert {
    condition     = azurerm_storage_account.this.infrastructure_encryption_enabled && azurerm_storage_account.this.allow_nested_items_to_be_public == false
    error_message = "Double encryption on, nothing public."
  }
  assert {
    condition     = azurerm_storage_container_immutability_policy.evidence[0].immutability_period_in_days == 2555
    error_message = "Evidence has a time-based retention policy."
  }
}
