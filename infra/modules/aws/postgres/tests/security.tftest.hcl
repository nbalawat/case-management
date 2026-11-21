mock_provider "aws" {
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}

variables {
  name                     = "cf-dev-acme"
  vpc_id                   = "vpc-123"
  subnet_ids               = ["subnet-a", "subnet-b"]
  client_security_group_id = "sg-nodes"
  kms_key_arn              = "arn:aws:kms:eu-west-2:123456789012:key/mock"
}

override_resource {
  target = aws_db_instance.this
  values = {
    master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:rds-mock", kms_key_id = "k", secret_status = "active" }]
  }
}

run "private_encrypted_tls_only" {
  command = plan

  assert {
    condition     = aws_db_instance.this.publicly_accessible == false
    error_message = "The database is never publicly accessible."
  }
  assert {
    condition     = aws_db_instance.this.storage_encrypted && aws_db_instance.this.kms_key_id == var.kms_key_arn
    error_message = "Storage is encrypted with the stamp's key."
  }
  assert {
    condition     = aws_db_instance.this.manage_master_user_password == true
    error_message = "RDS generates and rotates the password; it is never in state."
  }
  assert {
    condition     = one([for p in aws_db_parameter_group.this.parameter : p.value if p.name == "rds.force_ssl"]) == "1"
    error_message = "TLS is enforced."
  }
  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot
    error_message = "Deletion is protected and leaves a final snapshot."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.from_cluster.referenced_security_group_id == var.client_security_group_id
    error_message = "Only the cluster may connect."
  }
}

run "sizes_are_cloud_neutral" {
  command = plan
  variables {
    size = "huge"
  }
  expect_failures = [var.size]
}
