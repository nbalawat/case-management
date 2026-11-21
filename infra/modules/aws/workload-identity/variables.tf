variable "name" {
  description = "Stamp name, used in the role name."
  type        = string
}

variable "namespace" {
  description = "Kubernetes namespace of the stamp."
  type        = string
}

variable "service_account" {
  description = "Kubernetes service account the application runs as."
  type        = string
}

variable "oidc_provider_arn" {
  description = "The cluster's IAM OIDC provider."
  type        = string
}

variable "oidc_issuer" {
  description = "The cluster's OIDC issuer, without https://."
  type        = string
}

variable "bucket_arns" {
  description = "Bucket ARNs by purpose: landing, evidence, quarantine."
  type        = map(string)
  validation {
    condition     = alltrue([for k in ["landing", "evidence", "quarantine"] : contains(keys(var.bucket_arns), k)])
    error_message = "bucket_arns needs landing, evidence and quarantine."
  }
}

variable "secret_arns" {
  description = "Secrets the application (through External Secrets) may read."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "The stamp's key."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
