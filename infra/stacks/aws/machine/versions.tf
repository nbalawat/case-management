terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }

  # infra/bin/cloud.py passes the bucket from the setup (bootstrap) and a key per machine: machine/<name>.tfstate.
  # S3 native locking, no DynamoDB table.
  backend "s3" {}

  # State and plans are encrypted before they leave the laptop, with the key from the setup.
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
  }
}
