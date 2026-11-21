variable "subscription_id" {
  description = "Subscription of the platform."
  type        = string
}

variable "state_passphrase" {
  description = "Passphrase for OpenTofu state encryption. Set TF_VAR_state_passphrase; never put it in a file."
  type        = string
  sensitive   = true
}

variable "tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = {}
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
  subscription_id     = var.subscription_id
  storage_use_azuread = true
}
