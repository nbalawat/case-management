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
  mock_data "azurerm_client_config" {
    defaults = { tenant_id = "11111111-1111-1111-1111-111111111111" }
  }
}

variables {
  name                       = "cf-dev-shared"
  location                   = "uksouth"
  resource_group_name        = "rg-cf-dev-shared"
  subnet_id                  = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/nodes"
  api_server_subnet_id       = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/api"
  key_vault_id               = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv"
  key_id                     = "https://kv.vault.azure.net/keys/data/0123456789abcdef0123456789abcdef"
  log_analytics_workspace_id = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/w"
}

run "private_entra_only_encrypted" {
  command = plan

  assert {
    condition     = azurerm_kubernetes_cluster.this.private_cluster_enabled
    error_message = "The cluster is private by default."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.local_account_disabled
    error_message = "Local accounts are off: Entra ID sign-in only."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.workload_identity_enabled && azurerm_kubernetes_cluster.this.oidc_issuer_enabled
    error_message = "Workload identity is on."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.key_management_service[0].key_vault_network_access == "Private"
    error_message = "etcd is encrypted with the platform key over a private path."
  }
  assert {
    condition     = azurerm_kubernetes_cluster.this.network_profile[0].network_policy == "cilium"
    error_message = "Network policy is enforced."
  }
}
