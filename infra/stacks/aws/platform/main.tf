# Layer 1 of 3: the platform for one environment (network, keys, EKS, registry).
variable "platform" {
  description = "Inputs of modules/aws/platform (name, environment, network, cluster, create_registry)."
  type        = any
}

module "platform" {
  source          = "../../../modules/aws/platform"
  name            = var.platform.name
  environment     = var.platform.environment
  network         = try(var.platform.network, {})
  cluster         = try(var.platform.cluster, {})
  create_registry = try(var.platform.create_registry, true)
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
