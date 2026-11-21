# Layer 2 of 3: cluster add-ons, from the cloud-neutral module.
variable "platform_state" {
  description = "Backend settings of this environment's platform state."
  type        = map(string)
}

variable "addons" {
  description = "Inputs of modules/kubernetes/platform-addons."
  type        = any
  default     = {}
}

module "addons" {
  source            = "../../../modules/kubernetes/platform-addons"
  addons            = try(var.addons.addons, {})
  ingress_namespace = try(var.addons.ingress_namespace, "ingress")
  load_balancer_annotations = merge({
    "service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path" = "/ping"
  }, try(var.addons.load_balancer_annotations, {}))
  cluster_issuer      = try(var.addons.cluster_issuer, "letsencrypt")
  acme_email          = try(var.addons.acme_email, "")
  acme_server         = try(var.addons.acme_server, "https://acme-v02.api.letsencrypt.org/directory")
  argocd_host         = try(var.addons.argocd_host, "")
  argocd_oidc         = try(var.addons.argocd_oidc, null)
  argocd_admin_groups = try(var.addons.argocd_admin_groups, [])
  policy_action       = try(var.addons.policy_action, "Enforce")
  signed_images       = try(var.addons.signed_images, { enabled = false, image_patterns = [], subject_regexp = "" })
}

output "installed" {
  description = "Add-on versions installed."
  value       = module.addons.installed
}
