# Layer 1 of 3: the platform for one environment (resource group, network, Key Vault, Log Analytics, AKS, ACR).
variable "platform" {
  description = "Inputs of modules/azure/platform."
  type        = any
}

module "platform" {
  source             = "../../../modules/azure/platform"
  name               = var.platform.name
  environment        = var.platform.environment
  location           = var.platform.location
  zones              = try(var.platform.zones, ["1", "2", "3"])
  network            = try(var.platform.network, {})
  cluster            = try(var.platform.cluster, {})
  deployer_ip_ranges = try(var.platform.deployer_ip_ranges, [])
  create_registry    = try(var.platform.create_registry, true)
  tags               = var.tags
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
