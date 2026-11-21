# Plans a stamp release against mocked Kubernetes and Helm providers: no cluster needed. The contract is the shape
# every cloud's stamp module outputs (here an AWS one with in-cluster Kafka and OpenSearch).
mock_provider "kubernetes" {}
mock_provider "helm" {}

variables {
  contract = {
    cloud                       = "aws"
    name                        = "acme"
    environment                 = "dev"
    tenancy                     = "dedicated"
    namespace                   = "cf-acme"
    service_account             = "caseforge"
    service_account_annotations = { "eks.amazonaws.com/role-arn" = "arn:aws:iam::123456789012:role/cf-dev-acme-app" }
    pod_labels                  = {}
    storage = {
      landing_url    = "s3://landing/caseforge"
      evidence_url   = "s3://evidence/caseforge"
      quarantine_url = "s3://quarantine/caseforge"
    }
    database = {
      host        = "db.internal"
      port        = 5432
      name        = "caseforge"
      sslmode     = "require"
      credentials = { key = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:db", username_property = "username", password_property = "password" }
    }
    event_bus    = { mode = "in_cluster", url = null, sasl_mechanism = null, credentials = null }
    search       = { mode = "in_cluster", url = null, credentials = null }
    secret_store = { aws = { service = "SecretsManager", region = "eu-west-2", auth = { jwt = { serviceAccountRef = { name = "caseforge" } } } } }
    extra_env    = { AWS_REGION = "eu-west-2" }
  }
  release = {
    mode             = "dedicated"
    host             = "acme.dev.caseforge.example.com"
    operator_issuer  = "https://idp.example.com/realms/ops"
    image_repository = "ghcr.io/nbalawat/caseforge"
  }
}

run "in_cluster_services" {
  command = plan

  assert {
    condition     = kubernetes_namespace_v1.this.metadata[0].labels["pod-security.kubernetes.io/audit"] == "restricted"
    error_message = "The namespace audits against the restricted pod security standard."
  }
  assert {
    condition     = contains(one(one(kubernetes_network_policy_v1.allow.spec).egress[2].to).ip_block[0].except, "169.254.169.254/32")
    error_message = "Pods can never reach the node metadata service."
  }
  assert {
    condition     = startswith(kubernetes_manifest.app_secret.manifest.spec.target.template.data.DATABASE_URL, "postgresql+psycopg://{{ .db_username | urlquery }}:{{ .db_password | urlquery }}@db.internal:5432/caseforge?sslmode=require")
    error_message = "DATABASE_URL is assembled in the cluster, with the password URL-encoded and TLS required."
  }
  assert {
    condition     = strcontains(kubernetes_manifest.app_secret.manifest.spec.target.template.data.KAFKA_CONFIG, "SCRAM-SHA-512")
    error_message = "In-cluster Kafka is reached with SCRAM over TLS."
  }
  assert {
    condition     = strcontains(kubernetes_manifest.app_secret.manifest.spec.target.template.data.SEARCH_CONFIG, "verify_certs\" true")
    error_message = "In-cluster search verifies the server certificate."
  }
  assert {
    condition     = local.app_values.eventBus.url == "kafka://kafka-kafka-bootstrap.cf-acme.svc:9093"
    error_message = "The application is pointed at the stamp's own Kafka."
  }
  assert {
    condition     = local.app_values.serviceAccount.create == false && local.app_values.existingSecret == "caseforge-secrets"
    error_message = "The chart uses the service account and Secret made here."
  }
  assert {
    condition     = kubernetes_manifest.project[0].manifest.spec.destinations[0].namespace == "cf-acme"
    error_message = "The stamp's Argo CD project can deploy only into its own namespace."
  }
  assert {
    condition     = length(kubernetes_manifest.data) == 1 && length(helm_release.app) == 0
    error_message = "Argo CD deploys the data services and the application."
  }
}

run "managed_services_no_in_cluster_data" {
  command = plan

  variables {
    contract = {
      cloud                       = "aws"
      name                        = "acme"
      environment                 = "dev"
      tenancy                     = "dedicated"
      namespace                   = "cf-acme"
      service_account             = "caseforge"
      service_account_annotations = { "eks.amazonaws.com/role-arn" = "arn:aws:iam::123456789012:role/cf-dev-acme-app" }
      pod_labels                  = {}
      storage = {
        landing_url    = "s3://landing/caseforge"
        evidence_url   = "s3://evidence/caseforge"
        quarantine_url = "s3://quarantine/caseforge"
      }
      database = {
        host        = "db.internal"
        port        = 5432
        name        = "caseforge"
        sslmode     = "require"
        credentials = { key = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:db", username_property = "username", password_property = "password" }
      }
      event_bus    = { mode = "managed", url = "kafka://b-1.msk:9096", sasl_mechanism = "SCRAM-SHA-512", credentials = { key = "msk", username_property = "username", password_property = "password" } }
      search       = { mode = "none", url = null, credentials = null }
      secret_store = { aws = { service = "SecretsManager", region = "eu-west-2", auth = { jwt = { serviceAccountRef = { name = "caseforge" } } } } }
      extra_env    = { AWS_REGION = "eu-west-2" }
    }
  }

  assert {
    condition     = length(kubernetes_manifest.data) == 0 && length(kubernetes_manifest.cluster_store) == 0
    error_message = "No in-cluster data services or in-cluster secret reader when nothing runs in the cluster."
  }
  assert {
    condition     = kubernetes_namespace_v1.this.metadata[0].labels["pod-security.kubernetes.io/enforce"] == "restricted"
    error_message = "With no data services in the namespace, restricted is enforced."
  }
  assert {
    condition     = local.app_values.eventBus.url == "kafka://b-1.msk:9096"
    error_message = "The application is pointed at the managed broker."
  }
}

run "helm_delivery_needs_a_tag" {
  command = plan
  variables {
    release = {
      delivery         = "helm"
      mode             = "dedicated"
      host             = "acme.dev.caseforge.example.com"
      operator_issuer  = "https://idp.example.com/realms/ops"
      image_repository = "ghcr.io/nbalawat/caseforge"
    }
  }
  expect_failures = [var.release]
}
