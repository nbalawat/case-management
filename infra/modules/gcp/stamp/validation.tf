check "managed_services_supported" {
  assert {
    condition     = var.search.mode != "managed" && var.kafka.mode != "managed"
    error_message = "On GCP, run search and Kafka in the cluster (mode = in_cluster) or turn them off (none)."
  }
}
