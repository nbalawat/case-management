# Cluster-wide add-ons, the same on every cloud: certificates, secrets from the cloud's secret manager, an ingress
# controller, GitOps, admission policy, the operators for in-cluster data services, and monitoring. Each is a pinned
# Helm chart; turn one off or override its values with var.addons. The platform's own policies (pod security and
# signed images) are installed after Kyverno from deploy/helm/caseforge-policies.
locals {
  defaults = {
    cert-manager = {
      repository = "https://charts.jetstack.io"
      chart      = "cert-manager"
      version    = "v1.21.3"
      namespace  = "cert-manager"
      values = {
        crds                      = { enabled = true, keep = true }
        replicaCount              = 2
        podDisruptionBudget       = { enabled = true }
        prometheus                = { enabled = true }
        global                    = { leaderElection = { namespace = "cert-manager" } }
        enableCertificateOwnerRef = true
      }
    }
    external-secrets = {
      repository = "https://charts.external-secrets.io"
      chart      = "external-secrets"
      version    = "2.15.0"
      namespace  = "external-secrets"
      values = {
        installCRDs         = true
        replicaCount        = 2
        podDisruptionBudget = { enabled = true }
        serviceMonitor      = { enabled = false }
      }
    }
    traefik = {
      repository = "https://traefik.github.io/charts"
      chart      = "traefik"
      version    = "42.4.1"
      namespace  = var.ingress_namespace
      values = {
        deployment          = { replicas = 3 }
        podDisruptionBudget = { enabled = true, minAvailable = 2 }
        ingressClass        = { enabled = true, isDefaultClass = true, name = "traefik" }
        providers           = { kubernetesIngress = { enabled = true }, kubernetesGateway = { enabled = true } }
        ports = {
          web       = { redirections = { entryPoint = { to = "websecure", scheme = "https", permanent = true } } }
          websecure = { tls = { enabled = true } }
        }
        service = { annotations = var.load_balancer_annotations }
        logs    = { access = { enabled = true } }
        securityContext = {
          allowPrivilegeEscalation = false
          readOnlyRootFilesystem   = true
          capabilities             = { drop = ["ALL"] }
        }
      }
    }
    argo-cd = {
      repository = "https://argoproj.github.io/argo-helm"
      chart      = "argo-cd"
      version    = "10.18.2"
      namespace  = "argocd"
      values = {
        global = { domain = var.argocd_host }
        configs = {
          params = { "server.insecure" = false, "application.namespaces" = "" }
          cm = {
            "admin.enabled"                                 = var.argocd_oidc == null
            "exec.enabled"                                  = false
            "users.anonymous.enabled"                       = false
            "oidc.config"                                   = var.argocd_oidc == null ? "" : yamlencode(var.argocd_oidc)
            "resource.customizations.ignoreDifferences.all" = yamlencode({ managedFieldsManagers = ["kube-controller-manager"] })
          }
          rbac = {
            "policy.default" = "role:readonly"
            "policy.csv"     = join("\n", [for g in var.argocd_admin_groups : "g, ${g}, role:admin"])
          }
        }
        dex        = { enabled = false }
        controller = { replicas = 1 }
        server     = { replicas = 2, ingress = { enabled = var.argocd_host != "", ingressClassName = "traefik", tls = true, annotations = { "cert-manager.io/cluster-issuer" = var.cluster_issuer } } }
        repoServer = { replicas = 2 }
        redis-ha   = { enabled = false }
      }
    }
    kyverno = {
      repository = "https://kyverno.github.io/kyverno"
      chart      = "kyverno"
      version    = "3.9.2"
      namespace  = "kyverno"
      values = {
        admissionController  = { replicas = 3 }
        backgroundController = { replicas = 2 }
        cleanupController    = { replicas = 2 }
        reportsController    = { replicas = 2 }
        features             = { policyExceptions = { enabled = true, namespace = "kyverno" } }
      }
    }
    strimzi = {
      repository = "https://strimzi.io/charts"
      chart      = "strimzi-kafka-operator"
      version    = "1.3.1"
      namespace  = "strimzi"
      values     = { watchAnyNamespace = true, replicas = 1 }
    }
    opensearch-operator = {
      repository = "https://opensearch-project.github.io/opensearch-k8s-operator"
      chart      = "opensearch-operator"
      version    = "3.0.14"
      namespace  = "opensearch-operator"
      # Only the current opensearch.org API: no deprecated opensearch.opster.io CRDs, webhooks or watches.
      values = { legacyAPI = { enabled = false } }
    }
    flink-kubernetes-operator = {
      repository = "https://downloads.apache.org/flink/flink-kubernetes-operator-1.16.1"
      chart      = "flink-kubernetes-operator"
      version    = "1.16.1"
      namespace  = "flink-operator"
      values     = { watchNamespaces = [], webhook = { create = true } }
    }
    kube-prometheus-stack = {
      repository = "https://prometheus-community.github.io/helm-charts"
      chart      = "kube-prometheus-stack"
      version    = "93.6.1"
      namespace  = "monitoring"
      values = {
        prometheus = { prometheusSpec = { retention = "15d", serviceMonitorSelectorNilUsesHelmValues = false, podMonitorSelectorNilUsesHelmValues = false } }
      }
    }
  }

  # Install order: cert-manager first (webhooks of later charts need it), then everything else.
  first = ["cert-manager"]

  addons = {
    for name, d in local.defaults : name => merge(d, {
      enabled = try(var.addons[name].enabled, true)
      version = try(var.addons[name].version, d.version)
      values  = merge(d.values, try(var.addons[name].values, {}))
    })
  }
  enabled_first = { for k, v in local.addons : k => v if v.enabled && contains(local.first, k) }
  enabled_rest  = { for k, v in local.addons : k => v if v.enabled && !contains(local.first, k) }
}

resource "helm_release" "first" {
  for_each         = local.enabled_first
  name             = each.key
  repository       = each.value.repository
  chart            = each.value.chart
  version          = each.value.version
  namespace        = each.value.namespace
  create_namespace = true
  atomic           = true
  cleanup_on_fail  = true
  wait             = true
  timeout          = 900
  values           = [yamlencode(each.value.values)]
}

resource "helm_release" "rest" {
  for_each         = local.enabled_rest
  name             = each.key
  repository       = each.value.repository
  chart            = each.value.chart
  version          = each.value.version
  namespace        = each.value.namespace
  create_namespace = true
  atomic           = true
  cleanup_on_fail  = true
  wait             = true
  timeout          = 900
  values           = [yamlencode(each.value.values)]
  depends_on       = [helm_release.first]
}

# The platform's policies and the certificate issuer, from this repository.
resource "helm_release" "policies" {
  count     = local.addons["kyverno"].enabled ? 1 : 0
  name      = "caseforge-policies"
  chart     = "${path.module}/../../../../deploy/helm/caseforge-policies"
  namespace = "kyverno"
  atomic    = true
  wait      = true
  values = [yamlencode({
    podSecurity   = { action = var.policy_action }
    signedImages  = var.signed_images
    clusterIssuer = { name = var.cluster_issuer, acmeEmail = var.acme_email, acmeServer = var.acme_server, ingressClass = "traefik" }
  })]
  depends_on = [helm_release.rest]
}
