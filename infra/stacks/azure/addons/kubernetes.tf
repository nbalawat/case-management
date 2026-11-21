variable "kubelogin_mode" {
  description = "How kubelogin gets an Entra token: azurecli locally, workloadidentity in GitHub Actions."
  type        = string
  default     = "workloadidentity"
}

data "terraform_remote_state" "platform" {
  backend = "azurerm"
  config  = var.platform_state
}

locals {
  kube = data.terraform_remote_state.platform.outputs.kubernetes
  # Entra ID's fixed application id for the AKS API server.
  aks_server_id = "6dae42f8-4368-4678-94ff-3960e28e3630"
}

provider "kubernetes" {
  host                   = local.kube.host
  cluster_ca_certificate = base64decode(local.kube.ca_certificate)
  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "kubelogin"
    args        = ["get-token", "--login", var.kubelogin_mode, "--server-id", local.aks_server_id]
  }
}

provider "helm" {
  kubernetes = {
    host                   = local.kube.host
    cluster_ca_certificate = base64decode(local.kube.ca_certificate)
    exec = {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "kubelogin"
      args        = ["get-token", "--login", var.kubelogin_mode, "--server-id", local.aks_server_id]
    }
  }
}
