# One user-assigned identity per stamp, federated to exactly that stamp's Kubernetes service account, with data-plane
# roles scoped to its own containers and its own vault. No subscription- or resource-group-wide roles.
resource "azurerm_user_assigned_identity" "this" {
  name                = "${var.name}-app"
  location            = var.location
  resource_group_name = var.resource_group_name
  tags                = var.tags
}

resource "azurerm_federated_identity_credential" "this" {
  name                      = "kubernetes-${var.namespace}-${var.service_account}"
  user_assigned_identity_id = azurerm_user_assigned_identity.this.id
  issuer                    = var.oidc_issuer_url
  subject                   = "system:serviceaccount:${var.namespace}:${var.service_account}"
  audience                  = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "landing" {
  scope                = var.container_ids["landing"]
  role_definition_name = "Storage Blob Data Contributor" # read, and remove a file once it is moved to quarantine
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

resource "azurerm_role_assignment" "write" {
  for_each             = toset(["evidence", "quarantine"])
  scope                = var.container_ids[each.key]
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}

resource "azurerm_role_assignment" "secrets" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.this.principal_id
}
