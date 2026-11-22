# infra/bin/cloud.py writes these into a variables file per machine; nothing here is secret.

variable "region" {
  description = "AWS region of the machine and of the setup's state bucket and key."
  type        = string
}

variable "state_encryption_key" {
  description = "KMS key ARN for OpenTofu state encryption (setup output state_key_arn)."
  type        = string
}

variable "operator_role_arn" {
  description = "Role to assume (setup output machine_operator_role_arn). Null: use the caller's own credentials."
  type        = string
  default     = null
}

variable "instance_profile_name" {
  description = "The setup's instance profile for the machine (setup output machine_instance_profile)."
  type        = string
  default     = null
}

variable "name" {
  description = "Machine name."
  type        = string
  default     = "caseforge"
}

variable "size" {
  description = "small, large or xlarge (see infra/contract/machine-variables.tf)."
  type        = string
  default     = "large"
}

variable "disk_gb" {
  description = "System disk size in GB."
  type        = number
  default     = 150
}

variable "allowed_cidrs" {
  description = "Addresses that may reach SSH."
  type        = list(string)
}

variable "running" {
  description = "false stops the machine and keeps its disk."
  type        = bool
  default     = true
}

variable "budget_email" {
  description = "E-mail for the monthly budget alert; empty for none."
  type        = string
  default     = ""
}

variable "budget_usd" {
  description = "Monthly budget in US dollars."
  type        = number
  default     = 150
}

variable "ssh_public_key" {
  description = "The operator's public key."
  type        = string
}

variable "stack" {
  description = "Makefile target the machine runs."
  type        = string
  default     = "full"
}

variable "git_url" {
  description = "Repository the machine clones."
  type        = string
  default     = "https://github.com/nbalawat/case-management.git"
}

variable "git_ref" {
  description = "Branch, tag or commit the machine runs first."
  type        = string
  default     = "main"
}

variable "auto_stop_hours" {
  description = "Hours after each boot until the machine stops itself; 0 for never."
  type        = number
  default     = 10
}

variable "tags" {
  description = "Tags on every resource."
  type        = map(string)
  default     = {}
}
