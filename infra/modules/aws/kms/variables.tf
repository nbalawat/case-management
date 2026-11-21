variable "name" {
  description = "Alias for the key, without the alias/ prefix."
  type        = string
}

variable "description" {
  description = "What the key protects."
  type        = string
}

variable "deletion_window_days" {
  description = "Days between scheduling deletion and the key being destroyed."
  type        = number
  default     = 30
  validation {
    condition     = var.deletion_window_days >= 7 && var.deletion_window_days <= 30
    error_message = "AWS allows 7 to 30 days."
  }
}

variable "service_principals" {
  description = "AWS service principals (for example logs.<region>.amazonaws.com) that encrypt with the key on this account's behalf."
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
