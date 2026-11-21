# A platform on AWS: the shared foundation one environment runs on. Network, platform key, EKS and the registry.
# Stamps (the pooled one and any dedicated clients) are created on top of it by modules/aws/stamp.
locals {
  id = "cf-${var.environment}-${var.name}"
  tags = merge(var.tags, {
    "caseforge/platform"    = var.name
    "caseforge/environment" = var.environment
  })
}

data "aws_region" "current" {}

module "kms" {
  source             = "../kms"
  name               = "${local.id}-platform"
  description        = "caseforge platform ${var.name} (${var.environment}): cluster secrets, nodes, logs, images"
  service_principals = ["logs.${data.aws_region.current.region}.amazonaws.com"]
  tags               = local.tags
}

module "network" {
  source             = "../network"
  name               = local.id
  cidr               = var.network.cidr
  az_count           = var.network.zones
  single_nat_gateway = var.environment != "prod" && var.network.single_nat_gateway
  kms_key_arn        = module.kms.key_arn
  tags               = local.tags
}

module "cluster" {
  source                    = "../cluster"
  name                      = local.id
  kubernetes_version        = var.cluster.kubernetes_version
  vpc_id                    = module.network.vpc_id
  subnet_ids                = module.network.private_subnet_ids
  kms_key_arn               = module.kms.key_arn
  public_api_allowed_cidrs  = var.cluster.public_api_allowed_cidrs
  private_api_allowed_cidrs = var.cluster.private_api_allowed_cidrs
  admin_principal_arns      = var.cluster.admin_principal_arns
  node_pools                = var.cluster.node_pools
  deletion_protection       = var.environment == "prod"
  tags                      = local.tags
}

module "registry" {
  count       = var.create_registry ? 1 : 0
  source      = "../registry"
  name        = local.id
  kms_key_arn = module.kms.key_arn
  tags        = local.tags
}
