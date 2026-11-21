terraform {
  required_version = ">= 1.11.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 8.0" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }

  # Settings come from envs/gcp/<env>/backend.hcl through infra/bin/stack. GCS locks the state.
  backend "gcs" {}

  encryption {
    key_provider "gcp_kms" "state" {
      kms_encryption_key = var.state_encryption_key
      key_length         = 32
    }
    method "aes_gcm" "state" {
      keys = key_provider.gcp_kms.state
    }
    state {
      method   = method.aes_gcm.state
      enforced = true
    }
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
    remote_state_data_sources {
      default {
        method = method.aes_gcm.state
      }
    }
  }
}
