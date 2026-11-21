# Run once per AWS account, with local state, by an administrator: the encrypted, versioned state bucket, the key that
# encrypts state client-side (OpenTofu state encryption), and the roles GitHub Actions assumes through OIDC to plan
# (read-only) and apply (per environment). Afterwards everything else runs from CI with no long-lived keys.
terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { "caseforge/managed-by" = "opentofu", "caseforge/stack" = "bootstrap" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

module "state_key" {
  source      = "../../../modules/aws/kms"
  name        = "${var.prefix}-tofu-state"
  description = "OpenTofu state: bucket encryption and client-side state encryption"
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_18:State access is recorded by CloudTrail data events; enable them on the account
  #checkov:skip=CKV_AWS_144:State is versioned; cross-region replication is optional per account
  #checkov:skip=CKV2_AWS_62:No consumers of state bucket events
  bucket = "${var.prefix}-tofu-state-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = module.state_key.key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  rule {
    id     = "keep-old-state-versions-a-year"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 365
    }
  }
}

data "aws_iam_policy_document" "state_tls" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
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
}

resource "aws_s3_bucket_policy" "state" {
  bucket     = aws_s3_bucket.state.id
  policy     = data.aws_iam_policy_document.state_tls.json
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# ---- GitHub Actions through OIDC
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  roles = merge(
    # Plans run read-only on pull requests and on main (the plan an apply then applies); applies only in a protected
    # GitHub environment.
    { plan = { subjects = ["repo:${var.github_repository}:pull_request", "repo:${var.github_repository}:ref:refs/heads/main"], admin = false } },
    { for env in var.environments : "apply-${env}" => { subjects = ["repo:${var.github_repository}:environment:${env}"], admin = true } },
  )
}

data "aws_iam_policy_document" "github_trust" {
  for_each = local.roles
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value.subjects
    }
  }
}

resource "aws_iam_role" "github" {
  for_each             = local.roles
  name                 = "${var.prefix}-github-${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.github_trust[each.key].json
  max_session_duration = 3600
  permissions_boundary = var.permissions_boundary_arn
}

# Planning reads everything and may use state, but changes nothing.
resource "aws_iam_role_policy_attachment" "plan_read" {
  role       = aws_iam_role.github["plan"].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "state_access" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
  }
  statement {
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [module.state_key.key_arn]
  }
}

resource "aws_iam_role_policy" "state_access" {
  for_each = local.roles
  name     = "tofu-state"
  role     = aws_iam_role.github[each.key].id
  policy   = data.aws_iam_policy_document.state_access.json
}

# Applying creates what the stacks declare. Scope it down with a permissions boundary (var.permissions_boundary_arn)
# and, in AWS Organizations, service control policies; the environment's GitHub protection rules gate who can run it.
resource "aws_iam_role_policy_attachment" "apply" {
  for_each   = { for k, v in local.roles : k => v if v.admin }
  role       = aws_iam_role.github[each.key].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "apply_iam" {
  statement {
    sid = "ManageCaseforgeRolesOnly"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:TagRole", "iam:UntagRole", "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PassRole",
      "iam:CreateOpenIDConnectProvider", "iam:DeleteOpenIDConnectProvider", "iam:TagOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint", "iam:CreateServiceLinkedRole",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/cf-*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/*",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/*",
    ]
  }
}

resource "aws_iam_role_policy" "apply_iam" {
  for_each = { for k, v in local.roles : k => v if v.admin }
  name     = "caseforge-iam"
  role     = aws_iam_role.github[each.key].id
  policy   = data.aws_iam_policy_document.apply_iam.json
}
