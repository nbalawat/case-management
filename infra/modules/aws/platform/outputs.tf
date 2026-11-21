output "platform" {
  description = "Everything a stamp and the Kubernetes layer need to know about this platform."
  value = {
    cloud       = "aws"
    name        = var.name
    environment = var.environment
    region      = data.aws_region.current.region
    kms_key_arn = module.kms.key_arn
    network = {
      vpc_id             = module.network.vpc_id
      private_subnet_ids = module.network.private_subnet_ids
      data_subnet_ids    = module.network.data_subnet_ids
    }
    cluster = {
      name                   = module.cluster.name
      endpoint               = module.cluster.endpoint
      ca_certificate         = module.cluster.ca_certificate
      oidc_provider_arn      = module.cluster.oidc_provider_arn
      oidc_issuer            = module.cluster.oidc_issuer
      node_security_group_id = module.cluster.node_security_group_id
    }
    registry = var.create_registry ? module.registry[0].repository_urls : {}
  }
}

output "kubernetes" {
  description = "How to reach the cluster's API, for the Kubernetes and Helm providers."
  sensitive   = true
  value = {
    host           = module.cluster.endpoint
    ca_certificate = module.cluster.ca_certificate
  }
}
