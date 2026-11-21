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

variable "network" {
  description = "Network settings."
  type = object({
    cidr               = optional(string, "10.40.0.0/16")
    zones              = optional(number, 3)
    single_nat_gateway = optional(bool, false)
  })
  default = {}
}

variable "cluster" {
  description = "Cluster settings. node_pools uses the AWS cluster module's shape."
  type = object({
    kubernetes_version        = optional(string, "1.33")
    public_api_allowed_cidrs  = optional(list(string), [])
    private_api_allowed_cidrs = optional(list(string), [])
    admin_principal_arns      = optional(list(string), [])
    node_pools                = optional(any)
  })
  default = {}
}

variable "create_registry" {
  description = "Create ECR repositories. Off when images come from a shared or client registry."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
