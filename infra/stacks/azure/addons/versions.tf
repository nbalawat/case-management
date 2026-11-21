terraform {
  required_version = ">= 1.11.0"
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 3.0" }
    helm       = { source = "hashicorp/helm", version = "~> 3.0" }
  }

  # Settings come from envs/azure/<env>/backend.hcl through infra/bin/stack. Blob leases lock the state.
  backend "azurerm" {}

  # Azure Key Vault is not an OpenTofu key provider, so state is encrypted with a key derived from a passphrase held in
  # the GitHub environment (TF_VAR_state_passphrase), at least 16 characters.
  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
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
