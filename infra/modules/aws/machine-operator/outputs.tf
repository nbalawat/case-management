output "operator_role_arn" {
  description = "Role that `infra/bin/cloud.py` assumes for up, stop, start and down."
  value       = aws_iam_role.operator.arn
}

output "instance_profile_name" {
  description = "Instance profile every cloud machine uses (Session Manager only)."
  value       = aws_iam_instance_profile.machine.name
}
