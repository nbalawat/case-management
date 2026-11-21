variable "addons" {
  description = "Per add-on overrides: { <name> = { enabled = bool, version = string, values = map } }. Values are merged over the defaults at the top level."
  type        = any
  default     = {}
}

variable "ingress_namespace" {
  description = "Namespace of the ingress controller. Stamp network policies admit traffic from it."
  type        = string
  default     = "ingress"
}

variable "load_balancer_annotations" {
  description = "Cloud annotations for the ingress load balancer (for example an internal load balancer)."
  type        = map(string)
  default     = {}
}

variable "cluster_issuer" {
  description = "cert-manager ClusterIssuer for ingress certificates."
  type        = string
  default     = "letsencrypt"
}

variable "acme_email" {
  description = "Contact address for the ACME account. Empty: no ACME issuer is created (bring your own ClusterIssuer)."
  type        = string
  default     = ""
}

variable "acme_server" {
  description = "ACME directory. Use the staging directory until DNS and ingress are proven."
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"
}

variable "argocd_host" {
  description = "Host for the Argo CD UI. Empty: no ingress (reach it with kubectl port-forward)."
  type        = string
  default     = ""
}

variable "argocd_oidc" {
  description = "Argo CD OIDC settings (name, issuer, clientID, clientSecret as $secret:key, requestedScopes). Null keeps the local admin, for bootstrap only."
  type        = any
  default     = null
}

variable "argocd_admin_groups" {
  description = "Identity-provider groups that are Argo CD admins. Everyone else signed in is read-only."
  type        = list(string)
  default     = []
}

variable "policy_action" {
  description = "Kyverno action for pod security and image signature policies: Audit while adopting, Enforce after."
  type        = string
  default     = "Enforce"
  validation {
    condition     = contains(["Audit", "Enforce"], var.policy_action)
    error_message = "policy_action is Audit or Enforce."
  }
}

variable "signed_images" {
  description = "Require caseforge images to carry a keyless cosign signature from this repository's release workflow."
  type = object({
    enabled            = bool
    image_patterns     = list(string)
    certificate_issuer = optional(string, "https://token.actions.githubusercontent.com")
    subject_regexp     = string
  })
  default = {
    enabled        = false
    image_patterns = []
    subject_regexp = ""
  }
}
