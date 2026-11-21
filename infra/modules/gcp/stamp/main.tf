# A stamp on GCP: its own key ring, Cloud SQL instance, buckets, secrets and service account in the platform's project.
# Google Cloud has no managed OpenSearch and its managed Kafka needs credentials the application cannot use yet,
# so search and Kafka run in the cluster.
locals {
  namespace = coalesce(var.namespace, "cf-${var.name}")
  id        = "cf-${var.environment}-${var.name}"
  labels = merge(var.tags, {
    caseforge-stamp       = var.name
    caseforge-environment = var.environment
    caseforge-tenancy     = var.tenancy
  })
  p       = var.platform
  project = local.p.project_id
}

data "google_project" "this" {
  project_id = local.project
}

data "google_storage_project_service_account" "this" {
  project = local.project
}

module "kms" {
  source     = "../kms"
  name       = "${local.id}-${var.key_ring_suffix}"
  project_id = local.project
  location   = local.p.region
  encrypter_members = [
    data.google_storage_project_service_account.this.member,
    "serviceAccount:service-${data.google_project.this.number}@gcp-sa-cloud-sql.iam.gserviceaccount.com",
  ]
  labels = local.labels
}

module "postgres" {
  source                = "../postgres"
  project_id            = local.project
  name                  = "${local.id}-${var.key_ring_suffix}"
  region                = local.p.region
  size                  = var.postgres.size
  storage_gb            = var.postgres.storage_gb
  high_availability     = var.postgres.high_availability
  backup_retention_days = var.postgres.backup_retention_days
  deletion_protection   = var.postgres.deletion_protection
  network_id            = local.p.network.network_id
  kms_key_id            = module.kms.key_id
  labels                = local.labels
}

module "object_store" {
  source                  = "../object-store"
  project_id              = local.project
  name                    = "${local.project}-${local.id}"
  location                = local.p.region
  kms_key_id              = module.kms.key_id
  evidence_worm           = var.evidence.worm
  evidence_retention_days = var.evidence.retention_days
  labels                  = local.labels
}

module "identity" {
  source          = "../workload-identity"
  project_id      = local.project
  name            = "${local.id}-app"
  workload_pool   = local.p.cluster.workload_pool
  namespace       = local.namespace
  service_account = var.service_account
  bucket_names    = module.object_store.bucket_names
  secret_ids      = [module.postgres.credentials_secret_id]
}
