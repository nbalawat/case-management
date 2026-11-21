# Run once per Azure subscription, with local state, by an administrator: the state storage account (Entra ID only,
# no shared keys), and the Entra applications GitHub Actions signs in as through OIDC federation, one read-only for
# plans and one per environment for applies. Afterwards everything runs from CI with no client secrets.
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    azurerm = { source = "hashicorp/azurerm", version = "~> 5.0" }
    azuread = { source = "hashicorp/azuread", version = "~> 3.0" }
  }
}

provider "azurerm" {
  features {}
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}

provider "azuread" {}

data "azurerm_client_config" "current" {}
data "azurerm_subscription" "current" {}

resource "azurerm_resource_group" "state" {
  name     = "rg-${var.prefix}-tofu-state"
  location = var.location
}

resource "azurerm_storage_account" "state" {
  #checkov:skip=CKV2_AZURE_33:Reached through allowed_ip_ranges from runners; add a private endpoint with self-hosted runners
  #checkov:skip=CKV2_AZURE_1:State is also encrypted client-side by OpenTofu before it is written
  #checkov:skip=CKV_AZURE_59:public_network_access is Disabled unless runner ranges are given
  #checkov:skip=CKV_AZURE_33:No queues are used
  #checkov:skip=CKV_AZURE_206:ZRS keeps state through a zone loss; versioning and soft delete cover mistakes
  #checkov:skip=CKV2_AZURE_21:State access is in the storage account's activity and diagnostic logs; enable them centrally
  name                              = substr("st${var.prefix}tfstate${substr(sha1(data.azurerm_subscription.current.id), 0, 6)}", 0, 24)
  resource_group_name               = azurerm_resource_group.state.name
  location                          = var.location
  account_kind                      = "StorageV2"
  account_tier                      = "Standard"
  account_replication_type          = "ZRS"
  https_traffic_only_enabled        = true
  min_tls_version                   = "TLS1_2"
  shared_access_key_enabled         = false
  default_to_oauth_authentication   = true
  allow_nested_items_to_be_public   = false
  infrastructure_encryption_enabled = true
  cross_tenant_replication_enabled  = false
  public_network_access             = length(var.allowed_ip_ranges) > 0 ? "Enabled" : "Disabled"

  blob_properties {
    versioning_enabled = true
    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 30
    }
  }

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
    ip_rules       = var.allowed_ip_ranges
  }
}

resource "azurerm_storage_container" "state" {
  #checkov:skip=CKV2_AZURE_21:State access is in the account's diagnostic logs; enable them centrally
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_management_lock" "state" {
  name       = "do-not-delete"
  scope      = azurerm_storage_account.state.id
  lock_level = "CanNotDelete"
  notes      = "OpenTofu state for every caseforge environment."
}

# ---- GitHub Actions through OIDC federation
locals {
  identities = merge(
    { plan = { subject = "repo:${var.github_repository}:pull_request", apply = false } },
    { for env in var.environments : "apply-${env}" => { subject = "repo:${var.github_repository}:environment:${env}", apply = true } },
  )
}

resource "azuread_application" "github" {
  for_each     = local.identities
  display_name = "${var.prefix}-github-${each.key}"
  owners       = [data.azurerm_client_config.current.object_id]
}

resource "azuread_service_principal" "github" {
  for_each  = local.identities
  client_id = azuread_application.github[each.key].client_id
  owners    = [data.azurerm_client_config.current.object_id]
}

resource "azuread_application_federated_identity_credential" "github" {
  #checkov:skip=CKV_AZURE_249:The subject pins the repository and either pull_request (read-only) or a protected environment
  for_each       = local.identities
  application_id = azuread_application.github[each.key].id
  display_name   = "github-${each.key}"
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = each.value.subject
  audiences      = ["api://AzureADTokenExchange"]
}

# The read-only plan identity also plans on main, for infra-apply.yml, which then applies that plan in a protected
# environment with the apply identity.
resource "azuread_application_federated_identity_credential" "plan_main" {
  #checkov:skip=CKV_AZURE_249:The subject pins the repository and its main branch, for the read-only identity
  application_id = azuread_application.github["plan"].id
  display_name   = "github-plan-main"
  issuer         = "https://token.actions.githubusercontent.com"
  subject        = "repo:${var.github_repository}:ref:refs/heads/main"
  audiences      = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "state" {
  for_each             = local.identities
  scope                = azurerm_storage_container.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azuread_service_principal.github[each.key].object_id
}

resource "azurerm_role_assignment" "plan" {
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Reader"
  principal_id         = azuread_service_principal.github["plan"].object_id
}

resource "azurerm_role_assignment" "apply" {
  for_each             = { for k, v in local.identities : k => v if v.apply }
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Contributor"
  principal_id         = azuread_service_principal.github[each.key].object_id
}

# Applies create role assignments for the stamps' identities. Constrain that: they may grant only data-plane roles
# the stacks use, never Owner, Contributor or User Access Administrator.
resource "azurerm_role_assignment" "apply_rbac" {
  for_each             = { for k, v in local.identities : k => v if v.apply }
  scope                = data.azurerm_subscription.current.id
  role_definition_name = "Role Based Access Control Administrator"
  principal_id         = azuread_service_principal.github[each.key].object_id
  condition_version    = "2.0"
  condition            = <<-COND
    (
      (!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'}))
      OR
      (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidNotEquals {8e3af657-a8ff-443c-a75c-2fe8c4bcb635, b24988ac-6180-42a0-ab88-20f7382dd24c, 18d7d88d-d35e-4fb5-a5c3-7773c20a72d9, f58310d9-a9f6-439a-9e8d-f62e7b41a168})
    )
    AND
    (
      (!(ActionMatches{'Microsoft.Authorization/roleAssignments/delete'}))
      OR
      (@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidNotEquals {8e3af657-a8ff-443c-a75c-2fe8c4bcb635, b24988ac-6180-42a0-ab88-20f7382dd24c, 18d7d88d-d35e-4fb5-a5c3-7773c20a72d9, f58310d9-a9f6-439a-9e8d-f62e7b41a168})
    )
  COND
}
