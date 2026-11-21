# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
# A dedicated stamp for one client (acme): its own database, buckets, key, identity and namespace.
stamp = {
  name        = "acme"
  environment = "dev"
  tenancy     = "dedicated"
  postgres = {
    size                = "small"
    high_availability   = false
    deletion_protection = false
  }
  kafka    = { mode = "in_cluster" }
  search   = { mode = "in_cluster" }
  evidence = { worm = false }
}

release = {
  release = {
    mode             = "dedicated"
    host             = "acme.dev.caseforge.example.com"
    operator_issuer  = "https://idp.example.com/realms/caseforge-operators"
    image_repository = "ghcr.io/nbalawat/caseforge"
    git_revision     = "main"
    auto_sync        = true
    temporal_url     = ""
  }
  # Model provider key and similar, from the cloud's secret manager:
  # extra_secrets = { ANTHROPIC_API_KEY = { key = "cf-dev-acme-anthropic" } }
}
