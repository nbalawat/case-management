output "id" {
  description = "Cluster resource id."
  value       = azurerm_kubernetes_cluster.this.id
}

output "name" {
  description = "Cluster name."
  value       = azurerm_kubernetes_cluster.this.name
}

output "host" {
  description = "API server address."
  value       = try(azurerm_kubernetes_cluster.this.kube_config[0].host, null)
  sensitive   = true
}

output "ca_certificate" {
  description = "Base64 cluster CA certificate."
  value       = try(azurerm_kubernetes_cluster.this.kube_config[0].cluster_ca_certificate, null)
  sensitive   = true
}

output "oidc_issuer_url" {
  description = "Issuer for workload identity federated credentials."
  value       = azurerm_kubernetes_cluster.this.oidc_issuer_url
}

output "kubelet_object_id" {
  description = "Object id of the kubelet identity, for registry pull access."
  value       = try(azurerm_kubernetes_cluster.this.kubelet_identity[0].object_id, null)
}
