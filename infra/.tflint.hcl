# TFLint for every module and stack: the bundled Terraform rules (recommended preset plus naming and documentation)
# and the cloud rulesets, which catch invalid instance types, regions and arguments that validate does not.
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "aws" {
  enabled = true
  version = "0.50.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}

plugin "azurerm" {
  enabled = true
  version = "0.33.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

plugin "google" {
  enabled = true
  version = "0.41.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}

rule "terraform_naming_convention" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_standard_module_structure" {
  enabled = false # contract.tf and common.tf hold variables on purpose
}

# Data resources are protected by deletion_protection variables, management locks, Key Vault purge protection and
# Cloud KMS's scheduled destruction instead, so non-production stamps can still be torn down by the same code.
rule "azurerm_resources_missing_prevent_destroy" {
  enabled = false
}
