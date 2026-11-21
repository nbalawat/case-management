variable "project_id" {
  description = "Project of the stamp."
  type        = string
}

variable "name" {
  description = "Instance name. Cloud SQL does not reuse a name for a week after deletion."
  type        = string
}

variable "region" {
  description = "Region."
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
  description = "Initial storage; it grows on its own."
  type        = number
  default     = 100
}

variable "high_availability" {
  description = "Regional instance with a standby in another zone."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Daily backups kept."
  type        = number
  default     = 30
}

variable "deletion_protection" {
  description = "Refuse to delete the instance until this is turned off."
  type        = bool
  default     = true
}

variable "credentials_version" {
  description = "Increase by one to generate and write a new application password."
  type        = number
  default     = 1
}

variable "network_id" {
  description = "VPC with a private services connection."
  type        = string
}

variable "kms_key_id" {
  description = "The stamp's key."
  type        = string
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
