# A virtual network for one platform: a subnet for cluster nodes, one delegated to PostgreSQL Flexible Server, one for
# private endpoints. Outbound traffic leaves through a NAT gateway, every subnet has a network security group, and the
# private DNS zones the data services need are created and linked here once.
locals {
  dns_zones = {
    postgres  = "${var.name}.private.postgres.database.azure.com"
    blob      = "privatelink.blob.core.windows.net"
    dfs       = "privatelink.dfs.core.windows.net"
    vault     = "privatelink.vaultcore.azure.net"
    eventhubs = "privatelink.servicebus.windows.net"
    acr       = "privatelink.azurecr.io"
  }
}

resource "azurerm_virtual_network" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  address_space       = [var.cidr]
  tags                = var.tags
}

resource "azurerm_subnet" "nodes" {
  name                            = "nodes"
  resource_group_name             = var.resource_group_name
  virtual_network_name            = azurerm_virtual_network.this.name
  address_prefixes                = [cidrsubnet(var.cidr, 4, 0)]
  default_outbound_access_enabled = false
}

resource "azurerm_subnet" "postgres" {
  name                            = "postgres"
  resource_group_name             = var.resource_group_name
  virtual_network_name            = azurerm_virtual_network.this.name
  address_prefixes                = [cidrsubnet(var.cidr, 8, 32)]
  default_outbound_access_enabled = false
  delegation {
    name = "postgres"
    service_delegation {
      name    = "Microsoft.DBforPostgreSQL/flexibleServers"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "api_server" {
  name                            = "aks-api-server"
  resource_group_name             = var.resource_group_name
  virtual_network_name            = azurerm_virtual_network.this.name
  address_prefixes                = [cidrsubnet(var.cidr, 12, 544)] # a /28 after the /24s
  default_outbound_access_enabled = false
  delegation {
    name = "aks"
    service_delegation {
      name    = "Microsoft.ContainerService/managedClusters"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "endpoints" {
  name                              = "private-endpoints"
  resource_group_name               = var.resource_group_name
  virtual_network_name              = azurerm_virtual_network.this.name
  address_prefixes                  = [cidrsubnet(var.cidr, 8, 33)]
  default_outbound_access_enabled   = false
  private_endpoint_network_policies = "Enabled"
}

resource "azurerm_network_security_group" "this" {
  for_each            = toset(["nodes", "postgres", "endpoints", "api_server"])
  name                = "${var.name}-${each.key}"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

# Data subnets accept traffic only from inside the virtual network.
resource "azurerm_network_security_rule" "deny_internet_inbound" {
  for_each                    = toset(["postgres", "endpoints"])
  name                        = "deny-internet-inbound"
  priority                    = 4000
  direction                   = "Inbound"
  access                      = "Deny"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "Internet"
  destination_address_prefix  = "*"
  resource_group_name         = var.resource_group_name
  network_security_group_name = azurerm_network_security_group.this[each.key].name
}

resource "azurerm_subnet_network_security_group_association" "this" {
  for_each = {
    nodes      = azurerm_subnet.nodes.id
    postgres   = azurerm_subnet.postgres.id
    endpoints  = azurerm_subnet.endpoints.id
    api_server = azurerm_subnet.api_server.id
  }
  subnet_id                 = each.value
  network_security_group_id = azurerm_network_security_group.this[each.key].id
}

resource "azurerm_public_ip" "nat" {
  name                = "${var.name}-nat"
  location            = var.location
  resource_group_name = var.resource_group_name
  allocation_method   = "Static"
  sku                 = "Standard"
  zones               = var.zones
  tags                = var.tags
}

resource "azurerm_nat_gateway" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  sku_name            = "Standard"
  tags                = var.tags
}

resource "azurerm_nat_gateway_public_ip_association" "this" {
  nat_gateway_id       = azurerm_nat_gateway.this.id
  public_ip_address_id = azurerm_public_ip.nat.id
}

resource "azurerm_subnet_nat_gateway_association" "nodes" {
  subnet_id      = azurerm_subnet.nodes.id
  nat_gateway_id = azurerm_nat_gateway.this.id
}

resource "azurerm_private_dns_zone" "this" {
  for_each            = local.dns_zones
  name                = each.value
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "this" {
  for_each             = local.dns_zones
  name                 = "${var.name}-${each.key}"
  private_dns_zone_id  = azurerm_private_dns_zone.this[each.key].id
  virtual_network_id   = azurerm_virtual_network.this.id
  registration_enabled = false
  tags                 = var.tags
}

resource "azurerm_network_watcher_flow_log" "this" {
  count                = var.flow_logs == null ? 0 : 1
  name                 = "${var.name}-flow"
  network_watcher_name = var.flow_logs.network_watcher_name
  resource_group_name  = var.flow_logs.network_watcher_resource_group
  target_resource_id   = azurerm_virtual_network.this.id
  storage_account_id   = var.flow_logs.storage_account_id
  enabled              = true
  version              = 2
  retention_policy {
    enabled = true
    days    = 365
  }
  traffic_analytics {
    enabled               = true
    workspace_id          = var.flow_logs.workspace_guid
    workspace_region      = var.location
    workspace_resource_id = var.flow_logs.workspace_id
    interval_in_minutes   = 10
  }
  tags = var.tags
}
