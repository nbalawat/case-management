# The stamp's buckets: landing (data onboarding), evidence (write once when worm is on) and quarantine. All private,
# encrypted with the stamp's key, versioned, TLS only, with access logs in a separate bucket.
locals {
  buckets = toset(["landing", "evidence", "quarantine"])
}

resource "aws_s3_bucket" "logs" {
  #checkov:skip=CKV_AWS_18:This is the access-log bucket; logging it into itself would loop
  #checkov:skip=CKV_AWS_145:S3 server access logging can deliver only to SSE-S3 buckets
  #checkov:skip=CKV_AWS_144:Access logs are not replicated across regions
  #checkov:skip=CKV2_AWS_62:No consumers of access-log events
  #checkov:skip=CKV2_AWS_6:Public access block is aws_s3_bucket_public_access_block.this["logs"]
  #checkov:skip=CKV_AWS_21:Versioning is aws_s3_bucket_versioning.this["logs"]
  #checkov:skip=CKV2_AWS_61:Lifecycle is aws_s3_bucket_lifecycle_configuration.this["logs"]
  bucket_prefix = "${var.name}-access-logs-"
  force_destroy = false
  tags          = var.tags
}

resource "aws_s3_bucket" "this" {
  #checkov:skip=CKV_AWS_144:Cross-region replication is a disaster-recovery decision per client; see infra/README.md
  #checkov:skip=CKV2_AWS_62:The application lists landing files itself; no event consumers yet
  #checkov:skip=CKV2_AWS_6:Public access block is aws_s3_bucket_public_access_block.this (for_each)
  #checkov:skip=CKV_AWS_21:Versioning is aws_s3_bucket_versioning.this (for_each)
  #checkov:skip=CKV2_AWS_61:Lifecycle is aws_s3_bucket_lifecycle_configuration.this (for_each)
  #checkov:skip=CKV_AWS_18:Access logging is aws_s3_bucket_logging.this (for_each)
  #checkov:skip=CKV_AWS_145:SSE-KMS is aws_s3_bucket_server_side_encryption_configuration.this (for_each)
  for_each            = local.buckets
  bucket_prefix       = "${var.name}-${each.key}-"
  force_destroy       = false
  object_lock_enabled = each.key == "evidence" && var.evidence_worm
  tags                = merge(var.tags, { purpose = each.key })
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

# Server access logging can only deliver to SSE-S3 buckets.
resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_logging" "this" {
  for_each      = aws_s3_bucket.this
  bucket        = each.value.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "${each.key}/"
}

resource "aws_s3_bucket_object_lock_configuration" "evidence" {
  count  = var.evidence_worm ? 1 : 0
  bucket = aws_s3_bucket.this["evidence"].id
  rule {
    default_retention {
      mode = "COMPLIANCE"
      days = var.evidence_retention_days
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  bucket   = each.value.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = each.key == "evidence" ? var.evidence_retention_days : 90
    }
  }

  dynamic "rule" {
    for_each = each.key == "landing" ? [1] : []
    content {
      id     = "expire-landed-files"
      status = "Enabled"
      filter {}
      expiration {
        days = var.landing_expiry_days
      }
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  for_each = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [each.value.arn, "${each.value.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
  dynamic "statement" {
    for_each = each.key == "logs" ? [1] : []
    content {
      sid       = "S3ServerAccessLogs"
      actions   = ["s3:PutObject"]
      resources = ["${each.value.arn}/*"]
      principals {
        type        = "Service"
        identifiers = ["logging.s3.amazonaws.com"]
      }
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  for_each   = merge(aws_s3_bucket.this, { logs = aws_s3_bucket.logs })
  bucket     = each.value.id
  policy     = data.aws_iam_policy_document.tls_only[each.key].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}
