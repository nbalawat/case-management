variable "name" {
  description = "Domain name (3 to 28 lowercase characters)."
  type        = string
}

variable "size" {
  description = "Cloud-neutral size: small, medium or large."
  type        = string
  default     = "small"
  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size is small, medium or large."
  }
}

variable "engine_version" {
  description = "OpenSearch engine version."
  type        = string
  default     = "OpenSearch_2.19"
}

variable "storage_gb" {
  description = "Storage per node."
  type        = number
  default     = 100
}

variable "vpc_id" {
  description = "VPC of the data subnets."
  type        = string
}

variable "subnet_ids" {
  description = "Data subnets."
  type        = list(string)
}

variable "client_security_group_id" {
  description = "Security group of the cluster nodes."
  type        = string
}

variable "kms_key_arn" {
  description = "Key for storage, logs and the master secret."
  type        = string
}

variable "log_retention_days" {
  description = "How long search logs are kept."
  type        = number
  default     = 365
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
