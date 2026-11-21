variable "name" {
  description = "Namespace name: globally unique."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the stamp."
  type        = string
}

variable "size" {
  description = "Cloud-neutral size: small, medium or large (processing units 1, 2, 4)."
  type        = string
  default     = "small"
  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size is small, medium or large."
  }
}

variable "endpoints_subnet_id" {
  description = "Subnet for the private endpoint."
  type        = string
}

variable "private_dns_zone_id" {
  description = "privatelink.servicebus.windows.net zone."
  type        = string
}

variable "key_vault_id" {
  description = "The stamp's vault, for the connection secret."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
