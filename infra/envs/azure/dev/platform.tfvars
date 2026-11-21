# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
platform = {
  name        = "shared"
  environment = "dev"
  location    = "uksouth"
  network = {
    cidr = "10.50.0.0/16"
  }
  cluster = {
    kubernetes_version     = "1.33"
    sku_tier               = "Free"
    admin_group_object_ids = ["CHANGE_ME_ENTRA_GROUP_ID"]
  }
  # Public ranges of the runners that manage Key Vault and push images. Prefer self-hosted runners in the VNet.
  deployer_ip_ranges = []
}
