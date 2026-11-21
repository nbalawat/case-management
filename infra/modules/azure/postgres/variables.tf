variable "name" {
  description = "Server name: globally unique."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the stamp."
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
  default     = 128
}

variable "high_availability" {
  description = "Zone-redundant standby with automatic failover."
  type        = bool
  default     = true
}

variable "backup_retention_days" {
  description = "Days of point-in-time recovery (7 to 35)."
  type        = number
  default     = 30
}

variable "deletion_protection" {
  description = "A CanNotDelete lock on the server."
  type        = bool
  default     = true
}

variable "credentials_version" {
  description = "Increase by one to generate and write a new admin password."
  type        = number
  default     = 1
}

variable "subnet_id" {
  description = "Subnet delegated to Flexible Server."
  type        = string
}

variable "private_dns_zone_id" {
  description = "Private DNS zone for Flexible Server."
  type        = string
}

variable "key_vault_id" {
  description = "The stamp's vault, for the key and the credentials secret."
  type        = string
}

variable "key_id" {
  description = "Versionless id of the stamp's key."
  type        = string
}

variable "log_analytics_workspace_id" {
  description = "Workspace for audit and server logs. Null turns them off."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
