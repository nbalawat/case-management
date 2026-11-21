# One stamp inside the cluster, the same on every cloud. From the stamp contract (the cloud stamp module's output) it
# creates the namespace with quotas and default-deny network policy, the service account bound to the stamp's cloud
# identity, the External Secrets wiring that assembles DATABASE_URL and the data service settings from the cloud's
# secret manager without the values ever passing through OpenTofu, and the Argo CD project and applications that
# deploy the in-cluster data services and the application chart from git.
locals {
  c                 = var.contract
  namespace         = local.c.namespace
  in_cluster_kafka  = local.c.event_bus.mode == "in_cluster"
  in_cluster_search = local.c.search.mode == "in_cluster"
  any_in_cluster    = local.in_cluster_kafka || local.in_cluster_search
  labels = {
    "app.kubernetes.io/part-of" = "caseforge"
    "caseforge.io/stamp"        = local.c.name
    "caseforge.io/environment"  = local.c.environment
    "caseforge.io/tenancy"      = local.c.tenancy
  }
  size = local.c.tenancy == "pooled" ? var.quota.pooled : var.quota.dedicated

  kafka_bootstrap = "kafka-kafka-bootstrap.${local.namespace}.svc:9093"
  search_host     = "search.${local.namespace}.svc"
  search_ca_dir   = "/etc/caseforge/search-ca"
  secret_name     = "caseforge-secrets"
}

# ---- Namespace, quotas, network policy
resource "kubernetes_namespace_v1" "this" {
  metadata {
    name = local.namespace
    labels = merge(local.labels, {
      # The application meets "restricted"; operator-managed data pods are held to "baseline" by policy.
      "pod-security.kubernetes.io/enforce" = local.any_in_cluster ? "baseline" : "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
      "pod-security.kubernetes.io/warn"    = "restricted"
    })
  }
}

resource "kubernetes_resource_quota_v1" "this" {
  metadata {
    name      = "stamp"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    hard = {
      "requests.cpu"           = local.size.cpu
      "requests.memory"        = local.size.memory
      "limits.memory"          = local.size.memory_limit
      "persistentvolumeclaims" = 20
      "requests.storage"       = local.size.storage
      "services.loadbalancers" = 0
      "services.nodeports"     = 0
    }
  }
}

resource "kubernetes_limit_range_v1" "this" {
  metadata {
    name      = "defaults"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    limit {
      type            = "Container"
      default_request = { cpu = "100m", memory = "128Mi" }
      default         = { memory = "512Mi" }
    }
  }
}

resource "kubernetes_network_policy_v1" "default_deny" {
  metadata {
    name      = "default-deny"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy_v1" "allow" {
  metadata {
    name      = "allow-stamp-traffic"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]

    # Pods of the stamp talk to each other; the ingress controller reaches the API; monitoring scrapes.
    ingress {
      from {
        pod_selector {}
      }
    }
    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = var.ingress_namespace }
        }
      }
      ports {
        port     = "8000"
        protocol = "TCP"
      }
    }
    dynamic "ingress" {
      for_each = var.operator_namespaces
      content {
        from {
          namespace_selector {
            match_labels = { "kubernetes.io/metadata.name" = ingress.value }
          }
        }
      }
    }

    egress {
      to {
        pod_selector {}
      }
    }
    # DNS
    egress {
      to {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "kube-system" }
        }
      }
      ports {
        port     = "53"
        protocol = "UDP"
      }
      ports {
        port     = "53"
        protocol = "TCP"
      }
    }
    # Managed data services, cloud APIs and the model provider, over TLS. Never the node metadata service.
    egress {
      to {
        ip_block {
          cidr   = "0.0.0.0/0"
          except = ["169.254.169.254/32", "169.254.170.2/32", "100.100.100.200/32"]
        }
      }
      ports {
        port     = "443"
        protocol = "TCP"
      }
      ports {
        port     = "5432"
        protocol = "TCP"
      }
      dynamic "ports" {
        for_each = toset(var.extra_egress_ports)
        content {
          port     = tostring(ports.value)
          protocol = "TCP"
        }
      }
    }
  }
}

