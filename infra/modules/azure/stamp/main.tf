# A stamp on Azure: its own resource group, Key Vault and key, database, storage account and identity. Event Hubs
# (Kafka protocol) only when asked for. Azure has no managed OpenSearch, so search runs in the cluster.
locals {
  namespace = coalesce(var.namespace, "cf-${var.name}")
  id        = "cf-${var.environment}-${var.name}"
  # Globally unique names that allow no hyphens get a short suffix from the subscription and stamp.
  suffix = substr(sha1("${data.azurerm_client_config.current.subscription_id}/${local.id}"), 0, 6)
  tags = merge(var.tags, {
    "caseforge-stamp"       = var.name
    "caseforge-environment" = var.environment
    "caseforge-tenancy"     = var.tenancy
  })
  p = var.platform
}

data "azurerm_client_config" "current" {}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.id}"
  location = local.p.location
  tags     = local.tags
}

module "kms" {
  source                    = "../kms"
  name                      = substr("kv${replace(local.id, "-", "")}${local.suffix}", 0, 24)
  location                  = local.p.location
  resource_group_name       = azurerm_resource_group.this.name
  endpoints_subnet_id       = local.p.network.endpoints_subnet_id
  vault_private_dns_zone_id = local.p.network.private_dns_zone_ids["vault"]
  deployer_ip_ranges        = var.deployer_ip_ranges
  tags                      = local.tags
}

module "postgres" {
  source                     = "../postgres"
  name                       = "${local.id}-${local.suffix}"
  location                   = local.p.location
  resource_group_name        = azurerm_resource_group.this.name
  size                       = var.postgres.size
  storage_gb                 = var.postgres.storage_gb
  high_availability          = var.postgres.high_availability
  backup_retention_days      = var.postgres.backup_retention_days
  deletion_protection        = var.postgres.deletion_protection
  subnet_id                  = local.p.network.postgres_subnet_id
  private_dns_zone_id        = local.p.network.private_dns_zone_ids["postgres"]
  key_vault_id               = module.kms.key_vault_id
  key_id                     = module.kms.key_id
  log_analytics_workspace_id = local.p.log_analytics_workspace_id
  tags                       = local.tags
}

module "object_store" {
  source                     = "../object-store"
  name                       = local.id
  account_name               = substr("st${replace(local.id, "-", "")}${local.suffix}", 0, 24)
  location                   = local.p.location
  resource_group_name        = azurerm_resource_group.this.name
  key_vault_id               = module.kms.key_vault_id
  key_id                     = module.kms.key_id
  endpoints_subnet_id        = local.p.network.endpoints_subnet_id
  private_dns_zone_ids       = { blob = local.p.network.private_dns_zone_ids["blob"], dfs = local.p.network.private_dns_zone_ids["dfs"] }
  evidence_worm              = var.evidence.worm
  evidence_retention_days    = var.evidence.retention_days
  replication                = var.environment == "prod" ? "GZRS" : "ZRS"
  log_analytics_workspace_id = local.p.log_analytics_workspace_id
  tags                       = local.tags
}

module "kafka" {
  count               = var.kafka.mode == "managed" ? 1 : 0
  source              = "../kafka"
  name                = "${local.id}-${local.suffix}"
  location            = local.p.location
  resource_group_name = azurerm_resource_group.this.name
  size                = var.kafka.size
  endpoints_subnet_id = local.p.network.endpoints_subnet_id
  private_dns_zone_id = local.p.network.private_dns_zone_ids["eventhubs"]
  key_vault_id        = module.kms.key_vault_id
  tags                = local.tags
}

module "identity" {
  source              = "../workload-identity"
  name                = local.id
  location            = local.p.location
  resource_group_name = azurerm_resource_group.this.name
  namespace           = local.namespace
  service_account     = var.service_account
  oidc_issuer_url     = local.p.cluster.oidc_issuer_url
  container_ids       = module.object_store.container_ids
  key_vault_id        = module.kms.key_vault_id
  tags                = local.tags
}
