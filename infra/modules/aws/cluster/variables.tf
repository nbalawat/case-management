variable "name" {
  description = "Cluster name."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes minor version. Upgrade one minor at a time."
  type        = string
  default     = "1.33"
}

variable "vpc_id" {
  description = "VPC the cluster runs in."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the control plane interfaces and the nodes."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "Key for Kubernetes secrets, node volumes and the control plane log group."
  type        = string
}

variable "public_api_allowed_cidrs" {
  description = "Addresses that may reach the API from the internet. Empty (the default) keeps the endpoint private."
  type        = list(string)
  default     = []
  validation {
    condition     = !contains(var.public_api_allowed_cidrs, "0.0.0.0/0")
    error_message = "Never open the Kubernetes API to the whole internet."
  }
}

variable "private_api_allowed_cidrs" {
  description = "Private ranges (VPN, CI runners, peered VPCs) that may reach the private API endpoint."
  type        = list(string)
  default     = []
}

variable "admin_principal_arns" {
  description = "IAM roles given cluster admin through access entries, such as the platform team's SSO role and the deploy role."
  type        = list(string)
  default     = []
}

variable "node_pools" {
  description = "Managed node groups by name."
  type = map(object({
    instance_types = list(string)
    min            = number
    max            = number
    disk_gb        = optional(number, 100)
    spot           = optional(bool, false)
    labels         = optional(map(string), {})
    taints         = optional(list(object({ key = string, value = string, effect = string })), [])
  }))
  nullable = false
  default = {
    system = { instance_types = ["m7i.large"], min = 2, max = 4 }
    apps   = { instance_types = ["m7i.xlarge"], min = 3, max = 12 }
  }
}

variable "log_retention_days" {
  description = "How long control plane logs are kept."
  type        = number
  default     = 365
}

variable "deletion_protection" {
  description = "Refuse to delete the cluster until this is turned off."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
