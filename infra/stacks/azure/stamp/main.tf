# Layer 3 of 3: one stamp (the pooled stamp or a dedicated client): cloud resources, then its release in the cluster.
variable "platform_state" {
  description = "Backend settings of this environment's platform state."
  type        = map(string)
}

variable "stamp" {
  description = "Inputs of the stamp contract (infra/contract/stamp-variables.tf) except platform, plus deployer_ip_ranges."
  type        = any
}

variable "release" {
  description = "Inputs of modules/kubernetes/stamp-release: release, extra_secrets, data_values."
  type        = any
}

module "stamp" {
  source             = "../../../modules/azure/stamp"
  platform           = local.platform
  name               = var.stamp.name
  environment        = var.stamp.environment
  tenancy            = var.stamp.tenancy
  namespace          = try(var.stamp.namespace, null)
  service_account    = try(var.stamp.service_account, "caseforge")
  postgres           = try(var.stamp.postgres, {})
  kafka              = try(var.stamp.kafka, {})
  search             = try(var.stamp.search, {})
  evidence           = try(var.stamp.evidence, {})
  deployer_ip_ranges = try(var.stamp.deployer_ip_ranges, [])
  tags               = var.tags
}

module "release" {
  source        = "../../../modules/kubernetes/stamp-release"
  contract      = module.stamp.contract
  release       = var.release.release
  extra_secrets = try(var.release.extra_secrets, {})
  data_values   = try(var.release.data_values, {})
}

output "contract" {
  description = "The stamp contract."
  value       = module.stamp.contract
}

output "public_url" {
  description = "Where the stamp is served."
  value       = module.release.public_url
}

output "landing_url" {
  description = "Landing location for the stamp's data pipelines."
  value       = module.release.landing_url
}
