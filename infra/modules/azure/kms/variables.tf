variable "name" {
  description = "Key Vault name: globally unique, 3 to 24 characters."
  type        = string
  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{1,22}[a-zA-Z0-9]$", var.name))
    error_message = "3 to 24 letters, digits and hyphens."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group for the vault."
  type        = string
}

variable "endpoints_subnet_id" {
  description = "Subnet for the vault's private endpoint."
  type        = string
}

variable "vault_private_dns_zone_id" {
  description = "privatelink.vaultcore.azure.net zone."
  type        = string
}

variable "deployer_ip_ranges" {
  description = "Public addresses (CI runners, operators) allowed to reach the vault to manage it. Empty: private endpoint only, so OpenTofu must run inside the network."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
