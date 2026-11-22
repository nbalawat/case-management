# Least privilege for the cloud machine. `infra/bin/cloud.py setup` creates this once per account with an
# administrator's credentials; afterwards `up`, `stop`, `start` and `down` run as the operator role, which can manage
# the machine's network, instance, disk and budget and use the state bucket, but cannot create IAM roles or policies.
# The machine's own instance profile (Session Manager only) is created here, so the operator only needs to pass it.
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account   = data.aws_caller_identity.current.account_id
  partition = data.aws_partition.current.partition
  trusted   = length(var.trusted_principals) > 0 ? var.trusted_principals : ["arn:${local.partition}:iam::${local.account}:root"]
}

# ---- the machine's instance profile: Session Manager as a second way in, and the application's own permissions below
data "aws_iam_policy_document" "ec2_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "machine" {
  name                 = "${var.prefix}-machine"
  assume_role_policy   = data.aws_iam_policy_document.ec2_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "machine_ssm" {
  role       = aws_iam_role.machine.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ---- what the application on the machine may do with the machine's own credentials (python -m app.cloudcheck, and
# KEY_PROVIDER=aws-kms with EVIDENCE_URL=s3://...). Only the data bucket, and only KMS keys the application itself made
# for a client: tagged caseforge:client and named alias/<prefix>-client-*. It cannot touch the state key or any other key.
data "aws_iam_policy_document" "machine_app" {
  #checkov:skip=CKV_AWS_356:kms:CreateKey and kms:ListAliases have no resource to scope to; CreateKey requires the client tag in the request
  #checkov:skip=CKV_AWS_111:CreateKey is limited by the required request tag; every other KMS write is limited to keys carrying the tag
  dynamic "statement" {
    for_each = var.data_bucket_arn == null ? [] : [var.data_bucket_arn]
    content {
      sid       = "DataBucketList"
      actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
      resources = [statement.value]
    }
  }
  dynamic "statement" {
    for_each = var.data_bucket_arn == null ? [] : [var.data_bucket_arn]
    content {
      sid       = "DataBucketObjects"
      actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      resources = ["${statement.value}/*"]
    }
  }
  statement {
    sid       = "ClientKeysCreate"
    actions   = ["kms:CreateKey", "kms:TagResource"]
    resources = ["*"]
    condition {
      test     = "Null"
      variable = "aws:RequestTag/caseforge:client"
      values   = ["false"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = var.regions
    }
  }
  statement {
    sid = "ClientKeysUse"
    actions = [
      "kms:Encrypt", "kms:Decrypt", "kms:DescribeKey", "kms:ListKeyRotations", "kms:ListResourceTags",
      "kms:RotateKeyOnDemand", "kms:EnableKeyRotation", "kms:GetKeyRotationStatus",
      "kms:DisableKey", "kms:ScheduleKeyDeletion", "kms:CreateAlias",
    ]
    resources = [for r in var.regions : "arn:${local.partition}:kms:${r}:${local.account}:key/*"]
    condition {
      test     = "Null"
      variable = "aws:ResourceTag/caseforge:client"
      values   = ["false"]
    }
  }
  statement {
    sid       = "ClientKeyAliases"
    actions   = ["kms:CreateAlias"]
    resources = [for r in var.regions : "arn:${local.partition}:kms:${r}:${local.account}:alias/${var.prefix}-client-*"]
  }
}

resource "aws_iam_role_policy" "machine_app" {
  name   = "application"
  role   = aws_iam_role.machine.id
  policy = data.aws_iam_policy_document.machine_app.json
}

resource "aws_iam_instance_profile" "machine" {
  name = "${var.prefix}-machine"
  role = aws_iam_role.machine.name
}

# ---- the operator
data "aws_iam_policy_document" "operator_trust" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession", "sts:SetSourceIdentity"]
    principals {
      type        = "AWS"
      identifiers = local.trusted
    }
  }
}

resource "aws_iam_role" "operator" {
  name                 = "${var.prefix}-machine-operator"
  description          = "Brings the CaseForge cloud machine up and down (infra/bin/cloud.py)"
  assume_role_policy   = data.aws_iam_policy_document.operator_trust.json
  max_session_duration = 14400
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "operator" {
  #checkov:skip=CKV_AWS_356:EC2 create actions cannot be scoped to resource ARNs that do not exist yet; scoped by region instead
  #checkov:skip=CKV_AWS_111:Same: writes are limited to the allowed regions and to the actions the machine module uses
  statement {
    sid = "MachineNetworkInstanceAndDisk"
    actions = [
      "ec2:Describe*", "ec2:Get*",
      "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:ModifySubnetAttribute",
      "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway", "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
      "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:ReplaceRoute",
      "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress",
      "ec2:ModifySecurityGroupRules", "ec2:UpdateSecurityGroupRuleDescriptionsIngress", "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
      "ec2:RunInstances", "ec2:TerminateInstances", "ec2:StartInstances", "ec2:StopInstances",
      "ec2:ModifyInstanceAttribute", "ec2:ModifyInstanceMetadataOptions",
      "ec2:CreateTags", "ec2:DeleteTags",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = var.regions
    }
  }
  statement {
    sid       = "UbuntuImageId"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [for r in var.regions : "arn:${local.partition}:ssm:${r}::parameter/aws/service/canonical/ubuntu/*"]
  }
  statement {
    sid       = "PassOnlyTheMachineRole"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.machine.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadTheMachineProfile"
    actions   = ["iam:GetInstanceProfile", "iam:GetRole"]
    resources = [aws_iam_instance_profile.machine.arn, aws_iam_role.machine.arn]
  }
  statement {
    sid       = "MachineBudgets"
    actions   = ["budgets:ViewBudget", "budgets:ModifyBudget", "budgets:ListTagsForResource", "budgets:TagResource", "budgets:UntagResource"]
    resources = ["arn:${local.partition}:budgets::${local.account}:budget/${var.prefix}*"]
  }
  statement {
    sid       = "StateBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }
  statement {
    sid       = "MachineState"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/machine/*"]
  }
  statement {
    sid       = "StateEncryption"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [var.state_key_arn]
  }
  statement {
    sid       = "WhoAmI"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "operator" {
  name   = "cloud-machine"
  role   = aws_iam_role.operator.id
  policy = data.aws_iam_policy_document.operator.json
}
