# Layer 1 of 3: the platform for one environment (APIs, network, key, GKE, Artifact Registry).
variable "platform" {
  description = "Inputs of modules/gcp/platform except project_id and region."
  type        = any
}

module "platform" {
  source          = "../../../modules/gcp/platform"
  project_id      = var.project_id
  region          = var.region
  name            = var.platform.name
  environment     = var.platform.environment
  network         = try(var.platform.network, {})
  cluster         = try(var.platform.cluster, {})
  create_registry = try(var.platform.create_registry, true)
  key_ring_suffix = try(var.platform.key_ring_suffix, "v1")
  tags            = var.tags
}

output "platform" {
  description = "Read by the add-ons and stamp layers through remote state."
  value       = module.platform.platform
}

output "kubernetes" {
  description = "How to reach the cluster."
  value       = module.platform.kubernetes
  sensitive   = true
}
