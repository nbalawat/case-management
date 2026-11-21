variable "name" {
  description = "Stamp id, used in the identity name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the stamp."
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

variable "oidc_issuer_url" {
  description = "The cluster's OIDC issuer."
  type        = string
}

variable "container_ids" {
  description = "Container resource ids by purpose: landing, evidence, quarantine."
  type        = map(string)
}

variable "key_vault_id" {
  description = "The stamp's vault."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