# ---- Identity
resource "kubernetes_service_account_v1" "app" {
  metadata {
    name        = local.c.service_account
    namespace   = kubernetes_namespace_v1.this.metadata[0].name
    labels      = local.labels
    annotations = local.c.service_account_annotations
  }
  automount_service_account_token = true # the cloud identity webhook projects a token from it
}

# A reader that may get only the in-cluster Secrets the application needs (Kafka user and CA, search user).
resource "kubernetes_service_account_v1" "reader" {
  count = local.any_in_cluster ? 1 : 0
  metadata {
    name      = "secret-reader"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    labels    = local.labels
  }
  automount_service_account_token = false
}

resource "kubernetes_role_v1" "reader" {
  count = local.any_in_cluster ? 1 : 0
  metadata {
    name      = "secret-reader"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  rule {
    api_groups     = [""]
    resources      = ["secrets"]
    verbs          = ["get"]
    resource_names = concat(local.in_cluster_kafka ? ["caseforge", "kafka-cluster-ca-cert"] : [], local.in_cluster_search ? ["search-user"] : [])
  }
  rule {
    api_groups = ["authorization.k8s.io"]
    resources  = ["selfsubjectrulesreviews"]
    verbs      = ["create"]
  }
}

resource "kubernetes_role_binding_v1" "reader" {
  count = local.any_in_cluster ? 1 : 0
  metadata {
    name      = "secret-reader"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.reader[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.reader[0].metadata[0].name
    namespace = local.namespace
  }
}

# ---- Secrets, assembled by External Secrets inside the cluster
resource "kubernetes_manifest" "cloud_store" {
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "SecretStore"
    metadata   = { name = "cloud", namespace = local.namespace, labels = local.labels }
    spec       = { provider = local.c.secret_store }
  }
  depends_on = [kubernetes_service_account_v1.app]
}

resource "kubernetes_manifest" "cluster_store" {
  count = local.any_in_cluster ? 1 : 0
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "SecretStore"
    metadata   = { name = "in-cluster", namespace = local.namespace, labels = local.labels }
    spec = {
      provider = {
        kubernetes = {
          remoteNamespace = local.namespace
          server          = { caProvider = { type = "ConfigMap", name = "kube-root-ca.crt", key = "ca.crt" } }
          auth            = { serviceAccount = { name = kubernetes_service_account_v1.reader[0].metadata[0].name } }
        }
      }
    }
  }
}

locals {
  cloud_ref    = { name = "cloud", kind = "SecretStore" }
  cluster_ref  = { name = "in-cluster", kind = "SecretStore" }
  db           = local.c.database
  bus_creds    = try(local.c.event_bus.credentials, null)
  search_creds = try(local.c.search.credentials, null)

  secret_data = concat(
    [
      { secretKey = "db_username", remoteRef = { key = local.db.credentials.key, property = local.db.credentials.username_property } },
      { secretKey = "db_password", remoteRef = { key = local.db.credentials.key, property = local.db.credentials.password_property } },
    ],
    local.bus_creds == null ? [] : [
      { secretKey = "kafka_username", remoteRef = { key = local.bus_creds.key, property = local.bus_creds.username_property } },
      { secretKey = "kafka_password", remoteRef = { key = local.bus_creds.key, property = local.bus_creds.password_property } },
    ],
    local.in_cluster_kafka ? [
      { secretKey = "kafka_password", remoteRef = { key = "caseforge", property = "password" }, sourceRef = { storeRef = local.cluster_ref } },
      { secretKey = "kafka_ca", remoteRef = { key = "kafka-cluster-ca-cert", property = "ca.crt" }, sourceRef = { storeRef = local.cluster_ref } },
    ] : [],
    local.search_creds == null ? [] : [
      { secretKey = "search_username", remoteRef = { key = local.search_creds.key, property = local.search_creds.username_property } },
      { secretKey = "search_password", remoteRef = { key = local.search_creds.key, property = local.search_creds.password_property } },
    ],
    local.in_cluster_search ? [
      { secretKey = "search_password", remoteRef = { key = "search-user", property = "password" }, sourceRef = { storeRef = local.cluster_ref } },
    ] : [],
    [for env, ref in var.extra_secrets : { secretKey = "extra_${lower(env)}", remoteRef = ref }],
  )

  # Go templates, rendered by External Secrets. Passwords are URL-encoded or JSON-encoded, never pasted raw.
  database_url = "postgresql+psycopg://{{ .db_username | urlquery }}:{{ .db_password | urlquery }}@${local.db.host}:${local.db.port}/${local.db.name}?sslmode=${local.db.sslmode}"

  kafka_config = (
    local.in_cluster_kafka
    ? "{{ dict \"security.protocol\" \"SASL_SSL\" \"sasl.mechanisms\" \"SCRAM-SHA-512\" \"sasl.username\" \"caseforge\" \"sasl.password\" .kafka_password \"ssl.ca.pem\" .kafka_ca | toJson }}"
    : local.bus_creds != null
    ? "{{ dict \"security.protocol\" \"SASL_SSL\" \"sasl.mechanisms\" \"${local.c.event_bus.sasl_mechanism}\" \"sasl.username\" .kafka_username \"sasl.password\" .kafka_password | toJson }}"
    : null
  )

  search_config = (
    local.in_cluster_search
    ? "{{ dict \"http_auth\" (list \"caseforge\" .search_password) \"verify_certs\" true \"ca_certs\" \"${local.search_ca_dir}/ca.crt\" | toJson }}"
    : local.search_creds != null
    ? "{{ dict \"http_auth\" (list .search_username .search_password) \"verify_certs\" true | toJson }}"
    : null
  )

  secret_template = merge(
    { DATABASE_URL = local.database_url },
    local.kafka_config == null ? {} : { KAFKA_CONFIG = local.kafka_config },
    local.search_config == null ? {} : { SEARCH_CONFIG = local.search_config },
    { for env, _ in var.extra_secrets : env => "{{ .extra_${lower(env)} }}" },
  )
}

