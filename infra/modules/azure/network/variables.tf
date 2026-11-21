variable "name" {
  description = "Name of the network, used in every resource name."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group of the platform."
  type        = string
}

variable "cidr" {
  description = "Address space. Nodes get the first /20 of a /16; postgres and private endpoints get /24s."
  type        = string
  default     = "10.50.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.cidr)) && tonumber(split("/", var.cidr)[1]) <= 16
    error_message = "Give a valid CIDR of /16 or larger."
  }
}

variable "zones" {
  description = "Availability zones for the NAT address."
  type        = list(string)
  default     = ["1", "2", "3"]
}

variable "flow_logs" {
  description = "Virtual network flow logs with traffic analytics. Null turns them off (the region's Network Watcher must exist)."
  type = object({
    network_watcher_name           = string
    network_watcher_resource_group = string
    storage_account_id             = string
    workspace_id                   = string
    workspace_guid                 = string
  })
  default = null
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}
