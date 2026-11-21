# Optional managed OpenSearch for a stamp: in the data subnets, HTTPS only on TLS 1.2+, encrypted at rest with the
# stamp's key and between nodes, fine-grained access control with the master user's password in Secrets Manager.
locals {
  instance_type = {
    small  = "m7g.large.search"
    medium = "r7g.xlarge.search"
    large  = "r7g.2xlarge.search"
  }[var.size]
  zones = min(length(var.subnet_ids), 3)
}

resource "aws_security_group" "this" {
  name_prefix = "${var.name}-search-"
  description = "OpenSearch for ${var.name}: HTTPS from the cluster only"
  vpc_id      = var.vpc_id
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  security_group_id            = aws_security_group.this.id
  description                  = "HTTPS from cluster nodes"
  referenced_security_group_id = var.client_security_group_id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

resource "random_password" "master" {
  length           = 32
  min_upper        = 1
  min_lower        = 1
  min_numeric      = 1
  min_special      = 1
  override_special = "!#%*-_=+"
}

resource "aws_secretsmanager_secret" "master" {
  #checkov:skip=CKV2_AWS_57:The master password is set on the domain by OpenTofu; rotate by replacing random_password.master
  name_prefix = "${var.name}-search-"
  kms_key_id  = var.kms_key_arn
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "master" {
  secret_id     = aws_secretsmanager_secret.master.id
  secret_string = jsonencode({ username = "caseforge", password = random_password.master.result })
}

resource "aws_cloudwatch_log_group" "this" {
  for_each          = toset(["application", "audit"])
  name              = "/caseforge/${var.name}/opensearch/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

data "aws_iam_policy_document" "logs" {
  statement {
    actions   = ["logs:PutLogEvents", "logs:CreateLogStream"]
    resources = [for g in aws_cloudwatch_log_group.this : "${g.arn}:*"]
    principals {
      type        = "Service"
      identifiers = ["es.amazonaws.com"]
    }
  }
}

resource "aws_cloudwatch_log_resource_policy" "this" {
  policy_name     = "${var.name}-opensearch"
  policy_document = data.aws_iam_policy_document.logs.json
}

resource "aws_opensearch_domain" "this" {
  domain_name    = var.name
  engine_version = var.engine_version

  #checkov:skip=CKV_AWS_317:Audit logs are published (log_publishing_options AUDIT_LOGS, a dynamic block)
  #checkov:skip=CKV_AWS_318:Dedicated masters are on for medium and large; small stamps run without them
  #checkov:skip=CKV2_AWS_59:Dedicated masters are on for medium and large; small stamps run without them
  cluster_config {
    dedicated_master_enabled = var.size != "small"
    dedicated_master_count   = var.size != "small" ? 3 : null
    dedicated_master_type    = var.size != "small" ? "m7g.large.search" : null
    instance_type            = local.instance_type
    instance_count           = local.zones
    zone_awareness_enabled   = local.zones > 1
    dynamic "zone_awareness_config" {
      for_each = local.zones > 1 ? [1] : []
      content {
        availability_zone_count = local.zones
      }
    }
  }

  ebs_options {
    ebs_enabled = true
    volume_type = "gp3"
    volume_size = var.storage_gb
  }

  vpc_options {
    subnet_ids         = slice(var.subnet_ids, 0, local.zones)
    security_group_ids = [aws_security_group.this.id]
  }

  encrypt_at_rest {
    enabled    = true
    kms_key_id = var.kms_key_arn
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https       = true
    tls_security_policy = "Policy-Min-TLS-1-2-PFS-2023-10"
  }

  advanced_security_options {
    enabled                        = true
    internal_user_database_enabled = true
    master_user_options {
      master_user_name     = "caseforge"
      master_user_password = random_password.master.result
    }
  }

  dynamic "log_publishing_options" {
    for_each = { INDEX_SLOW_LOGS = "application", ES_APPLICATION_LOGS = "application", AUDIT_LOGS = "audit" }
    content {
      log_type                 = log_publishing_options.key
      cloudwatch_log_group_arn = aws_cloudwatch_log_group.this[log_publishing_options.value].arn
    }
  }

  software_update_options {
    auto_software_update_enabled = true
  }

  tags       = var.tags
  depends_on = [aws_cloudwatch_log_resource_policy.this]
}