resource "kubernetes_manifest" "app_secret" {
  manifest = {
    apiVersion = "external-secrets.io/v1"
    kind       = "ExternalSecret"
    metadata   = { name = local.secret_name, namespace = local.namespace, labels = local.labels }
    spec = {
      refreshInterval = var.secret_refresh_interval
      secretStoreRef  = local.cloud_ref
      target = {
        name           = local.secret_name
        creationPolicy = "Owner"
        template = {
          engineVersion = "v2"
          mergePolicy   = "Replace"
          data          = local.secret_template
        }
      }
      data = local.secret_data
    }
  }
  depends_on = [kubernetes_manifest.cloud_store, kubernetes_manifest.cluster_store]
}

# ---- Delivery
locals {
  public_url = "https://${var.release.host}"

  app_values = {
    existingSecret = local.secret_name
    image          = { repository = var.release.image_repository }
    publicUrl      = local.public_url
    auth           = { mode = "oidc", operatorIssuer = var.release.operator_issuer }
    storage        = { evidenceUrl = local.c.storage.evidence_url, quarantineUrl = local.c.storage.quarantine_url }
    eventBus = {
      url = (
        local.in_cluster_kafka ? "kafka://${local.kafka_bootstrap}"
        : local.c.event_bus.mode == "managed" ? local.c.event_bus.url : ""
      )
    }
    search = {
      url = (
        local.in_cluster_search ? "opensearchs://${local.search_host}:9200"
        : local.c.search.mode == "managed" ? local.c.search.url : ""
      )
    }
    temporal       = { url = var.release.temporal_url }
    serviceAccount = { create = false, name = local.c.service_account }
    podLabels      = local.c.pod_labels
    extraEnv       = local.c.extra_env
    ingress = {
      enabled       = true
      className     = var.release.ingress_class
      host          = var.release.host
      tlsSecretName = "${local.c.name}-tls"
      annotations   = { "cert-manager.io/cluster-issuer" = var.release.cluster_issuer }
    }
    networkPolicy = { enabled = true, ingressNamespace = var.ingress_namespace }
    extraVolumes = local.in_cluster_search ? [
      { name = "search-ca", secret = { secretName = "search-http-tls", items = [{ key = "ca.crt", path = "ca.crt" }] } }
    ] : []
    extraVolumeMounts = local.in_cluster_search ? [
      { name = "search-ca", mountPath = local.search_ca_dir, readOnly = true }
    ] : []
  }

  data_values = {
    kafka  = merge({ enabled = local.in_cluster_kafka }, var.data_values.kafka)
    search = merge({ enabled = local.in_cluster_search }, var.data_values.search)
  }

  sync_policy = {
    automated   = var.release.auto_sync ? { prune = true, selfHeal = true } : null
    syncOptions = ["CreateNamespace=false", "ServerSideApply=true", "PruneLast=true"]
    retry       = { limit = 5, backoff = { duration = "30s", factor = 2, maxDuration = "10m" } }
  }
}

