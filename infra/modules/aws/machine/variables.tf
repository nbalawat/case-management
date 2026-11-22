# AWS-only settings; the cloud-neutral ones are in contract.tf.

variable "instance_profile_name" {
  description = "Existing instance profile for the machine (the setup's, from modules/aws/machine-operator). Null: the module creates its own, which needs IAM write access."
  type        = string
  default     = null
}
