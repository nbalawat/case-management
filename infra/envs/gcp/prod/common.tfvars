# Example values: replace every CHANGE_ME before the first plan. No secrets belong in this file.
project_id           = "CHANGE_ME_PROJECT"
region               = "europe-west2"
state_encryption_key = "projects/CHANGE_ME_PROJECT/locations/europe-west2/keyRings/caseforge-tofu-state/cryptoKeys/data"
tags = {
  caseforge-owner = "platform-team"
  caseforge-env   = "prod"
}
