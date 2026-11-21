# The case database: Cloud SQL for PostgreSQL by private IP only, encrypted with the stamp's key, SSL required,
# regional (a standby in another zone), point-in-time recovery, IAM database sign-in on, and the application password
# generated ephemerally and written to Cloud SQL and Secret Manager in the same run, never stored in state.
locals {
  tier = {
    small  = "db-custom-2-8192"
    medium = "db-custom-8-32768"
    large  = "db-custom-16-106496"
  }[var.size]
}

ephemeral "random_password" "app" {
  length           = 40
  override_special = "-_.~"
}

resource "google_sql_database_instance" "this" {
  #checkov:skip=CKV_GCP_6:ssl_mode = ENCRYPTED_ONLY is the current form of require_ssl
  #checkov:skip=CKV_GCP_109:log_min_messages is "error"; the check expects upper case, which Cloud SQL does not take
  #checkov:skip=CKV_GCP_79:The application is tested on PostgreSQL 16; raise engine_major after testing
  project             = var.project_id
  name                = var.name
  region              = var.region
  database_version    = "POSTGRES_${var.engine_major}"
  encryption_key_name = var.kms_key_id
  deletion_protection = var.deletion_protection

  settings {
    tier                        = local.tier
    edition                     = "ENTERPRISE"
    availability_type           = var.high_availability ? "REGIONAL" : "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = var.storage_gb
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    user_labels                 = var.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.network_id
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = false
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = var.backup_retention_days
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7
      hour         = 4
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      record_application_tags = true
      record_client_address   = false
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }
    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }
    database_flags {
      name  = "log_hostname"
      value = "on"
    }
    database_flags {
      name  = "log_duration"
      value = "on"
    }
    database_flags {
      name  = "log_statement"
      value = "ddl"
    }
    database_flags {
      name  = "log_min_messages"
      value = "error"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "-1" # statements are not logged with their parameters; durations come from log_duration
    }
  }

  lifecycle {
    prevent_destroy = false
  }
}

resource "google_sql_database" "this" {
  project  = var.project_id
  name     = var.database_name
  instance = google_sql_database_instance.this.name
}

resource "google_sql_user" "app" {
  project             = var.project_id
  name                = "caseforge"
  instance            = google_sql_database_instance.this.name
  password_wo         = ephemeral.random_password.app.result
  password_wo_version = var.credentials_version
}

resource "google_secret_manager_secret" "credentials" {
  project   = var.project_id
  secret_id = "${var.name}-db"
  labels    = var.labels
  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

resource "google_secret_manager_secret_version" "credentials" {
  secret                 = google_secret_manager_secret.credentials.id
  secret_data_wo         = jsonencode({ username = "caseforge", password = ephemeral.random_password.app.result })
  secret_data_wo_version = var.credentials_version
}
