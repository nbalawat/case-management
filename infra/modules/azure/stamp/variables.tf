# The inputs every cloud shares are in contract.tf. These are Azure's own.

variable "deployer_ip_ranges" {
  description = "Public ranges allowed to manage the stamp's Key Vault. Empty: OpenTofu must run inside the network."
  type        = list(string)
  default     = []
}
