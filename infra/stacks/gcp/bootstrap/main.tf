# Run once per GCP project, with local state, by an administrator: the state bucket, the key that encrypts state
# client-side, and Workload Identity Federation so GitHub Actions impersonates a read-only plan account or a
# per-environment apply account with no service account keys.
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 8.0" }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "this" {
  for_each           = toset(["cloudkms.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com", "storage.googleapis.com"])
  service            = each.key
  disable_on_destroy = false
}

data "google_storage_project_service_account" "this" {}

module "state_key" {
  source            = "../../../modules/gcp/kms"
  project_id        = var.project_id
  name              = "${var.prefix}-tofu-state"
  location          = var.region
  protection_level  = "SOFTWARE"
  encrypter_members = [data.google_storage_project_service_account.this.member]
  depends_on        = [google_project_service.this]
}

resource "google_storage_bucket" "state" {
  #checkov:skip=CKV_GCP_62:State access is in Cloud Audit Logs (data access logs); enable them for the project
  name                        = "${var.project_id}-${var.prefix}-tofu-state"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  encryption {
    default_kms_key_name = module.state_key.key_id
  }
  lifecycle_rule {
    condition {
      num_newer_versions = 50
    }
    action {
      type = "Delete"
    }
  }
}

# ---- GitHub Actions through Workload Identity Federation
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.prefix}-github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  #checkov:skip=CKV_GCP_125:attribute_condition pins the repository; each account is bound to one exact subject
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.repository"  = "assertion.repository"
    "attribute.environment" = "assertion.environment"
    "attribute.event_name"  = "assertion.event_name"
  }
  # Only this repository's workflows can exchange tokens at all.
  attribute_condition = "assertion.repository == '${var.github_repository}'"
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

locals {
  accounts = merge(
    { plan = { principal = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:pull_request", roles = ["roles/viewer", "roles/iam.securityReviewer"] } },
    { for env in var.environments : "apply-${env}" => {
      principal = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${env}"
      roles = [
        "roles/editor",
        "roles/container.admin",
        "roles/iam.serviceAccountAdmin",
        "roles/resourcemanager.projectIamAdmin",
        "roles/cloudkms.admin",
        "roles/secretmanager.admin",
        "roles/servicenetworking.networksAdmin",
      ]
    } },
  )
  bindings = merge([for k, a in local.accounts : { for r in a.roles : "${k}/${r}" => { account = k, role = r } }]...)
}

resource "google_service_account" "github" {
  for_each     = local.accounts
  account_id   = substr("${var.prefix}-gh-${each.key}", 0, 30)
  display_name = "GitHub Actions ${each.key}"
}

resource "google_service_account_iam_member" "github" {
  for_each           = local.accounts
  service_account_id = google_service_account.github[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value.principal
}

# The read-only plan account also plans on main, for infra-apply.yml, which then applies that plan in a protected
# environment with the apply account.
resource "google_service_account_iam_member" "plan_main" {
  service_account_id = google_service_account.github["plan"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:ref:refs/heads/main"
}

resource "google_project_iam_member" "github" {
  for_each = local.bindings
  project  = var.project_id
  role     = each.value.role
  member   = google_service_account.github[each.value.account].member
}

resource "google_storage_bucket_iam_member" "state" {
  for_each = local.accounts
  bucket   = google_storage_bucket.state.name
  role     = "roles/storage.objectUser"
  member   = google_service_account.github[each.key].member
}

resource "google_kms_crypto_key_iam_member" "state" {
  for_each      = local.accounts
  crypto_key_id = module.state_key.key_id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = google_service_account.github[each.key].member
}
