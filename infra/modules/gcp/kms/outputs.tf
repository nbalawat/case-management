output "key_id" {
  description = "Id of the key, for CMEK settings."
  value       = google_kms_crypto_key.this.id
}
