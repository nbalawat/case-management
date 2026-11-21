variable "state_passphrase" {
  description = "Passphrase for OpenTofu state encryption. Set TF_VAR_state_passphrase; never put it in a file."
  type        = string
  sensitive   = true
}
