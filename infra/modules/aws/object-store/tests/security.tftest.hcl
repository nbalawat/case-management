mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
}

variables {
  name        = "cf-dev-acme"
  kms_key_arn = "arn:aws:kms:eu-west-2:123456789012:key/mock"
}

run "private_encrypted_versioned_worm" {
  command = plan

  assert {
    condition = alltrue([for b in aws_s3_bucket_public_access_block.this :
    b.block_public_acls && b.block_public_policy && b.ignore_public_acls && b.restrict_public_buckets])
    error_message = "Every bucket blocks public access."
  }
  assert {
    condition = alltrue([for e in aws_s3_bucket_server_side_encryption_configuration.this :
    one(e.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms"])
    error_message = "Every data bucket is encrypted with the stamp's key."
  }
  assert {
    condition     = alltrue([for v in aws_s3_bucket_versioning.this : v.versioning_configuration[0].status == "Enabled"])
    error_message = "Every bucket is versioned."
  }
  assert {
    condition     = aws_s3_bucket.this["evidence"].object_lock_enabled && aws_s3_bucket_object_lock_configuration.evidence[0].rule[0].default_retention[0].mode == "COMPLIANCE"
    error_message = "Evidence is write-once in compliance mode."
  }
}

run "worm_can_be_off_for_dev" {
  command = plan
  variables {
    evidence_worm = false
  }
  assert {
    condition     = length(aws_s3_bucket_object_lock_configuration.evidence) == 0
    error_message = "No object lock when worm is off."
  }
}
