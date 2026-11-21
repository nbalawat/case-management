# Plans the GCP stamp against a mocked provider: no project, no credentials, nothing created.
mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      email  = "cf-dev-acme-app@caseforge-dev.iam.gserviceaccount.com"
      member = "serviceAccount:cf-dev-acme-app@caseforge-dev.iam.gserviceaccount.com"
      name   = "projects/caseforge-dev/serviceAccounts/cf-dev-acme-app@caseforge-dev.iam.gserviceaccount.com"
    }
  }
  mock_data "google_project" {
    defaults = { number = "123456789012" }
  }
  mock_data "google_storage_project_service_account" {
    defaults = { email_address = "service-123456789012@gs-project-accounts.iam.gserviceaccount.com", member = "serviceAccount:service-123456789012@gs-project-accounts.iam.gserviceaccount.com" }
  }
}

variables {
  name        = "acme"
  environment = "dev"
  tenancy     = "dedicated"
  platform = {
    cloud      = "gcp"
    project_id = "caseforge-dev"
    region     = "europe-west2"
    network    = { network_id = "projects/caseforge-dev/global/networks/cf-dev-shared" }
    cluster    = { name = "cf-dev-shared", location = "europe-west2", workload_pool = "caseforge-dev.svc.id.goog" }
  }
}

run "dedicated_stamp" {
  command = plan

  assert {
    condition     = output.contract.cloud == "gcp" && output.contract.namespace == "cf-acme"
    error_message = "The contract names the cloud and defaults the namespace."
  }
  assert {
    condition     = alltrue([for u in values(output.contract.storage) : startswith(u, "gs://caseforge-dev-cf-dev-acme-")])
    error_message = "Storage URLs use gs:// and project-scoped bucket names."
  }
  assert {
    condition     = output.contract.secret_store.gcpsm.auth.workloadIdentity.clusterName == "cf-dev-shared"
    error_message = "External Secrets reads Secret Manager through workload identity."
  }
}

run "no_managed_kafka_or_search_on_gcp" {
  command = plan
  variables {
    kafka = { mode = "managed" }
  }
  expect_failures = [check.managed_services_supported]
}
