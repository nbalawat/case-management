output "contract" {
  description = "The stamp contract (infra/contract/stamp-outputs.md), consumed by modules/kubernetes/stamp-release."
  value = {
    cloud                       = "gcp"
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
      credentials = { key = module.postgres.credentials_secret_id, username_property = "username", password_property = "password" }
    }
    event_bus = {
      mode           = var.kafka.mode
      url            = null
      sasl_mechanism = null
      credentials    = null
    }
    search = {
      mode        = var.search.mode
      url         = null
      credentials = null
    }
    secret_store = {
      gcpsm = {
        projectID = local.project
        auth = {
          workloadIdentity = {
            clusterLocation   = local.p.cluster.location
            clusterName       = local.p.cluster.name
            clusterProjectID  = local.project
            serviceAccountRef = { name = var.service_account }
          }
        }
      }
    }
    extra_env = { GOOGLE_CLOUD_PROJECT = local.project }
  }
}
