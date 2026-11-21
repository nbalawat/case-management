# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
# A dedicated stamp for one client (acme): its own database, buckets, key, identity and namespace.
stamp = {
  name        = "acme"
  environment = "staging"
  tenancy     = "dedicated"
  postgres = {
    size                = "small"
    high_availability   = true
    deletion_protection = true
  }
  kafka    = { mode = "managed" }
  search   = { mode = "managed" }
  evidence = { worm = true }
}

release = {
  release = {
    mode             = "dedicated"
    host             = "acme.staging.caseforge.example.com"
    operator_issuer  = "https://idp.example.com/realms/caseforge-operators"
    image_repository = "ghcr.io/nbalawat/caseforge"
    git_revision     = "main"
    auto_sync        = false
    temporal_url     = ""
  }
  # Model provider key and similar, from the cloud's secret manager:
  # extra_secrets = { ANTHROPIC_API_KEY = { key = "cf-staging-acme-anthropic" } }
}
