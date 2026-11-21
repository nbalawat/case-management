# One Google service account per stamp, impersonable only by that stamp's Kubernetes service account, with access only
# to its own buckets and its own secrets. No project-wide roles.
resource "google_service_account" "this" {
  project      = var.project_id
  account_id   = substr(replace(var.name, "_", "-"), 0, 30)
  display_name = "caseforge application, stamp ${var.name}"
}

resource "google_service_account_iam_member" "workload_identity" {
  service_account_id = google_service_account.this.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.workload_pool}[${var.namespace}/${var.service_account}]"
}

resource "google_storage_bucket_iam_member" "landing" {
  bucket = var.bucket_names["landing"]
  role   = "roles/storage.objectUser" # read, and remove a file once it is moved to quarantine
  member = google_service_account.this.member
}

resource "google_storage_bucket_iam_member" "write" {
  for_each = toset(["evidence", "quarantine"])
  bucket   = var.bucket_names[each.key]
  role     = "roles/storage.objectUser"
  member   = google_service_account.this.member
}

resource "google_secret_manager_secret_iam_member" "this" {
  for_each  = toset(var.secret_ids)
  project   = var.project_id
  secret_id = each.key
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.this.member
}
