variable "region" {
  description = "AWS region."
  type        = string
}

variable "state_encryption_key" {
  description = "KMS key ARN for OpenTofu state encryption (bootstrap output state_key_arn)."
  type        = string
}

variable "tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = merge(var.tags, { "caseforge/managed-by" = "opentofu" })
  }
}
