variable "name" {
  description = "Name of the network, used in every resource name."
  type        = string
}

variable "cidr" {
  description = "VPC address range. A /16 is split into private, data and public subnets per zone."
  type        = string
  default     = "10.40.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.cidr)) && tonumber(split("/", var.cidr)[1]) <= 16
    error_message = "Give a valid CIDR of /16 or larger."
  }
}

variable "az_count" {
  description = "Availability zones to spread across."
  type        = number
  default     = 3
  validation {
    condition     = var.az_count >= 2 && var.az_count <= 4
    error_message = "Use 2 to 4 zones."
  }
}

variable "single_nat_gateway" {
  description = "One NAT gateway for all zones: cheaper, and a single point of failure. For non-production only."
  type        = bool
  default     = false
}

variable "flow_log_retention_days" {
  description = "How long VPC flow logs are kept."
  type        = number
  default     = 365
}

variable "kms_key_arn" {
  description = "Key that encrypts the flow log group."
  type        = string
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
