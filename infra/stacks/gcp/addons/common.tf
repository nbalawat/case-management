variable "project_id" {
  description = "Project of the platform."
  type        = string
}

variable "region" {
  description = "Region."
  type        = string
}

variable "state_encryption_key" {
  description = "KMS key id for OpenTofu state encryption (bootstrap output state_key_id)."
  type        = string
}

variable "tags" {
  description = "Labels on every resource."
  type        = map(string)
  default     = {}
}

provider "google" {
  project        = var.project_id
  region         = var.region
  default_labels = merge(var.tags, { caseforge-managed-by = "opentofu" })
}
