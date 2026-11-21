# A platform on GCP: the APIs it needs, a VPC with private services access, the platform key, a regional GKE cluster
# and Artifact Registry, all in one project.
locals {
  id = "cf-${var.environment}-${var.name}"
  labels = merge(var.tags, {
    caseforge-platform    = var.name
    caseforge-environment = var.environment
  })
  apis = [
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "containerscanning.googleapis.com",
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
  ]
}

resource "google_project_service" "this" {
  for_each           = toset(local.apis)
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "this" {
  project_id = var.project_id
}

module "network" {
  source        = "../network"
  project_id    = var.project_id
  name          = local.id
  region        = var.region
  cidr          = var.network.cidr
  pods_cidr     = var.network.pods_cidr
  services_cidr = var.network.services_cidr
  depends_on    = [google_project_service.this]
}

module "kms" {
  source     = "../kms"
  project_id = var.project_id
  name       = "${local.id}-platform-${var.key_ring_suffix}"
  location   = var.region
  encrypter_members = [
    "serviceAccount:service-${data.google_project.this.number}@container-engine-robot.iam.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.this.number}@compute-system.iam.gserviceaccount.com",
    "serviceAccount:service-${data.google_project.this.number}@gcp-sa-artifactregistry.iam.gserviceaccount.com",
  ]
  labels     = local.labels
  depends_on = [google_project_service.this]
}

module "cluster" {
  source               = "../cluster"
  project_id           = var.project_id
  name                 = local.id
  region               = var.region
  network_id           = module.network.network_id
  subnet_id            = module.network.subnet_id
  pods_range_name      = module.network.pods_range_name
  services_range_name  = module.network.services_range_name
  kms_key_id           = module.kms.key_id
  api_allowed_cidrs    = var.cluster.api_allowed_cidrs
  binary_authorization = var.cluster.binary_authorization
  deletion_protection  = var.environment == "prod"
  node_pools           = var.cluster.node_pools
  labels               = local.labels
}

module "registry" {
  count      = var.create_registry ? 1 : 0
  source     = "../registry"
  project_id = var.project_id
  name       = local.id
  region     = var.region
  kms_key_id = module.kms.key_id
  labels     = local.labels
}
