# Optional managed event backbone: an Event Hubs namespace speaking the Kafka protocol (Premium: Kafka, zone
# redundancy and private endpoints), TLS 1.2, no public network. The application connects with SASL PLAIN using a
# send-and-listen connection string kept in the stamp's Key Vault in the same {username, password} shape as elsewhere.
resource "azurerm_eventhub_namespace" "this" {
  name                          = var.name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku                           = "Premium"
  capacity                      = { small = 1, medium = 2, large = 4 }[var.size]
  minimum_tls_version           = "1.2"
  public_network_access_enabled = false
  local_authentication_enabled  = true # Kafka clients without an Entra token callback need a connection string
  tags                          = var.tags
}

resource "azurerm_eventhub_namespace_authorization_rule" "app" {
  name                = "caseforge-app"
  namespace_name      = azurerm_eventhub_namespace.this.name
  resource_group_name = var.resource_group_name
  listen              = true
  send                = true
  manage              = false
}

resource "azurerm_private_endpoint" "this" {
  name                = "${var.name}-eventhubs"
  location            = var.location
  resource_group_name = var.resource_group_name
  subnet_id           = var.endpoints_subnet_id

  private_service_connection {
    name                           = "namespace"
    private_connection_resource_id = azurerm_eventhub_namespace.this.id
    subresource_names              = ["namespace"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "eventhubs"
    private_dns_zone_ids = [var.private_dns_zone_id]
  }

  tags = var.tags
}

resource "azurerm_key_vault_secret" "credentials" {
  #checkov:skip=CKV_AZURE_41:Tied to the authorization rule's key; rotate by regenerating the rule
  name         = "${var.name}-kafka"
  key_vault_id = var.key_vault_id
  content_type = "application/json"
  value = jsonencode({
    username = "$ConnectionString"
    password = azurerm_eventhub_namespace_authorization_rule.app.primary_connection_string
  })
}
