# The case database: PostgreSQL Flexible Server injected into the delegated subnet (no public endpoint), TLS enforced,
# encrypted with the stamp's key, zone-redundant standby, Entra ID sign-in on, and the admin password generated
# ephemerally: written to the server and to Key Vault in the same run and never stored in state.
locals {
  sku = {
    small  = "GP_Standard_D2ds_v5"
    medium = "GP_Standard_D8ds_v5"
    large  = "MO_Standard_E16ds_v5"
  }[var.size]
}

data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "this" {
  name                = "${var.name}-pg"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_role_assignment" "key" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Crypto Service Encryption User"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

ephemeral "random_password" "admin" {
  length           = 40
  override_special = "-_.~"
}

resource "azurerm_postgresql_flexible_server" "this" {
  #checkov:skip=CKV_AZURE_136:Geo-redundant backup with a customer key needs a second key in the paired region; see infra/README.md
  #checkov:skip=CKV2_AZURE_57:The server is injected into a delegated subnet with no public endpoint
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  version                       = var.engine_major
  sku_name                      = local.sku
  storage_mb                    = var.storage_gb * 1024
  auto_grow_enabled             = true
  delegated_subnet_id           = var.subnet_id
  private_dns_zone_id           = var.private_dns_zone_id
  public_network_access_enabled = false
  zone                          = "1"

  administrator_login               = "caseforge_admin"
  administrator_password_wo         = ephemeral.random_password.admin.result
  administrator_password_wo_version = var.credentials_version

  authentication {
    active_directory_auth_enabled = true
    password_auth_enabled         = true
    tenant_id                     = data.azurerm_client_config.current.tenant_id
  }

  backup_retention_days        = var.backup_retention_days
  geo_redundant_backup_enabled = false # with a customer key, geo backups need a second key in the paired region

  dynamic "high_availability" {
    for_each = var.high_availability ? [1] : []
    content {
      mode                      = "ZoneRedundant"
      standby_availability_zone = "2"
    }
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.this.id]
  }

  customer_managed_key {
    key_vault_key_id                  = var.key_id
    primary_user_assigned_identity_id = azurerm_user_assigned_identity.this.id
  }

  maintenance_window {
    day_of_week  = 0
    start_hour   = 3
    start_minute = 0
  }

  tags       = var.tags
  depends_on = [azurerm_role_assignment.key]

  lifecycle {
    prevent_destroy = false
    ignore_changes  = [zone, high_availability[0].standby_availability_zone]
  }
}

resource "azurerm_management_lock" "this" {
  count      = var.deletion_protection ? 1 : 0
  name       = "do-not-delete"
  scope      = azurerm_postgresql_flexible_server.this.id
  lock_level = "CanNotDelete"
  notes      = "The case database. Remove this lock deliberately before deleting."
}

resource "azurerm_postgresql_flexible_server_database" "this" {
  name      = var.database_name
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_postgresql_flexible_server_configuration" "this" {
  for_each = {
    require_secure_transport   = "ON"
    ssl_min_protocol_version   = "TLSv1.2"
    log_connections            = "ON"
    log_disconnections         = "ON"
    log_min_duration_statement = "1000"
    "azure.extensions"         = "AGE,PG_TRGM,PGCRYPTO"
    shared_preload_libraries   = "age,pg_stat_statements"
  }
  name      = each.key
  server_id = azurerm_postgresql_flexible_server.this.id
  value     = each.value
}

# The credentials as JSON, the same shape on every cloud, for External Secrets to read.
resource "azurerm_key_vault_secret" "credentials" {
  #checkov:skip=CKV_AZURE_41:Rotated by raising credentials_version; an expiry would break the database connection
  name             = "${var.name}-db"
  key_vault_id     = var.key_vault_id
  content_type     = "application/json"
  value_wo         = jsonencode({ username = "caseforge_admin", password = ephemeral.random_password.admin.result })
  value_wo_version = var.credentials_version
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  count                      = var.log_analytics_workspace_id == null ? 0 : 1
  name                       = "postgres"
  target_resource_id         = azurerm_postgresql_flexible_server.this.id
  log_analytics_workspace_id = var.log_analytics_workspace_id
  enabled_log {
    category_group = "audit"
  }
  enabled_log {
    category = "PostgreSQLLogs"
  }
}
