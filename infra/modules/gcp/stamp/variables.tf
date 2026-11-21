# The inputs every cloud shares are in contract.tf. These are GCP's own.

variable "key_ring_suffix" {
  description = "Key rings and Cloud SQL names cannot be reused soon after deletion: change this to recreate a stamp."
  type        = string
  default     = "v1"
}
