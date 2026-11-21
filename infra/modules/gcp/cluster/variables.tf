variable "project_id" {
  description = "Project of the cluster."
  type        = string
}

variable "name" {
  description = "Cluster name."
  type        = string
}

variable "region" {
  description = "Region: the cluster is regional, spread across its zones."
  type        = string
}

variable "network_id" {
  description = "VPC."
  type        = string
}

variable "subnet_id" {
  description = "Node subnet."
  type        = string
}

variable "pods_range_name" {
  description = "Secondary range for pods."
  type        = string
}

variable "services_range_name" {
  description = "Secondary range for services."
  type        = string
}

variable "kms_key_id" {
  description = "Key for Kubernetes secrets and node boot disks."
  type        = string
}

variable "api_allowed_cidrs" {
  description = "Public ranges allowed to the control plane's IP endpoint. Empty (the default) leaves only the private and DNS endpoints."
  type        = list(string)
  default     = []
  validation {
    condition     = !contains(var.api_allowed_cidrs, "0.0.0.0/0")
    error_message = "Never open the Kubernetes API to the whole internet."
  }
}

variable "binary_authorization" {
  description = "Enforce the project's Binary Authorization policy (signed images only)."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Refuse to delete the cluster until this is turned off."
  type        = bool
  default     = true
}

variable "node_pools" {
  description = "Node pools by name. min and max are totals across the region's zones."
  type = map(object({
    machine_type = string
    min          = number
    max          = number
    disk_gb      = optional(number, 100)
    spot         = optional(bool, false)
    labels       = optional(map(string), {})
    taints       = optional(list(object({ key = string, value = string, effect = string })), [])
  }))
  default = {
    system = { machine_type = "e2-standard-4", min = 3, max = 6 }
    apps   = { machine_type = "n2-standard-8", min = 3, max = 12 }
  }
  nullable = false
}

variable "labels" {
  description = "Labels for every resource."
  type        = map(string)
  default     = {}
}

variable "rbac_security_group" {
  description = "Google Groups for RBAC: gke-security-groups@<your domain>. Null turns it off."
  type        = string
  default     = null
}
