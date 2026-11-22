variable "prefix" {
  description = "Prefix of the operator role and the machine's instance profile. Machine names must start with it."
  type        = string
  default     = "caseforge"
}

variable "regions" {
  description = "Regions the operator may create machines in."
  type        = list(string)
}

variable "state_bucket_arn" {
  description = "ARN of the OpenTofu state bucket (the bootstrap stack's)."
  type        = string
}

variable "state_key_arn" {
  description = "ARN of the KMS key that encrypts state."
  type        = string
}

variable "data_bucket_arn" {
  description = "ARN of the machine's data bucket, which the machine's own role may read and write. Null: no bucket access."
  type        = string
  default     = null
}

variable "trusted_principals" {
  description = "IAM ARNs that may assume the operator role. Empty: any identity in this account whose own policy allows sts:AssumeRole on it."
  type        = list(string)
  default     = []
}

variable "permissions_boundary_arn" {
  description = "Optional permissions boundary for the operator role."
  type        = string
  default     = null
}
