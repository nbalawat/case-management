variable "name" {
  description = "Cluster name."
  type        = string
}

variable "size" {
  description = "Cloud-neutral size: small, medium or large."
  type        = string
  default     = "small"
  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size is small, medium or large."
  }
}

variable "kafka_version" {
  description = "Apache Kafka version offered by MSK."
  type        = string
  default     = "3.9.x"
}

variable "storage_gb" {
  description = "Storage per broker."
  type        = number
  default     = 500
}

variable "vpc_id" {
  description = "VPC of the data subnets."
  type        = string
}

variable "subnet_ids" {
  description = "Data subnets, one broker in each."
  type        = list(string)
}

variable "client_security_group_id" {
  description = "Security group of the cluster nodes."
  type        = string
}

variable "kms_key_arn" {
  description = "Key for broker storage, logs and the SCRAM secret."
  type        = string
}

variable "log_retention_days" {
  description = "How long broker logs are kept."
  type        = number
  default     = 365
}

variable "tags" {
  description = "Tags for every resource."
  type        = map(string)
  default     = {}
}

variable "credentials_version" {
  description = "Increase by one to generate and write a new SCRAM password."
  type        = number
  default     = 1
}
