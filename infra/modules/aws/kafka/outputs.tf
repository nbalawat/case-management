output "bootstrap_brokers" {
  description = "SASL/SCRAM bootstrap brokers, comma separated."
  value       = aws_msk_cluster.this.bootstrap_brokers_sasl_scram
}

output "credentials_secret_arn" {
  description = "Secrets Manager secret holding the application's SCRAM {username, password}."
  value       = aws_secretsmanager_secret.scram.arn
}
