# AKS with Entra ID sign-in only (local accounts off), Azure RBAC for Kubernetes, workload identity, etcd encrypted
# with a customer key, Cilium network policy, host-encrypted node disks, automatic patching, Azure Policy, and
# control plane audit logs sent to Log Analytics. The API is private unless allowed ranges are given.
data "azurerm_client_config" "current" {}

resource "azurerm_user_assigned_identity" "control_plane" {
  name                = "${var.name}-control-plane"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

# The control plane wraps etcd keys with the platform key and manages the node subnet.
resource "azurerm_role_assignment" "kms" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Crypto User"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
}

resource "azurerm_role_assignment" "network" {
  for_each             = { nodes = var.subnet_id, api = var.api_server_subnet_id }
  scope                = each.value
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.control_plane.principal_id
}

resource "azurerm_kubernetes_cluster" "this" {
  #checkov:skip=CKV_AZURE_117:OS disks are ephemeral (no managed disk to encrypt) and hosts encrypt temp and cache disks
  #checkov:skip=CKV_AZURE_172:Secrets come from External Secrets, not the Secrets Store CSI driver
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  dns_prefix          = var.name
  kubernetes_version  = var.kubernetes_version
  sku_tier            = var.sku_tier
  node_resource_group = "${var.resource_group_name}-nodes"

  private_cluster_enabled   = length(var.api_allowed_cidrs) == 0
  local_account_disabled    = true
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  azure_policy_enabled      = true
  run_command_enabled       = false
  image_cleaner_enabled     = true

  automatic_upgrade_channel = "patch"
  node_os_upgrade_channel   = "NodeImage"

  # The API server is injected into its own subnet, so it reaches the private Key Vault for etcd encryption.
  api_server_access_profile {
    virtual_network_integration_enabled = true
    subnet_id                           = var.api_server_subnet_id
    authorized_ip_ranges                = length(var.api_allowed_cidrs) > 0 ? var.api_allowed_cidrs : null
  }

  node_provisioning_profile {
    mode = "Manual"
  }

  azure_active_directory_role_based_access_control {
    tenant_id              = data.azurerm_client_config.current.tenant_id
    azure_rbac_enabled     = true
    admin_group_object_ids = var.admin_group_object_ids
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.control_plane.id]
  }

  key_management_service {
    key_vault_key_id         = var.key_id
    key_vault_id             = var.key_vault_id
    key_vault_network_access = "Private"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    outbound_type       = "userAssignedNATGateway"
    load_balancer_sku   = "standard"
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.system_pool.vm_size
    auto_scaling_enabled         = true
    min_count                    = var.system_pool.min
    max_count                    = var.system_pool.max
    vnet_subnet_id               = var.subnet_id
    zones                        = var.zones
    host_encryption_enabled      = true
    only_critical_addons_enabled = true
    os_sku                       = "AzureLinux"
    os_disk_type                 = "Ephemeral" # nothing persists on a node's OS disk
    os_disk_size_gb              = 100
    max_pods                     = 110
    temporary_name_for_rotation  = "systemtmp"
    upgrade_settings {
      max_surge = "33%"
    }
  }

  oms_agent {
    log_analytics_workspace_id      = var.log_analytics_workspace_id
    msi_auth_for_monitoring_enabled = true
  }

  microsoft_defender {
    log_analytics_workspace_id = var.log_analytics_workspace_id
  }

  maintenance_window_auto_upgrade {
    frequency   = "Weekly"
    interval    = 1
    day_of_week = "Sunday"
    start_time  = "03:00"
    duration    = 4
  }

  tags       = var.tags
  depends_on = [azurerm_role_assignment.kms, azurerm_role_assignment.network]

  lifecycle {
    ignore_changes = [default_node_pool[0].node_count, kubernetes_version]
  }
}

resource "azurerm_kubernetes_cluster_node_pool" "this" {
  for_each                    = var.node_pools
  name                        = each.key
  kubernetes_cluster_id       = azurerm_kubernetes_cluster.this.id
  vm_size                     = each.value.vm_size
  auto_scaling_enabled        = true
  min_count                   = each.value.min
  max_count                   = each.value.max
  vnet_subnet_id              = var.subnet_id
  zones                       = var.zones
  host_encryption_enabled     = true
  os_sku                      = "AzureLinux"
  os_disk_type                = "Ephemeral"
  os_disk_size_gb             = 100
  max_pods                    = 110
  node_labels                 = each.value.labels
  node_taints                 = each.value.taints
  priority                    = each.value.spot ? "Spot" : "Regular"
  temporary_name_for_rotation = "${substr(each.key, 0, 8)}tmp"
  upgrade_settings {
    max_surge = "33%"
  }
  tags = var.tags
  lifecycle {
    ignore_changes = [node_count]
  }
}

resource "azurerm_monitor_diagnostic_setting" "this" {
  name                           = "control-plane"
  target_resource_id             = azurerm_kubernetes_cluster.this.id
  log_analytics_workspace_id     = var.log_analytics_workspace_id
  log_analytics_destination_type = "Dedicated"
  dynamic "enabled_log" {
    for_each = toset(["kube-audit-admin", "kube-apiserver", "kube-controller-manager", "kube-scheduler", "guard"])
    content {
      category = enabled_log.key
    }
  }
}
