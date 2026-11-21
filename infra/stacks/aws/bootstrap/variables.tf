variable "region" {
  description = "Region of the state bucket."
  type        = string
}

variable "prefix" {
  description = "Prefix for the bootstrap resources."
  type        = string
  default     = "caseforge"
}

variable "github_repository" {
  description = "owner/name of the repository whose workflows may assume the roles."
  type        = string
  default     = "nbalawat/case-management"
}

variable "environments" {
  description = "GitHub environments that get an apply role. Protect each with required reviewers."
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}

variable "permissions_boundary_arn" {
  description = "Permissions boundary for the CI roles. Strongly recommended for prod accounts."
  type        = string
  default     = null
}
