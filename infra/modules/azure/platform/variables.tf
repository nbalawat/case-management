variable "name" {
  description = "Platform name, for example shared for the managed service or a client name for a client-cloud install."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}[a-z0-9]$", var.name))
    error_message = "3 to 16 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "dev, staging or prod."
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment is dev, staging or prod."
  }
}

variable "location" {
  description = "Azure region, for example eastus2."
  type        = string
}

variable "zones" {
  description = "Availability zones."
  type        = list(string)
  default     = ["1", "2", "3"]
}

variable "network" {
  description = "Network settings."
  type = object({
    cidr = optional(string, "10.50.0.0/16")
  })
  default = {}
}

variable "cluster" {
  description = "Cluster settings. Pool shapes follow modules/azure/cluster."
  type = object({
    kubernetes_version     = optional(string, "1.33")
    sku_tier               = optional(string, "Free")
    api_allowed_cidrs      = optional(list(string), [])
    admin_group_object_ids = optional(list(string), [])
    system_pool            = optional(any)
    node_pools             = optional(any)
  })
  default = {}
}

variable "deployer_ip_ranges" {
  description = "Public ranges (CI runners) allowed to manage the vault and push to the registry. Empty: private only."
  type        = list(string)
  default     = []
}

variable "create_registry" {
  description = "Create an Azure Container Registry."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Days logs are kept in Log Analytics."
  type        = number
  default     = 365
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
