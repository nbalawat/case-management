check "search_mode_supported" {
  assert {
    condition     = var.search.mode != "managed"
    error_message = "Azure has no managed OpenSearch service: use search.mode = in_cluster or none."
  }
}
