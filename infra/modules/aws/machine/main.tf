# The cloud machine on AWS: one EC2 instance running Ubuntu 24.04 with the cloud-neutral user data from
# modules/common/machine-bootstrap, in a small VPC of its own whose only way in is SSH from allowed_cidrs. Everything
# is created for the machine and deleted with it, so `down` leaves nothing that bills.
data "aws_region" "current" {}

# Canonical publishes the current Ubuntu 24.04 image id per region here.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

# Not every zone offers every machine type (us-east-1e has no m7i, for example): pick one that offers this one.
data "aws_ec2_instance_type_offerings" "here" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [local.machine_type]
  }
}

locals {
  machine_types = {
    small  = "m7i.xlarge"  # 4 vCPU, 16 GB
    large  = "m7i.2xlarge" # 8 vCPU, 32 GB
    xlarge = "m7i.4xlarge" # 16 vCPU, 64 GB
  }
  machine_type = local.machine_types[var.size]
  tags         = merge(var.tags, { "caseforge/machine" = var.name })
}

resource "aws_vpc" "this" {
  #checkov:skip=CKV2_AWS_11:A single-operator dev machine; flow logs would add cost and the only inbound path is SSH from allowed_cidrs
  cidr_block           = "10.42.0.0/24"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = var.name })
}

# The default security group admits nothing, so nothing can be attached to it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = "${var.name}-default-closed" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = var.name })
}

resource "aws_subnet" "this" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = aws_vpc.this.cidr_block
  availability_zone       = sort(data.aws_ec2_instance_type_offerings.here.locations)[0]
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, { Name = var.name })
}

resource "aws_route_table" "this" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(local.tags, { Name = var.name })
}

resource "aws_route_table_association" "this" {
  subnet_id      = aws_subnet.this.id
  route_table_id = aws_route_table.this.id
}

# SSH from the operator only. Docker publishes the stack's ports on the machine's interfaces; this group is what keeps
# them private, and the laptop reaches them through the SSH tunnel.
resource "aws_security_group" "this" {
  name_prefix = "${var.name}-"
  description = "CaseForge cloud machine: SSH from the operator only"
  vpc_id      = aws_vpc.this.id
  tags        = merge(local.tags, { Name = var.name })
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  for_each          = toset(var.allowed_cidrs)
  security_group_id = aws_security_group.this.id
  description       = "SSH from the operator"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  tags              = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  #checkov:skip=CKV_AWS_382:The machine pulls Ubuntu packages, container images and the repository from the internet
  security_group_id = aws_security_group.this.id
  description       = "Packages, images and the repository"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  tags              = local.tags
}

# Session Manager as a second way in (for example when the operator's address has changed): no keys, no open port.
# The setup creates the profile once (modules/aws/machine-operator) so the operator role needs no IAM write access;
# without one, the module creates its own.
data "aws_iam_policy_document" "assume" {
  count = var.instance_profile_name == null ? 1 : 0
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  count              = var.instance_profile_name == null ? 1 : 0
  name_prefix        = "${var.name}-"
  assume_role_policy = data.aws_iam_policy_document.assume[0].json
  tags               = local.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  count      = var.instance_profile_name == null ? 1 : 0
  role       = aws_iam_role.this[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_partition" "current" {}

resource "aws_iam_instance_profile" "this" {
  count       = var.instance_profile_name == null ? 1 : 0
  name_prefix = "${var.name}-"
  role        = aws_iam_role.this[0].name
  tags        = local.tags
}

resource "aws_instance" "this" {
  #checkov:skip=CKV_AWS_88:The public address is how the operator reaches SSH; the security group admits only allowed_cidrs on port 22
  #checkov:skip=CKV_AWS_126:Detailed monitoring costs extra and a dev machine does not need one-minute metrics
  ami                                  = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type                        = local.machine_type
  subnet_id                            = aws_subnet.this.id
  vpc_security_group_ids               = [aws_security_group.this.id]
  associate_public_ip_address          = true
  iam_instance_profile                 = var.instance_profile_name == null ? aws_iam_instance_profile.this[0].name : var.instance_profile_name
  user_data                            = var.user_data
  user_data_replace_on_change          = false
  instance_initiated_shutdown_behavior = "stop" # the automatic stop keeps the disk
  ebs_optimized                        = true
  monitoring                           = false

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1          # containers cannot reach the instance's credentials
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.disk_gb
    encrypted             = true
    delete_on_termination = true
    tags                  = merge(local.tags, { Name = var.name })
  }

  tags = merge(local.tags, { Name = var.name })

  lifecycle {
    # A newer Ubuntu image or changed user data must not replace a machine that holds data: cloud-init runs once, and
    # later changes go through the machine's own `caseforge update` and `caseforge stack`.
    # A stopped instance reports no public address (associate_public_ip_address reads back false), which would
    # otherwise force a replacement on `start`, destroying the disk and its data.
    ignore_changes = [ami, user_data, associate_public_ip_address]
  }
}

resource "aws_ec2_instance_state" "this" {
  instance_id = aws_instance.this.id
  state       = var.running ? "running" : "stopped"
}

# Read after any start or stop: a stopped instance has no public address and a started one gets a new one.
data "aws_instance" "current" {
  instance_id = aws_ec2_instance_state.this.instance_id
  depends_on  = [aws_ec2_instance_state.this]
}

resource "aws_budgets_budget" "this" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${var.name}-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }

  tags = local.tags
}
