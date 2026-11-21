output "network_id" {
  description = "Id of the VPC."
  value       = google_compute_network.this.id
}

output "network_self_link" {
  description = "Self link of the VPC."
  value       = google_compute_network.this.self_link
}

output "subnet_id" {
  description = "Node subnet."
  value       = google_compute_subnetwork.nodes.id
}

output "pods_range_name" {
  description = "Secondary range name for pods."
  value       = "pods"
}

output "services_range_name" {
  description = "Secondary range name for services."
  value       = "services"
}

output "private_services_connection" {
  description = "The private services connection, for resources that must wait for it."
  value       = google_service_networking_connection.this.id
}
