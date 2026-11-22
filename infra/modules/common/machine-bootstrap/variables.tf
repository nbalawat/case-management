variable "ssh_public_key" {
  description = "Public key (OpenSSH format) allowed to sign in as the machine's user. The private key never leaves the laptop."
  type        = string
  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must be one OpenSSH public key line (ssh-ed25519 ...)."
  }
}

variable "ssh_user" {
  description = "The machine's user: owns the checkout, runs the stack, signs in over SSH."
  type        = string
  default     = "caseforge"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.ssh_user))
    error_message = "ssh_user must be a lower-case Linux user name."
  }
}

variable "stack" {
  description = "Makefile target the machine runs: full (the default), sso, observability, lineage, streaming, backbone or demo."
  type        = string
  default     = "full"
  validation {
    condition     = contains(["demo", "sso", "backbone", "backbone-cdc", "full", "observability", "lineage", "streaming"], var.stack)
    error_message = "stack must be one of the Docker targets in the Makefile: demo, sso, backbone, backbone-cdc, full, observability, lineage, streaming."
  }
}

variable "git_url" {
  description = "Repository the machine clones over HTTPS."
  type        = string
  default     = "https://github.com/nbalawat/case-management.git"
  validation {
    condition     = startswith(var.git_url, "https://")
    error_message = "git_url must be an https:// URL (the machine holds no Git credentials)."
  }
}

variable "git_ref" {
  description = "Branch, tag or commit to run."
  type        = string
  default     = "main"
  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]{1,200}$", var.git_ref))
    error_message = "git_ref must be a branch, tag or commit."
  }
}

variable "auto_stop_hours" {
  description = "The machine stops itself (disk kept) this many hours after each boot, so a forgotten machine stops billing for compute. 0 turns it off."
  type        = number
  default     = 10
  validation {
    condition     = var.auto_stop_hours >= 0 && var.auto_stop_hours <= 168 && floor(var.auto_stop_hours) == var.auto_stop_hours
    error_message = "auto_stop_hours must be a whole number from 0 to 168."
  }
}
