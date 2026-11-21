variable "subscription_id" {
  description = "Subscription the platform runs in."
  type        = string
}

variable "location" {
  description = "Region of the state account."
  type        = string
}

variable "prefix" {
  description = "Prefix for bootstrap resources (lowercase letters and digits)."
  type        = string
  default     = "caseforge"
}

variable "github_repository" {
  description = "owner/name of the repository whose workflows may sign in."
  type        = string
  default     = "nbalawat/case-management"
}

variable "environments" {
  description = "GitHub environments that get an apply identity. Protect each with required reviewers."
  type        = list(string)
  default     = ["dev", "staging", "prod"]
}

variable "allowed_ip_ranges" {
  description = "Public ranges allowed to reach the state account (GitHub-hosted runners need this; self-hosted runners in the network do not)."
  type        = list(string)
  default     = []
}
