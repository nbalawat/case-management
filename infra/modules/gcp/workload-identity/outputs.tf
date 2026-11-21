output "email" {
  description = "Email of the service account."
  value       = google_service_account.this.email
}

output "service_account_annotations" {
  description = "Annotations that bind the Kubernetes service account to the Google service account."
  value       = { "iam.gke.io/gcp-service-account" = google_service_account.this.email }
}
