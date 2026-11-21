variable "name" {
  description = "Prefix for repository names."
  type        = string
}

variable "repositories" {
  description = "Repositories to create under the prefix."
  type        = list(string)
  default     = ["caseforge", "caseforge-flink"]
}

variable "untagged_expiry_days" {
  description = "Days after which untagged images are removed."
  type        = number
  default     = 14
}

variable "kms_key_arn" {
  description = "Key that encrypts images at rest."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
