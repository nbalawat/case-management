variable "project_id" {
  description = "Project of the key ring."
  type        = string
}

variable "name" {
  description = "Key ring name. Key rings cannot be deleted, so names are never reused."
  type        = string
}

variable "location" {
  description = "Region of the key ring; it must match the resources it encrypts."
  type        = string
}

variable "protection_level" {
  description = "SOFTWARE or HSM."
  type        = string
  default     = "HSM"
  validation {
    condition     = contains(["SOFTWARE", "HSM"], var.protection_level)
    error_message = "protection_level is SOFTWARE or HSM."
  }
}

variable "encrypter_members" {
  description = "Members (service agents) that encrypt and decrypt with the key."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels for the key."
  type        = map(string)
  default     = {}
}
