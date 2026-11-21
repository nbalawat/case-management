# The stamp contract: the inputs every cloud's stamp module takes, identical byte for byte across
# modules/aws/stamp, modules/azure/stamp and modules/gcp/stamp. infra/contract/check.py fails CI when they drift.
# Edit infra/contract/stamp-variables.tf and run `make infra-contract` to copy it into each module.

variable "name" {
  description = "Stamp name: the pooled stamp (for example pool) or the dedicated client (for example acme)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "3 to 20 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment the stamp belongs to: dev, staging or prod."
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment is dev, staging or prod."
  }
}

variable "tenancy" {
  description = "pooled: many clients separated by row in one database. dedicated: one client, its own database, buckets, key and identity."
  type        = string
  validation {
    condition     = contains(["pooled", "dedicated"], var.tenancy)
    error_message = "tenancy is pooled or dedicated."
  }
}

variable "platform" {
  description = "The platform this stamp runs on: the `platform` output of the same cloud's platform module."
  type        = any
}

variable "namespace" {
  description = "Kubernetes namespace of the stamp. Defaults to cf-<name>."
  type        = string
  default     = null
}

variable "service_account" {
  description = "Kubernetes service account the application runs as."
  type        = string
  default     = "caseforge"
}

variable "postgres" {
  description = "The case database, in cloud-neutral terms."
  type = object({
    size                  = optional(string, "small")
    storage_gb            = optional(number, 100)
    high_availability     = optional(bool, true)
    backup_retention_days = optional(number, 30)
    deletion_protection   = optional(bool, true)
  })
  default = {}
}

variable "kafka" {
  description = "Event backbone. in_cluster: Strimzi in the stamp's namespace. managed: the cloud's Kafka service. none: events wait in the outbox."
  type = object({
    mode = optional(string, "in_cluster")
    size = optional(string, "small")
  })
  default = {}
  validation {
    condition     = contains(["in_cluster", "managed", "none"], var.kafka.mode)
    error_message = "kafka.mode is in_cluster, managed or none."
  }
}

variable "search" {
  description = "Name search. in_cluster: the OpenSearch operator. managed: the cloud's service, where there is one. none: search in the database."
  type = object({
    mode = optional(string, "in_cluster")
    size = optional(string, "small")
  })
  default = {}
  validation {
    condition     = contains(["in_cluster", "managed", "none"], var.search.mode)
    error_message = "search.mode is in_cluster, managed or none."
  }
}

variable "evidence" {
  description = "Evidence storage. worm makes objects write-once for retention_days; it cannot be undone."
  type = object({
    worm           = optional(bool, true)
    retention_days = optional(number, 2555)
  })
  default = {}
}

variable "tags" {
  description = "Tags or labels for every resource, on top of the ones the module sets."
  type        = map(string)
  default     = {}
}
