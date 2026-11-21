output "contract" {
  description = "The stamp contract (infra/contract/stamp-outputs.md), consumed by modules/kubernetes/stamp-release."
  value = {
    cloud                       = "aws"
    name                        = var.name
    environment                 = var.environment
    tenancy                     = var.tenancy
    namespace                   = local.namespace
    service_account             = var.service_account
    service_account_annotations = module.identity.service_account_annotations
    pod_labels                  = {}
    storage = {
      landing_url    = module.object_store.urls["landing"]
      evidence_url   = module.object_store.urls["evidence"]
      quarantine_url = module.object_store.urls["quarantine"]
    }
    database = {
      host        = module.postgres.host
      port        = module.postgres.port
      name        = module.postgres.database
      sslmode     = "require"
      credentials = { key = module.postgres.credentials_secret_arn, username_property = "username", password_property = "password" }
    }
    event_bus = {
      mode           = var.kafka.mode
      url            = var.kafka.mode == "managed" ? "kafka://${module.kafka[0].bootstrap_brokers}" : null
      sasl_mechanism = var.kafka.mode == "managed" ? "SCRAM-SHA-512" : null
      credentials    = var.kafka.mode == "managed" ? { key = module.kafka[0].credentials_secret_arn, username_property = "username", password_property = "password" } : null
    }
    search = {
      mode        = var.search.mode
      url         = var.search.mode == "managed" ? module.search[0].url : null
      credentials = var.search.mode == "managed" ? { key = module.search[0].credentials_secret_arn, username_property = "username", password_property = "password" } : null
    }
    secret_store = {
      aws = {
        service = "SecretsManager"
        region  = data.aws_region.current.region
        auth    = { jwt = { serviceAccountRef = { name = var.service_account } } }
      }
    }
    extra_env = { AWS_REGION = data.aws_region.current.region }
  }
}
