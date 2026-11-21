# Plans and applies the AWS stamp against mocked providers: no account, no credentials, nothing created. Proves the
# module wires together, satisfies the stamp contract, and keeps its security settings.
mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_region" {
    defaults = { region = "eu-west-2" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-west-2:123456789012:key/mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock-bucket", bucket = "mock-bucket" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address            = "mock.eu-west-2.rds.amazonaws.com"
      port               = 5432
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:rds-mock", kms_key_id = "k", secret_status = "active" }]
    }
  }
  mock_resource "aws_msk_configuration" {
    defaults = { arn = "arn:aws:kafka:eu-west-2:123456789012:configuration/mock/1" }
  }
  mock_resource "aws_msk_cluster" {
    defaults = { arn = "arn:aws:kafka:eu-west-2:123456789012:cluster/mock/1", bootstrap_brokers_sasl_scram = "b-1.mock:9096,b-2.mock:9096" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-west-2:123456789012:log-group:mock" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:mock" }
  }
  mock_resource "aws_opensearch_domain" {
    defaults = { arn = "arn:aws:es:eu-west-2:123456789012:domain/mock", endpoint = "vpc-mock.eu-west-2.es.amazonaws.com" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/cf-dev-acme-app-mock" }
  }
}


variables {
  name        = "acme"
  environment = "dev"
  tenancy     = "dedicated"
  platform = {
    cloud = "aws"
    network = {
      vpc_id          = "vpc-123"
      data_subnet_ids = ["subnet-a", "subnet-b", "subnet-c"]
    }
    cluster = {
      oidc_provider_arn      = "arn:aws:iam::123456789012:oidc-provider/oidc.eks.eu-west-2.amazonaws.com/id/MOCK"
      oidc_issuer            = "oidc.eks.eu-west-2.amazonaws.com/id/MOCK"
      node_security_group_id = "sg-nodes"
    }
  }
}

run "dedicated_stamp_in_cluster_services" {
  command = apply

  assert {
    condition     = output.contract.cloud == "aws" && output.contract.namespace == "cf-acme"
    error_message = "The contract names the cloud and defaults the namespace to cf-<name>."
  }
  assert {
    condition     = alltrue([for u in values(output.contract.storage) : startswith(u, "s3://")])
    error_message = "Storage URLs use the s3:// scheme the application reads."
  }
  assert {
    condition     = output.contract.database.sslmode == "require" && output.contract.database.credentials.key != ""
    error_message = "The database requires TLS and its credentials are a secret reference."
  }
  assert {
    condition     = output.contract.event_bus.mode == "in_cluster" && output.contract.event_bus.url == null
    error_message = "Kafka defaults to in-cluster, with no managed address."
  }
  assert {
    condition     = output.contract.service_account_annotations["eks.amazonaws.com/role-arn"] != ""
    error_message = "The service account is bound to an IAM role through IRSA."
  }
  assert {
    condition     = length(module.kafka) == 0 && length(module.search) == 0
    error_message = "No managed Kafka or OpenSearch unless asked for."
  }
}

run "managed_kafka_and_search_when_asked" {
  # plan: write-only secrets from ephemeral passwords cannot be applied against mocks
  command = plan

  variables {
    kafka  = { mode = "managed" }
    search = { mode = "managed" }
  }

  assert {
    condition     = startswith(output.contract.event_bus.url, "kafka://") && output.contract.event_bus.sasl_mechanism == "SCRAM-SHA-512"
    error_message = "Managed Kafka is MSK over SASL/SCRAM."
  }
  assert {
    condition     = startswith(output.contract.search.url, "opensearchs://")
    error_message = "Managed search is reached over TLS."
  }
}

run "names_are_checked" {
  command = plan

  variables {
    name = "Acme_Bank"
  }

  expect_failures = [var.name]
}
