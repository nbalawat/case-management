# One IAM role per stamp, assumable only by that stamp's Kubernetes service account through IRSA, allowed only what
# the application does: read landing files, read and write evidence and quarantine, read its own secrets, and use its
# own key. Nothing else in the account.
locals {
  subject = "system:serviceaccount:${var.namespace}:${var.service_account}"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:sub"
      values   = [local.subject]
    }
    condition {
      test     = "StringEquals"
      variable = "${var.oidc_issuer}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name_prefix          = "${var.name}-app-"
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "this" {
  statement {
    sid       = "ListOwnBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = values(var.bucket_arns)
  }
  statement {
    sid       = "ReadLanding"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${var.bucket_arns["landing"]}/*"]
  }
  statement {
    sid       = "ReadWriteEvidenceAndQuarantine"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${var.bucket_arns["evidence"]}/*", "${var.bucket_arns["quarantine"]}/*"]
  }
  statement {
    sid       = "MoveOutOfQuarantine"
    actions   = ["s3:DeleteObject"]
    resources = ["${var.bucket_arns["quarantine"]}/*", "${var.bucket_arns["landing"]}/*"]
  }
  statement {
    sid       = "ReadOwnSecrets"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = var.secret_arns
  }
  statement {
    sid       = "UseOwnKey"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "caseforge-app"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.this.json
}