# An Argo CD project per stamp: it may deploy only from this repository and only into its own namespace.
resource "kubernetes_manifest" "project" {
  count = var.release.delivery == "argocd" ? 1 : 0
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "AppProject"
    metadata   = { name = "stamp-${local.c.name}", namespace = var.argocd_namespace, labels = local.labels }
    spec = {
      description                = "caseforge stamp ${local.c.name} (${local.c.environment}, ${local.c.tenancy})"
      sourceRepos                = [var.release.git_repository]
      destinations               = [{ server = "https://kubernetes.default.svc", namespace = local.namespace }]
      clusterResourceWhitelist   = []
      namespaceResourceBlacklist = [{ group = "", kind = "ResourceQuota" }, { group = "", kind = "LimitRange" }, { group = "networking.k8s.io", kind = "NetworkPolicy" }]
      orphanedResources          = { warn = true }
    }
  }
}

resource "kubernetes_manifest" "data" {
  count = var.release.delivery == "argocd" && local.any_in_cluster ? 1 : 0
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = "${local.c.name}-data"
      namespace   = var.argocd_namespace
      labels      = local.labels
      annotations = { "argocd.argoproj.io/sync-wave" = "0" }
      finalizers  = ["resources-finalizer.argocd.argoproj.io"]
    }
    spec = {
      project = kubernetes_manifest.project[0].manifest.metadata.name
      source = {
        repoURL        = var.release.git_repository
        targetRevision = var.release.git_revision
        path           = "deploy/helm/caseforge-data"
        helm           = { releaseName = "data", valuesObject = local.data_values }
      }
      destination = { server = "https://kubernetes.default.svc", namespace = local.namespace }
      syncPolicy  = merge(local.sync_policy, { automated = var.release.auto_sync ? { prune = false, selfHeal = true } : null })
    }
  }
}

# The application: the chart and the release's image tag come from git (deploy/environments/<env>/<stamp>.yaml,
# changed by pull request), everything that comes from infrastructure is set here.
resource "kubernetes_manifest" "app" {
  count = var.release.delivery == "argocd" ? 1 : 0
  manifest = {
    apiVersion = "argoproj.io/v1alpha1"
    kind       = "Application"
    metadata = {
      name        = local.c.name
      namespace   = var.argocd_namespace
      labels      = local.labels
      annotations = { "argocd.argoproj.io/sync-wave" = "1" }
      finalizers  = ["resources-finalizer.argocd.argoproj.io"]
    }
    spec = {
      project = kubernetes_manifest.project[0].manifest.metadata.name
      sources = [
        {
          repoURL        = var.release.git_repository
          targetRevision = var.release.git_revision
          path           = "deploy/helm/caseforge"
          helm = {
            releaseName  = local.c.name
            valueFiles   = ["modes/${var.release.mode}.yaml", "$values/deploy/environments/${local.c.environment}/${local.c.name}.yaml"]
            valuesObject = local.app_values
          }
        },
        { repoURL = var.release.git_repository, targetRevision = var.release.git_revision, ref = "values" },
      ]
      destination = { server = "https://kubernetes.default.svc", namespace = local.namespace }
      syncPolicy  = local.sync_policy
    }
  }
  depends_on = [kubernetes_manifest.app_secret]
}

# Without Argo CD (a first install, or a site that does not run it): Helm directly, with the image tag given here.
resource "helm_release" "app" {
  count     = var.release.delivery == "helm" ? 1 : 0
  name      = local.c.name
  chart     = "${path.module}/../../../../deploy/helm/caseforge"
  namespace = local.namespace
  atomic    = true
  wait      = true
  timeout   = 900
  values = [
    file("${path.module}/../../../../deploy/helm/caseforge/modes/${var.release.mode}.yaml"),
    yamlencode(merge(local.app_values, { image = { repository = var.release.image_repository, tag = var.release.image_tag } })),
  ]
  depends_on = [kubernetes_manifest.app_secret]
}
