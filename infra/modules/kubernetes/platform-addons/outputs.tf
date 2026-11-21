output "installed" {
  description = "Add-on name to the chart version installed."
  value       = merge({ for k, r in helm_release.first : k => r.version }, { for k, r in helm_release.rest : k => r.version })
}

output "ingress_namespace" {
  description = "Namespace of the ingress controller."
  value       = var.ingress_namespace
}

output "cluster_issuer" {
  description = "ClusterIssuer for ingress certificates."
  value       = var.cluster_issuer
}
