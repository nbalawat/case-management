output "user_data" {
  description = "Cloud-init document for an Ubuntu 24.04 machine: the same on every cloud."
  value       = local.user_data
}

output "ssh_user" {
  description = "User to sign in as."
  value       = var.ssh_user
}

output "forwarded_ports" {
  description = "Consoles on the machine, by name, that the laptop's SSH configuration forwards to the same port on localhost."
  value       = local.forwarded_ports
}
