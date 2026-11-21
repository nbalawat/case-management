output "bucket_names" {
  description = "Purpose to bucket name."
  value       = { for k, b in google_storage_bucket.this : k => b.name }
}

output "urls" {
  description = "Purpose to the URL the application is given."
  value       = { for k, b in google_storage_bucket.this : k => "gs://${b.name}/caseforge" }
}
