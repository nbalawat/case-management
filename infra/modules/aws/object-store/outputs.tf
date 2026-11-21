output "bucket_arns" {
  description = "Purpose (landing, evidence, quarantine) to bucket ARN."
  value       = { for k, b in aws_s3_bucket.this : k => b.arn }
}

output "urls" {
  description = "Purpose to the URL the application is given."
  value       = { for k, b in aws_s3_bucket.this : k => "s3://${b.bucket}/caseforge" }
}
