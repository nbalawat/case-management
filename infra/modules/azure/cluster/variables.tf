variable "name" {
  description = "Cluster name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the platform."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes minor version. Patches are applied automatically."
  type        = string
  default     = "1.33"
}

variable "sku_tier" {
  description = "Free for dev, Standard (financially backed API SLA) for anything else."
  type        = string
  default     = "Standard"
  validation {
    condition     = contains(["Free", "Standard", "Premium"], var.sku_tier)
    error_message = "sku_tier is Free, Standard or Premium."
  }
}

variable "subnet_id" {
  description = "Node subnet."
  type        = string
}

variable "api_server_subnet_id" {
  description = "Subnet delegated to the AKS API server (VNet integration)."
  type        = string
}

variable "zones" {
  description = "Availability zones for node pools."
  type        = list(string)
  default     = ["1", "2", "3"]
}

variable "key_vault_id" {
  description = "Platform Key Vault."
  type        = string
}

variable "key_id" {
  description = "Versioned id of the key that encrypts etcd."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace for audit logs, container insights and Defender."
  type        = string
}

variable "api_allowed_cidrs" {
  description = "Public ranges allowed to reach the API. Empty (the default) makes the cluster private."
  type        = list(string)
  default     = []
  validation {
    condition     = !contains(var.api_allowed_cidrs, "0.0.0.0/0")
    error_message = "Never open the Kubernetes API to the whole internet."
  }
}

variable "admin_group_object_ids" {
  description = "Entra ID groups that are cluster admins."
  type        = list(string)
  default     = []
}

variable "system_pool" {
  description = "The system node pool, which runs only critical add-ons."
  type = object({
    vm_size = optional(string, "Standard_D4ds_v5")
    min     = optional(number, 2)
    max     = optional(number, 4)
  })
  default  = {}
  nullable = false
}

variable "node_pools" {
  description = "Workload node pools by name (lowercase, up to 12 characters)."
  type = map(object({
    vm_size = string
    min     = number
    max     = number
    spot    = optional(bool, false)
    labels  = optional(map(string), {})
    taints  = optional(list(string), [])
  }))
  default = {
    apps = { vm_size = "Standard_D8ds_v5", min = 3, max = 12 }
  }
  nullable = false
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
