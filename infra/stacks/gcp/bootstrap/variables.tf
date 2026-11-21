variable "project_id" {
  description = "Project the platform runs in."
  type        = string
}

variable "region" {
  description = "Region of the state bucket and key."
  type        = string
}

variable "prefix" {
  description = "Prefix for bootstrap resources."
  type        = string
  default     = "caseforge"
}

variable "github_repository" {
  description = "owner/name of the repository whose workflows may impersonate the accounts."
  type        = string
  default     = "nbalawat/case-management"
}

variable "environments" {
  description = "GitHub environments that get an apply account. Protect each with required reviewers."
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}
