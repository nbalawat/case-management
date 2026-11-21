data "terraform_remote_state" "platform" {
  backend = "gcs"
  config  = var.platform_state
}

data "google_client_config" "this" {}

locals {
  kube = data.terraform_remote_state.platform.outputs.kubernetes
}

# The DNS endpoint presents a publicly trusted certificate and checks IAM on every call.
provider "kubernetes" {
  host  = local.kube.host
  token = data.google_client_config.this.access_token
}

provider "helm" {
  kubernetes = {
    host  = local.kube.host
    token = data.google_client_config.this.access_token
  }
}
