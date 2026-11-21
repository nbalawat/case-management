mock_provider "google" {}

variables {
  project_id = "caseforge-dev"
  name       = "caseforge-dev-cf-dev-acme"
  location   = "europe-west2"
  kms_key_id = "projects/caseforge-dev/locations/europe-west2/keyRings/r/cryptoKeys/data"
}

run "private_encrypted_retained" {
  command = plan

  assert {
    condition     = alltrue([for b in google_storage_bucket.this : b.public_access_prevention == "enforced" && b.uniform_bucket_level_access])
    error_message = "Public access is prevented and ACLs are off."
  }
  assert {
    condition     = alltrue([for b in google_storage_bucket.this : b.encryption[0].default_kms_key_name == var.kms_key_id])
    error_message = "Every bucket uses the stamp's key."
  }
  assert {
    condition     = google_storage_bucket.this["evidence"].retention_policy[0].retention_period == tostring(2555 * 86400)
    error_message = "Evidence has a retention policy."
  }
}
