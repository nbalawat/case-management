variable "project_id" {
  description = "Project the platform and its stamps live in."
  type        = string
}

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

variable "region" {
  description = "Region, for example europe-west2."
  type        = string
}

variable "network" {
  description = "Network settings."
  type = object({
    cidr          = optional(string, "10.60.0.0/16")
    pods_cidr     = optional(string, "10.64.0.0/14")
    services_cidr = optional(string, "10.68.0.0/20")
  })
  default = {}
}

variable "cluster" {
  description = "Cluster settings. node_pools follows modules/gcp/cluster."
  type = object({
    api_allowed_cidrs    = optional(list(string), [])
    binary_authorization = optional(bool, false)
    node_pools           = optional(any)
  })
  default = {}
}

variable "create_registry" {
  description = "Create an Artifact Registry repository."
  type        = bool
  default     = true
}

variable "key_ring_suffix" {
  description = "Key rings cannot be deleted: change this to recreate the platform with new names."
  type        = string
  default     = "v1"
}

variable "tags" {
  description = "Labels for every resource."
  type        = map(string)
  default     = {}
}
