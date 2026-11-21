mock_provider "google" {}

variables {
  project_id          = "caseforge-dev"
  name                = "cf-dev-shared"
  region              = "europe-west2"
  network_id          = "projects/caseforge-dev/global/networks/n"
  subnet_id           = "projects/caseforge-dev/regions/europe-west2/subnetworks/s"
  pods_range_name     = "pods"
  services_range_name = "services"
  kms_key_id          = "projects/caseforge-dev/locations/europe-west2/keyRings/r/cryptoKeys/data"
}

run "private_nodes_encrypted_secrets_hidden_metadata" {
  command = plan

  assert {
    condition     = google_container_cluster.this.private_cluster_config[0].enable_private_nodes && google_container_cluster.this.private_cluster_config[0].enable_private_endpoint
    error_message = "Nodes and the IP endpoint are private by default."
  }
  assert {
    condition     = google_container_cluster.this.database_encryption[0].state == "ENCRYPTED"
    error_message = "Kubernetes secrets are encrypted with the platform key."
  }
  assert {
    condition     = alltrue([for p in google_container_node_pool.this : p.node_config[0].workload_metadata_config[0].mode == "GKE_METADATA"])
    error_message = "Pods see only the GKE metadata server, never the node's credentials."
  }
  assert {
    condition     = alltrue([for p in google_container_node_pool.this : p.node_config[0].shielded_instance_config[0].enable_secure_boot])
    error_message = "Nodes boot securely."
  }
}
