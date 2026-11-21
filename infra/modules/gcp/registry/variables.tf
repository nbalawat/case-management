variable "project_id" {
  description = "Project of the registry."
  type        = string
}

variable "name" {
  description = "Repository id."
  type        = string
}

variable "region" {
  description = "Region of the repository."
  type        = string
}

variable "kms_key_id" {
  description = "Key that encrypts images."
  type        = string
}

variable "reader_members" {
  description = "Members allowed to pull."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
