# A key ring and a rotating key for one platform or stamp, plus encrypter/decrypter grants for the Google service
# agents that use it. Keys are never destroyed by OpenTofu: removing one schedules destruction after 30 days.
resource "google_kms_key_ring" "this" {
  project  = var.project_id
  name     = var.name
  location = var.location
}

resource "google_kms_crypto_key" "this" {
  name                       = "data"
  key_ring                   = google_kms_key_ring.this.id
  purpose                    = "ENCRYPT_DECRYPT"
  rotation_period            = "7776000s" # 90 days
  destroy_scheduled_duration = "2592000s" # 30 days
  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.protection_level
  }
  labels = var.labels
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_kms_crypto_key_iam_member" "users" {
  for_each      = toset(var.encrypter_members)
  crypto_key_id = google_kms_crypto_key.this.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.key
}
