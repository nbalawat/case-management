output "name" {
  description = "Cluster name."
  value       = aws_eks_cluster.this.name
}

output "endpoint" {
  description = "API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "ca_certificate" {
  description = "Base64 cluster CA certificate."
  value       = aws_eks_cluster.this.certificate_authority[0].data
}

output "oidc_provider_arn" {
  description = "IAM OIDC provider for IRSA trust policies."
  value       = aws_iam_openid_connect_provider.this.arn
}

output "oidc_issuer" {
  description = "OIDC issuer host and path, without https://."
  value       = replace(aws_iam_openid_connect_provider.this.url, "https://", "")
}

output "node_security_group_id" {
  description = "Security group EKS gives the nodes and control plane, for data service ingress rules."
  value       = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id
}
