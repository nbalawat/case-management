# Artifact Registry for container images: tags cannot be overwritten, images are encrypted with the platform key,
# scanned for vulnerabilities, and untagged images are cleaned up.
resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name
  format        = "DOCKER"
  kms_key_name  = var.kms_key_id
  labels        = var.labels

  docker_config {
    immutable_tags = true
  }

  vulnerability_scanning_config {
    enablement_config = "INHERITED"
  }

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "1209600s" # 14 days
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each   = toset(var.reader_members)
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.this.name
  role       = "roles/artifactregistry.reader"
  member     = each.key
}
