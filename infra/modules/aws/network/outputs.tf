output "vpc_id" {
  description = "Id of the VPC."
  value       = aws_vpc.this.id
}

output "vpc_cidr" {
  description = "Address range of the VPC."
  value       = aws_vpc.this.cidr_block
}

output "private_subnet_ids" {
  description = "Subnets for workloads (cluster nodes)."
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Subnets for data services, with no route out."
  value       = aws_subnet.data[*].id
}

output "public_subnet_ids" {
  description = "Subnets for load balancers and NAT gateways only."
  value       = aws_subnet.public[*].id
}
