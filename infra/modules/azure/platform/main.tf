# A platform on Azure: resource group, network with private DNS, platform Key Vault, Log Analytics, AKS and ACR.
locals {
  id     = "cf-${var.environment}-${var.name}"
  suffix = substr(sha1("${data.azurerm_client_config.current.subscription_id}/${local.id}"), 0, 6)
  tags = merge(var.tags, {
    "caseforge-platform"    = var.name
    "caseforge-environment" = var.environment
  })
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.id}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "this" {
  name                         = local.id
  location                     = var.location
  resource_group_name          = azurerm_resource_group.this.name
  sku                          = "PerGB2018"
  retention_in_days            = var.log_retention_days
  local_authentication_enabled = false
  tags                         = local.tags
}

module "network" {
  source              = "../network"
  name                = local.id
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  cidr                = var.network.cidr
  zones               = var.zones
  tags                = local.tags
}

module "kms" {
  source                    = "../kms"
  name                      = substr("kv${replace(local.id, "-", "")}${local.suffix}", 0, 24)
  location                  = var.location
  resource_group_name       = azurerm_resource_group.this.name
  endpoints_subnet_id       = module.network.endpoints_subnet_id
  vault_private_dns_zone_id = module.network.private_dns_zone_ids["vault"]
  deployer_ip_ranges        = var.deployer_ip_ranges
  tags                      = local.tags
}

# AKS needs the versioned key id for etcd encryption.
data "azurerm_key_vault_key" "platform" {
  name         = "data"
  key_vault_id = module.kms.key_vault_id
  depends_on   = [module.kms]
}

module "cluster" {
  source                     = "../cluster"
  name                       = local.id
  location                   = var.location
  resource_group_name        = azurerm_resource_group.this.name
  kubernetes_version         = var.cluster.kubernetes_version
  sku_tier                   = var.environment == "prod" ? "Standard" : var.cluster.sku_tier
  subnet_id                  = module.network.nodes_subnet_id
  api_server_subnet_id       = module.network.api_server_subnet_id
  zones                      = var.zones
  key_vault_id               = module.kms.key_vault_id
  key_id                     = data.azurerm_key_vault_key.platform.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  api_allowed_cidrs          = var.cluster.api_allowed_cidrs
  admin_group_object_ids     = var.cluster.admin_group_object_ids
  system_pool                = var.cluster.system_pool
  node_pools                 = var.cluster.node_pools
  tags                       = local.tags
}

module "registry" {
  count               = var.create_registry ? 1 : 0
  source              = "../registry"
  name                = substr("cr${replace(local.id, "-", "")}${local.suffix}", 0, 50)
  location            = var.location
  resource_group_name = azurerm_resource_group.this.name
  endpoints_subnet_id = module.network.endpoints_subnet_id
  private_dns_zone_id = module.network.private_dns_zone_ids["acr"]
  allowed_ip_ranges   = var.deployer_ip_ranges
  pull_principal_ids  = [module.cluster.kubelet_object_id]
  tags                = local.tags
}

resource "azurerm_management_lock" "cluster" {
  count      = var.environment == "prod" ? 1 : 0
  name       = "do-not-delete"
  scope      = module.cluster.id
  lock_level = "CanNotDelete"
  notes      = "Production cluster. Remove deliberately before deleting."
}
