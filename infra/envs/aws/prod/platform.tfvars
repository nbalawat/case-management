# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
platform = {
  name        = "shared"
  environment = "prod"
  network = {
    cidr               = "10.40.0.0/16"
    zones              = 3
    single_nat_gateway = false
  }
  cluster = {
    kubernetes_version = "1.33"
    # The API stays private. Add your VPN or self-hosted runner ranges here, never 0.0.0.0/0.
    private_api_allowed_cidrs = ["10.0.0.0/8"]
    admin_principal_arns      = ["arn:aws:iam::CHANGE_ME_ACCOUNT_ID:role/caseforge-github-apply-prod"]
  }
}
