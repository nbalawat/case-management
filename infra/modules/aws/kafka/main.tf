# Optional managed Kafka (Amazon MSK) for a stamp. TLS everywhere, SASL/SCRAM only (no unauthenticated clients),
# encrypted with the stamp's key, brokers in the data subnets reachable only from the cluster. The application's
# credentials are a SCRAM secret in Secrets Manager; the stamp release turns them into KAFKA_CONFIG.
locals {
  broker_type = {
    small  = "kafka.m7g.large"
    medium = "kafka.m7g.xlarge"
    large  = "kafka.m7g.4xlarge"
  }[var.size]
}

resource "aws_security_group" "this" {
  name_prefix = "${var.name}-msk-"
  description = "MSK for ${var.name}: SASL/SCRAM over TLS from the cluster only"
  vpc_id      = var.vpc_id
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "scram" {
  security_group_id            = aws_security_group.this.id
  description                  = "Kafka SASL/SCRAM over TLS from cluster nodes"
  referenced_security_group_id = var.client_security_group_id
  from_port                    = 9096
  to_port                      = 9096
  ip_protocol                  = "tcp"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/caseforge/${var.name}/msk"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
  tags              = var.tags
}

resource "aws_msk_configuration" "this" {
  name              = "${var.name}-caseforge"
  kafka_versions    = [var.kafka_version]
  server_properties = <<-PROPS
    auto.create.topics.enable=false
    default.replication.factor=3
    min.insync.replicas=2
    unclean.leader.election.enable=false
  PROPS
}

resource "aws_msk_cluster" "this" {
  cluster_name           = var.name
  kafka_version          = var.kafka_version
  number_of_broker_nodes = length(var.subnet_ids)

  broker_node_group_info {
    instance_type   = local.broker_type
    client_subnets  = var.subnet_ids
    security_groups = [aws_security_group.this.id]
    storage_info {
      ebs_storage_info {
        volume_size = var.storage_gb
      }
    }
    connectivity_info {
      public_access {
        type = "DISABLED"
      }
    }
  }

  client_authentication {
    unauthenticated = false
    sasl {
      scram = true
      iam   = false
    }
  }

  encryption_info {
    encryption_at_rest_kms_key_arn = var.kms_key_arn
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this.arn
    revision = aws_msk_configuration.this.latest_revision
  }

  logging_info {
    broker_logs {
      cloudwatch_logs {
        enabled   = true
        log_group = aws_cloudwatch_log_group.this.name
      }
    }
  }

  enhanced_monitoring = "PER_TOPIC_PER_BROKER"
  tags                = var.tags
}

# The application's SCRAM user. MSK requires the secret name to start with AmazonMSK_ and a customer managed key.
# Ephemeral: the password is generated, written once to Secrets Manager, and never stored in state.
ephemeral "random_password" "scram" {
  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "scram" {
  #checkov:skip=CKV2_AWS_57:Rotated by raising credentials_version (MSK SCRAM has no managed rotation)
  name_prefix = "AmazonMSK_${var.name}_"
  kms_key_id  = var.kms_key_arn
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "scram" {
  secret_id                = aws_secretsmanager_secret.scram.id
  secret_string_wo         = jsonencode({ username = "caseforge", password = ephemeral.random_password.scram.result })
  secret_string_wo_version = var.credentials_version
}

resource "aws_msk_single_scram_secret_association" "this" {
  cluster_arn = aws_msk_cluster.this.arn
  secret_arn  = aws_secretsmanager_secret.scram.arn
  depends_on  = [aws_secretsmanager_secret_version.scram]
}
