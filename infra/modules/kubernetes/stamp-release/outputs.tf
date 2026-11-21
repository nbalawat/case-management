output "namespace" {
  description = "Namespace of the stamp."
  value       = local.namespace
}

output "public_url" {
  description = "Where the stamp is served."
  value       = local.public_url
}

output "secret_name" {
  description = "The Secret the chart reads (assembled by External Secrets)."
  value       = local.secret_name
}

output "landing_url" {
  description = "Landing location to register for the stamp's data pipelines."
  value       = local.c.storage.landing_url
}

output "app_values" {
  description = "The values given to the application chart, for review."
  value       = local.app_values
}
