output "state_bucket" {
  description = "Bucket for envs/gcp/<env>/backend.hcl."
  value       = google_storage_bucket.state.name
}

output "state_key_id" {
  description = "KMS key for state_encryption_key in every stack."
  value       = module.state_key.key_id
}

output "workload_identity_provider" {
  description = "GCP_WORKLOAD_IDENTITY_PROVIDER for GitHub."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "github_service_accounts" {
  description = "GCP_SERVICE_ACCOUNT for each GitHub environment."
  value       = { for k, a in google_service_account.github : k => a.email }
}
