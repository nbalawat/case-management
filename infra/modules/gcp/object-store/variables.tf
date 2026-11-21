variable "project_id" {
  description = "Project of the stamp."
  type        = string
}

variable "name" {
  description = "Prefix for bucket names (globally unique)."
  type        = string
}

variable "location" {
  description = "Region or multi-region of the buckets; it must match the key's location."
  type        = string
}

variable "kms_key_id" {
  description = "The stamp's key. The Cloud Storage service agent needs encrypter/decrypter on it."
  type        = string
}

variable "evidence_worm" {
  description = "Retention policy on the evidence bucket."
  type        = bool
  default     = true
}

variable "lock_evidence_policy" {
  description = "Lock the retention policy. Irreversible; leave off until retention is agreed."
  type        = bool
  default     = false
}

variable "evidence_retention_days" {
  description = "Days evidence objects cannot be deleted or replaced."
  type        = number
  default     = 2555
}

variable "landing_expiry_days" {
  description = "Days before landed source files are removed."
  type        = number
  default     = 30
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
