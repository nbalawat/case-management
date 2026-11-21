variable "name" {
  description = "Prefix for bucket names."
  type        = string
}

variable "kms_key_arn" {
  description = "Key that encrypts every object."
  type        = string
}

variable "evidence_worm" {
  description = "Write-once evidence: S3 Object Lock in compliance mode. Cannot be turned off once on."
  type        = bool
  default     = true
}

variable "evidence_retention_days" {
  description = "How long evidence objects are locked and old versions kept."
  type        = number
  default     = 2555 # seven years
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
