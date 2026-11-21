variable "project_id" {
  description = "Project of the platform."
  type        = string
}

variable "name" {
  description = "Name of the network, used in every resource name."
  type        = string
}

variable "region" {
  description = "Region, for example europe-west2."
  type        = string
}

variable "cidr" {
  description = "Node address range. Nodes get the first /20."
  type        = string
  default     = "10.60.0.0/16"
}

variable "pods_cidr" {
  description = "Secondary range for pods."
  type        = string
  default     = "10.64.0.0/14"
}

variable "services_cidr" {
  description = "Secondary range for services."
  type        = string
  default     = "10.68.0.0/20"
}
