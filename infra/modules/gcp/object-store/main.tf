# The stamp's buckets: landing, evidence and quarantine. Uniform bucket-level access, public access prevented,
# encrypted with the stamp's key, versioned, soft delete, access logs to a separate bucket, and on evidence a retention
# policy (locked only when asked, because a locked policy can never be shortened).
locals {
  buckets = toset(["landing", "evidence", "quarantine"])
}

resource "google_storage_bucket" "logs" {
  #checkov:skip=CKV_GCP_62:This is the access-log bucket; logging it into itself would loop
  project                     = var.project_id
  name                        = "${var.name}-access-logs"
  location                    = var.location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "this" {
  for_each                    = local.buckets
  project                     = var.project_id
  name                        = "${var.name}-${each.key}"
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = merge(var.labels, { purpose = each.key })

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 2592000 # 30 days
  }

  logging {
    log_bucket        = google_storage_bucket.logs.name
    log_object_prefix = each.key
  }

  dynamic "retention_policy" {
    for_each = each.key == "evidence" && var.evidence_worm ? [1] : []
    content {
      retention_period = var.evidence_retention_days * 86400
      is_locked        = var.lock_evidence_policy
    }
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 3
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  dynamic "lifecycle_rule" {
    for_each = each.key == "landing" ? [1] : []
    content {
      condition {
        age = var.landing_expiry_days
      }
      action {
        type = "Delete"
      }
    }
  }
}
