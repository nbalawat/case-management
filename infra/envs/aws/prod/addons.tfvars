# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
addons = {
  acme_email  = "platform-team@example.com"
  acme_server = "https://acme-v02.api.letsencrypt.org/directory"
  argocd_host = "argocd.caseforge.example.com"
  # Sign in to Argo CD with the firm's identity provider; the local admin is for bootstrap only.
  # argocd_oidc         = { name = "SSO", issuer = "https://idp.example.com/realms/ops", clientID = "argocd", clientSecret = "$oidc.clientSecret" }
  # argocd_admin_groups = ["platform-admins"]
  policy_action = "Enforce"
  signed_images = {
    enabled        = true
    image_patterns = ["ghcr.io/nbalawat/caseforge*", "CHANGE_ME_ACCOUNT_ID.dkr.ecr.eu-west-2.amazonaws.com/cf-prod-shared/*"]
    subject_regexp = "^https://github.com/nbalawat/case-management/.github/workflows/release.yml@refs/tags/v.*$"
  }
}
