variable "contract" {
  description = "The stamp contract: the `contract` output of the cloud's stamp module (infra/contract/stamp-outputs.md)."
  type        = any
}

variable "release" {
  description = "How the application is delivered."
  type = object({
    delivery         = optional(string, "argocd") # argocd | helm
    mode             = string                     # saas | dedicated | client-cloud | air-gapped (deploy/helm/caseforge/modes)
    host             = string                     # public host name of the stamp
    operator_issuer  = string                     # OIDC issuer for platform staff
    image_repository = string
    image_tag        = optional(string, "") # helm delivery only; with Argo CD the tag is in git
    git_repository   = optional(string, "https://github.com/nbalawat/case-management.git")
    git_revision     = optional(string, "main")
    auto_sync        = optional(bool, true)
    ingress_class    = optional(string, "traefik")
    cluster_issuer   = optional(string, "letsencrypt")
    temporal_url     = optional(string, "")
  })
  validation {
    condition     = contains(["argocd", "helm"], var.release.delivery)
    error_message = "release.delivery is argocd or helm."
  }
  validation {
    condition     = contains(["saas", "dedicated", "client-cloud", "air-gapped"], var.release.mode)
    error_message = "release.mode is saas, dedicated, client-cloud or air-gapped."
  }
  validation {
    condition     = var.release.delivery != "helm" || var.release.image_tag != ""
    error_message = "Helm delivery needs release.image_tag."
  }
  validation {
    condition     = var.release.temporal_url == "" || startswith(var.release.temporal_url, "temporal://")
    error_message = "release.temporal_url starts with temporal://."
  }
}

variable "extra_secrets" {
  description = "More environment variables from the cloud's secret manager: ENV_NAME = { key = secret name, property = optional JSON property }. For example ANTHROPIC_API_KEY."
  type = map(object({
    key      = string
    property = optional(string)
  }))
  default = {}
  validation {
    condition     = alltrue([for k in keys(var.extra_secrets) : can(regex("^[A-Z][A-Z0-9_]*$", k))])
    error_message = "Names are environment variable names: upper case, digits and underscores."
  }
}

variable "data_values" {
  description = "Overrides for deploy/helm/caseforge-data (sizes, versions, storage classes)."
  type = object({
    kafka  = optional(map(any), {})
    search = optional(map(any), {})
  })
  default = {}
}

variable "quota" {
  description = "Namespace resource quota by tenancy."
  type = object({
    pooled    = object({ cpu = string, memory = string, memory_limit = string, storage = string })
    dedicated = object({ cpu = string, memory = string, memory_limit = string, storage = string })
  })
  default = {
    pooled    = { cpu = "48", memory = "192Gi", memory_limit = "256Gi", storage = "4Ti" }
    dedicated = { cpu = "16", memory = "64Gi", memory_limit = "96Gi", storage = "1Ti" }
  }
}

variable "ingress_namespace" {
  description = "Namespace of the ingress controller."
  type        = string
  default     = "ingress"
}

variable "operator_namespaces" {
  description = "Namespaces whose pods may reach the stamp's pods (operators that manage the data services, monitoring)."
  type        = list(string)
  default     = ["strimzi", "opensearch-operator", "monitoring"]
}

variable "extra_egress_ports" {
  description = "More TCP ports the stamp may reach outside the cluster (for example 9096 for MSK, 9093 for Event Hubs)."
  type        = list(number)
  default     = [9093, 9096]
}

variable "argocd_namespace" {
  description = "Namespace Argo CD runs in."
  type        = string
  default     = "argocd"
}

variable "secret_refresh_interval" {
  description = "How often External Secrets re-reads the cloud secrets (picks up rotated passwords)."
  type        = string
  default     = "1h"
}
