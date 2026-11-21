# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
# The pooled managed-service stamp: many smaller clients, separated by row in one database.
stamp = {
  name        = "pool"
  environment = "dev"
  tenancy     = "pooled"
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
    mode             = "saas"
    host             = "pool.dev.caseforge.example.com"
    operator_issuer  = "https://idp.example.com/realms/caseforge-operators"
    image_repository = "ghcr.io/nbalawat/caseforge"
    git_revision     = "main"
    auto_sync        = true
    temporal_url     = ""
  }
  # Model provider key and similar, from the cloud's secret manager:
  # extra_secrets = { ANTHROPIC_API_KEY = { key = "cf-dev-pool-anthropic" } }
}
