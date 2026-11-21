variable "project_id" {
  description = "Project of the stamp."
  type        = string
}

variable "name" {
  description = "Service account id (6 to 30 characters)."
  type        = string
}

variable "workload_pool" {
  description = "The cluster's workload identity pool (<project>.svc.id.goog)."
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

variable "bucket_names" {
  description = "Bucket names by purpose: landing, evidence, quarantine."
  type        = map(string)
}

variable "secret_ids" {
  description = "Secret Manager secret ids the application may read."
  type        = list(string)
}
