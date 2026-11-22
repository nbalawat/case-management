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

variable "machine_regions" {
  description = "Regions the cloud machine may run in. Empty: the bootstrap's region."
  type        = list(string)
  default     = []
}

variable "machine_operators" {
  description = "IAM ARNs allowed to assume the cloud machine's operator role. Empty: any identity in the account permitted to assume it."
  type        = list(string)
  default     = []
}

variable "teardown" {
  description = "Set only by `infra/bin/cloud.py teardown`, just before destroying: lets the state bucket be deleted with its contents and shortens the key's deletion window to 7 days."
  type        = bool
  default     = false
}
