output "name" {
  description = "Cluster name."
  value       = google_container_cluster.this.name
}

output "location" {
  description = "Cluster location."
  value       = google_container_cluster.this.location
}

output "endpoint" {
  description = "DNS endpoint of the control plane."
  value       = try(google_container_cluster.this.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint, null)
}

output "ca_certificate" {
  description = "Base64 cluster CA certificate."
  value       = try(google_container_cluster.this.master_auth[0].cluster_ca_certificate, null)
}

output "workload_pool" {
  description = "Workload identity pool."
  value       = "${var.project_id}.svc.id.goog"
}
