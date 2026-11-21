# Backend settings from the bootstrap stack's outputs. infra/bin/stack.py adds the state key per layer.
resource_group_name  = "rg-caseforge-tofu-state"
storage_account_name = "CHANGE_ME"
container_name       = "tfstate"
subscription_id      = "CHANGE_ME_SUBSCRIPTION_ID"
