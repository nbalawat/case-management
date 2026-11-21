# Plans the Azure stamp against a mocked provider: no subscription, no credentials, nothing created.
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
    defaults = {
      subscription_id = "00000000-0000-0000-0000-000000000000"
      tenant_id       = "11111111-1111-1111-1111-111111111111"
      object_id       = "22222222-2222-2222-2222-222222222222"
    }
  }
}

variables {
  name        = "acme"
  environment = "dev"
  tenancy     = "dedicated"
  platform = {
    cloud                      = "azure"
    location                   = "uksouth"
    log_analytics_workspace_id = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/w"
    network = {
      postgres_subnet_id  = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/postgres"
      endpoints_subnet_id = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/v/subnets/private-endpoints"
      private_dns_zone_ids = {
        postgres  = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/x.private.postgres.database.azure.com"
        blob      = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.blob.core.windows.net"
        dfs       = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.dfs.core.windows.net"
        vault     = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"
        eventhubs = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Network/privateDnsZones/privatelink.servicebus.windows.net"
      }
    }
    cluster = {
      oidc_issuer_url = "https://uksouth.oic.prod-aks.azure.com/tenant/cluster/"
    }
  }
}

run "dedicated_stamp" {
  command = plan

  assert {
    condition     = output.contract.cloud == "azure" && output.contract.namespace == "cf-acme"
    error_message = "The contract names the cloud and defaults the namespace."
  }
  assert {
    condition     = output.contract.pod_labels["azure.workload.identity/use"] == "true"
    error_message = "Pods are labelled for Azure workload identity."
  }
  assert {
    condition     = output.contract.secret_store.azurekv.authType == "WorkloadIdentity"
    error_message = "External Secrets reads Key Vault as the stamp's own identity."
  }
  assert {
    condition     = length(module.kafka) == 0
    error_message = "No Event Hubs unless asked for."
  }
}

run "managed_event_hubs" {
  command = plan
  variables {
    kafka = { mode = "managed" }
  }
  assert {
    condition     = output.contract.event_bus.sasl_mechanism == "PLAIN" && output.contract.event_bus.credentials != null
    error_message = "Event Hubs is reached with SASL PLAIN and a connection string from Key Vault."
  }
}

run "no_managed_search_on_azure" {
  command = plan
  variables {
    search = { mode = "managed" }
  }
  expect_failures = [check.search_mode_supported]
}
