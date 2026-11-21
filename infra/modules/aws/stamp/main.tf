# A stamp on AWS: everything one release of the application needs outside the cluster. Its own key, database,
# buckets and IAM role; Kafka and OpenSearch as managed services only when asked for.
locals {
  namespace = coalesce(var.namespace, "cf-${var.name}")
  id        = "cf-${var.environment}-${var.name}"
  tags = merge(var.tags, {
    "caseforge/stamp"       = var.name
    "caseforge/environment" = var.environment
    "caseforge/tenancy"     = var.tenancy
  })
  p = var.platform
}

data "aws_region" "current" {}

module "kms" {
  source      = "../kms"
  name        = local.id
  description = "caseforge stamp ${var.name} (${var.environment}): database, buckets, secrets"
  tags        = local.tags
}

module "postgres" {
  source                   = "../postgres"
  name                     = local.id
  size                     = var.postgres.size
  storage_gb               = var.postgres.storage_gb
  high_availability        = var.postgres.high_availability
  backup_retention_days    = var.postgres.backup_retention_days
  deletion_protection      = var.postgres.deletion_protection
  vpc_id                   = local.p.network.vpc_id
  subnet_ids               = local.p.network.data_subnet_ids
  client_security_group_id = local.p.cluster.node_security_group_id
  kms_key_arn              = module.kms.key_arn
  tags                     = local.tags
}

module "object_store" {
  source                  = "../object-store"
  name                    = local.id
  kms_key_arn             = module.kms.key_arn
  evidence_worm           = var.evidence.worm
  evidence_retention_days = var.evidence.retention_days
  tags                    = local.tags
}

module "kafka" {
  count                    = var.kafka.mode == "managed" ? 1 : 0
  source                   = "../kafka"
  name                     = local.id
  size                     = var.kafka.size
  vpc_id                   = local.p.network.vpc_id
  subnet_ids               = local.p.network.data_subnet_ids
  client_security_group_id = local.p.cluster.node_security_group_id
  kms_key_arn              = module.kms.key_arn
  tags                     = local.tags
}

module "search" {
  count                    = var.search.mode == "managed" ? 1 : 0
  source                   = "../search"
  name                     = substr(local.id, 0, 28)
  size                     = var.search.size
  vpc_id                   = local.p.network.vpc_id
  subnet_ids               = local.p.network.data_subnet_ids
  client_security_group_id = local.p.cluster.node_security_group_id
  kms_key_arn              = module.kms.key_arn
  tags                     = local.tags
}

module "identity" {
  source            = "../workload-identity"
  name              = local.id
  namespace         = local.namespace
  service_account   = var.service_account
  oidc_provider_arn = local.p.cluster.oidc_provider_arn
  oidc_issuer       = local.p.cluster.oidc_issuer
  bucket_arns       = module.object_store.bucket_arns
  kms_key_arn       = module.kms.key_arn
  secret_arns = concat(
    [module.postgres.credentials_secret_arn],
    module.kafka[*].credentials_secret_arn,
    module.search[*].credentials_secret_arn,
  )
  tags = local.tags
}
