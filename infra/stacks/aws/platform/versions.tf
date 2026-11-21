terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
    tls    = { source = "hashicorp/tls", version = "~> 4.0" }
  }

  # Settings come from envs/aws/<env>/backend.hcl through infra/bin/stack. S3 native locking, no DynamoDB table.
  backend "s3" {}

  # State and plans are encrypted before they leave the runner, with the key from the bootstrap stack.
  encryption {
    key_provider "aws_kms" "state" {
      kms_key_id = var.state_encryption_key
      region     = var.region
      key_spec   = "AES_256"
    }
    method "aes_gcm" "state" {
      keys = key_provider.aws_kms.state
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
