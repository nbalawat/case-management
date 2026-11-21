variable "account_name" {
  description = "Storage account name: globally unique, 3 to 24 lowercase letters and digits."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.account_name))
    error_message = "3 to 24 lowercase letters and digits."
  }
}

variable "name" {
  description = "Stamp id, for the identity name."
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

variable "replication" {
  description = "ZRS within the region; GZRS for a copy in the paired region."
  type        = string
  default     = "ZRS"
  validation {
    condition     = contains(["ZRS", "GZRS", "RAGZRS"], var.replication)
    error_message = "Use ZRS, GZRS or RAGZRS: evidence must survive a zone."
  }
}

variable "key_vault_id" {
  description = "The stamp's vault."
  type        = string
}

variable "key_id" {
  description = "Versionless id of the stamp's key."
  type        = string
}

variable "endpoints_subnet_id" {
  description = "Subnet for private endpoints."
  type        = string
}

variable "private_dns_zone_ids" {
  description = "Private DNS zone ids for the blob and dfs endpoints."
  type        = map(string)
  validation {
    condition     = length(setsubtract(["blob", "dfs"], keys(var.private_dns_zone_ids))) == 0
    error_message = "Give zone ids for blob and dfs."
  }
}

variable "evidence_worm" {
  description = "Time-based retention on the evidence container."
  type        = bool
  default     = true
}

variable "lock_evidence_policy" {
  description = "Lock the retention policy so nobody can shorten it. Irreversible; leave off until retention is agreed."
  type        = bool
  default     = false
}

variable "evidence_retention_days" {
  description = "Days evidence cannot be changed or deleted."
  type        = number
  default     = 2555
}

variable "landing_expiry_days" {
  description = "Days before landed source files are removed."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}

variable "log_analytics_workspace_id" {
  description = "Workspace for blob access logs. Null turns them off."
  type        = string
  default     = null
}
