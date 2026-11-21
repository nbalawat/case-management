# GKE Standard, regional: private nodes, the control plane reached through its DNS endpoint (IAM-checked) or the
# private IP, Kubernetes secrets encrypted with a customer key, Dataplane V2 network policy, workload identity,
# Shielded nodes with secure boot, the node metadata server hidden from pods, node service account with only logging and
# monitoring rights, security posture and vulnerability scanning, and the REGULAR release channel.
resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = substr("${var.name}-nodes", 0, 30)
  display_name = "GKE nodes for ${var.name}"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  #checkov:skip=CKV_GCP_12:Dataplane V2 (ADVANCED_DATAPATH) enforces NetworkPolicy; the legacy network_policy block does not apply
  #checkov:skip=CKV_GCP_66:Binary Authorization is a variable, on in the production examples
  #checkov:skip=CKV_GCP_21:Labels come from var.labels
  #checkov:skip=CKV_GCP_65:Google Groups for RBAC needs a gke-security-groups group in the organisation; set rbac_security_group
  project             = var.project_id
  name                = var.name
  location            = var.region
  network             = var.network_id
  subnetwork          = var.subnet_id
  deletion_protection = var.deletion_protection

  remove_default_node_pool    = true
  initial_node_count          = 1
  datapath_provider           = "ADVANCED_DATAPATH"
  enable_shielded_nodes       = true
  enable_legacy_abac          = false
  enable_intranode_visibility = true

  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  dynamic "authenticator_groups_config" {
    for_each = var.rbac_security_group == null ? [] : [1]
    content {
      security_group = var.rbac_security_group
    }
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = length(var.api_allowed_cidrs) == 0
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true # every call is still authenticated and authorized by IAM
    }
  }

  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled = false
    dynamic "cidr_blocks" {
      for_each = var.api_allowed_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "allowed ${cidr_blocks.value}"
      }
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  database_encryption {
    state    = "ENCRYPTED"
    key_name = var.kms_key_id
  }

  release_channel {
    channel = "REGULAR"
  }

  binary_authorization {
    evaluation_mode = var.binary_authorization ? "PROJECT_SINGLETON_POLICY_ENFORCE" : "DISABLED"
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS", "APISERVER", "CONTROLLER_MANAGER", "SCHEDULER"]
  }

  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS", "APISERVER", "CONTROLLER_MANAGER", "SCHEDULER"]
    managed_prometheus {
      enabled = true
    }
  }

  maintenance_policy {
    recurring_window {
      start_time = "2026-01-04T03:00:00Z"
      end_time   = "2026-01-04T07:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SU"
    }
  }

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
  }

  node_config {
    service_account = google_service_account.nodes.email
    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  resource_labels = var.labels
}

resource "google_container_node_pool" "this" {
  for_each = var.node_pools
  project  = var.project_id
  name     = each.key
  cluster  = google_container_cluster.this.id
  location = var.region

  autoscaling {
    total_min_node_count = each.value.min
    total_max_node_count = each.value.max
    location_policy      = "BALANCED"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  node_config {
    machine_type      = each.value.machine_type
    disk_size_gb      = each.value.disk_gb
    disk_type         = "pd-balanced"
    boot_disk_kms_key = var.kms_key_id
    image_type        = "COS_CONTAINERD"
    service_account   = google_service_account.nodes.email
    oauth_scopes      = ["https://www.googleapis.com/auth/cloud-platform"]
    spot              = each.value.spot
    labels            = each.value.labels
    resource_labels   = var.labels
    metadata = {
      disable-legacy-endpoints = "true"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    dynamic "taint" {
      for_each = each.value.taints
      content {
        key    = taint.value.key
        value  = taint.value.value
        effect = taint.value.effect
      }
    }
  }

  lifecycle {
    ignore_changes = [node_count]
  }
}
