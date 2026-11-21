output "platform" {
  description = "Everything a stamp and the Kubernetes layer need to know about this platform."
  value = {
    cloud       = "gcp"
    name        = var.name
    environment = var.environment
    project_id  = var.project_id
    region      = var.region
    network = {
      network_id = module.network.network_id
      subnet_id  = module.network.subnet_id
    }
    cluster = {
      name          = module.cluster.name
      location      = module.cluster.location
      workload_pool = module.cluster.workload_pool
    }
    registry = var.create_registry ? { caseforge = "${module.registry[0].url}/caseforge" } : {}
  }
}

output "kubernetes" {
  description = "How to reach the cluster's API, for the Kubernetes and Helm providers."
  sensitive   = true
  value = {
    host           = "https://${module.cluster.endpoint}"
    ca_certificate = module.cluster.ca_certificate
  }
}
