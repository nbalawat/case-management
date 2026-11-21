variable "name" {
  description = "Registry name: globally unique, 5 to 50 letters and digits."
  type        = string
  validation {
    condition     = can(regex("^[a-zA-Z0-9]{5,50}$", var.name))
    error_message = "5 to 50 letters and digits."
  }
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the platform."
  type        = string
}

variable "endpoints_subnet_id" {
  description = "Subnet for the private endpoint."
  type        = string
}

variable "private_dns_zone_id" {
  description = "privatelink.azurecr.io zone."
  type        = string
}

variable "allowed_ip_ranges" {
  description = "Public ranges (CI runners that push) allowed to reach the registry. Empty: private endpoint only."
  type        = list(string)
  default     = []
}

variable "pull_principal_ids" {
  description = "Principals allowed to pull (the cluster's kubelet identity)."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
