output "machine" {
  description = "The machine contract (infra/contract/machine-outputs.md)."
  value       = module.machine.machine
}

output "ssh_user" {
  description = "User to sign in as."
  value       = module.bootstrap.ssh_user
}

output "forwarded_ports" {
  description = "Consoles forwarded to localhost by the SSH configuration."
  value       = module.bootstrap.forwarded_ports
}
