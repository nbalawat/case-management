output "contract" {
  description = "The stamp contract (infra/contract/stamp-outputs.md), consumed by modules/kubernetes/stamp-release."
  value = {
    cloud                       = "azure"
    name                        = var.name
    environment                 = var.environment
    tenancy                     = var.tenancy
    namespace                   = local.namespace
    service_account             = var.service_account
    service_account_annotations = module.identity.service_account_annotations
    pod_labels                  = { "azure.workload.identity/use" = "true" }
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
      credentials = { key = module.postgres.credentials_secret_name, username_property = "username", password_property = "password" }
    }
    event_bus = {
      mode           = var.kafka.mode
      url            = var.kafka.mode == "managed" ? "kafka://${module.kafka[0].bootstrap_servers}" : null
      sasl_mechanism = var.kafka.mode == "managed" ? "PLAIN" : null
      credentials    = var.kafka.mode == "managed" ? { key = module.kafka[0].credentials_secret_name, username_property = "username", password_property = "password" } : null
    }
    search = {
      mode        = var.search.mode
      url         = null
      credentials = null
    }
    secret_store = {
      azurekv = {
        vaultUrl          = module.kms.key_vault_uri
        authType          = "WorkloadIdentity"
        serviceAccountRef = { name = var.service_account }
      }
    }
    extra_env = { AZURE_STORAGE_ACCOUNT_NAME = module.object_store.account_name }
  }
}
