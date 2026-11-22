output "machine" {
  description = "The machine contract (infra/contract/machine-outputs.md)."
  value = {
    cloud        = "aws"
    region       = data.aws_region.current.region
    id           = aws_instance.this.id
    machine_type = local.machine_type
    public_ip    = var.running ? data.aws_instance.current.public_ip : ""
    running      = var.running
    console_url  = "https://${data.aws_region.current.region}.console.aws.amazon.com/ec2/home?region=${data.aws_region.current.region}#InstanceDetails:instanceId=${aws_instance.this.id}"
  }
}
