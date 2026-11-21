variable "name" {
  description = "Name of the database instance."
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

variable "engine_major" {
  description = "PostgreSQL major version."
  type        = string
  default     = "16"
}

variable "database_name" {
  description = "Database the application uses."
  type        = string
  default     = "caseforge"
}

variable "storage_gb" {
  description = "Initial storage. It grows on its own up to four times this."
  type        = number
  default     = 100
}

variable "high_availability" {
  description = "A standby in a second zone with automatic failover."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Days of point-in-time recovery."
  type        = number
  default     = 30
  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35
    error_message = "Keep at least 7 and at most 35 days."
  }
}

variable "deletion_protection" {
  description = "Refuse to delete the database until this is turned off."
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "VPC of the data subnets."
  type        = string
}

variable "subnet_ids" {
  description = "Data subnets, with no route out."
  type        = list(string)
}

variable "client_security_group_id" {
  description = "Security group of the cluster nodes, the only thing allowed to connect."
  type        = string
}

variable "kms_key_arn" {
  description = "Key for storage, the managed password and Performance Insights."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
