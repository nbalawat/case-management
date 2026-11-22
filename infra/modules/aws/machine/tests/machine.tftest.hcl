# Plans and applies the AWS cloud machine against mocked providers: no account, no credentials, nothing created.
# Proves the contract output, the SSH-only firewall, the disk and metadata settings, and stop and start.
mock_provider "aws" {
  mock_data "aws_region" {
    defaults = { region = "us-east-1" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_ec2_instance_type_offerings" {
    defaults = { locations = ["us-east-1b", "us-east-1a"] }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { insecure_value = "ami-0123456789abcdef0", value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_data "aws_instance" {
    defaults = { public_ip = "203.0.113.10" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-0123456789abcdef0" }
  }
}

variables {
  name          = "caseforge"
  user_data     = "#cloud-config\n"
  allowed_cidrs = ["198.51.100.7/32"]
}

run "defaults" {
  command = apply
  assert {
    condition     = aws_instance.this.instance_type == "m7i.2xlarge" && output.machine.machine_type == "m7i.2xlarge"
    error_message = "large must be 8 vCPU and 32 GB"
  }
  assert {
    condition     = aws_instance.this.metadata_options[0].http_tokens == "required" && aws_instance.this.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDSv2 only, out of the containers' reach"
  }
  assert {
    condition     = aws_instance.this.root_block_device[0].encrypted && aws_instance.this.root_block_device[0].volume_size == 150 && aws_instance.this.root_block_device[0].delete_on_termination
    error_message = "the disk must be encrypted, 150 GB, and deleted with the machine"
  }
  assert {
    condition     = aws_instance.this.instance_initiated_shutdown_behavior == "stop"
    error_message = "the automatic stop must keep the disk"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.ssh) == 1 && aws_vpc_security_group_ingress_rule.ssh["198.51.100.7/32"].from_port == 22 && aws_vpc_security_group_ingress_rule.ssh["198.51.100.7/32"].to_port == 22
    error_message = "the only way in is SSH from allowed_cidrs"
  }
  assert {
    condition     = aws_subnet.this.availability_zone == "us-east-1a"
    error_message = "the subnet must be in a zone that offers the machine type"
  }
  assert {
    condition     = aws_subnet.this.map_public_ip_on_launch == false
    error_message = "only the machine gets a public address"
  }
  assert {
    condition     = aws_ec2_instance_state.this.state == "running"
    error_message = "the machine runs by default"
  }
  assert {
    condition     = output.machine.cloud == "aws" && output.machine.region == "us-east-1" && output.machine.public_ip == "203.0.113.10" && output.machine.running
    error_message = "the contract output is wrong"
  }
  assert {
    condition     = length(aws_budgets_budget.this) == 0
    error_message = "no budget without an address"
  }
  assert {
    condition     = length(aws_iam_instance_profile.this) == 1
    error_message = "without the setup's profile the module makes its own"
  }
}

run "uses_the_setup_profile" {
  command = plan
  variables {
    instance_profile_name = "caseforge-machine"
  }
  assert {
    condition     = length(aws_iam_role.this) == 0 && aws_instance.this.iam_instance_profile == "caseforge-machine"
    error_message = "with the setup's profile the module must create no IAM resources"
  }
}

run "stopped_keeps_the_machine" {
  command = apply
  variables {
    running = false
  }
  assert {
    condition     = aws_ec2_instance_state.this.state == "stopped" && output.machine.public_ip == "" && !output.machine.running
    error_message = "stopping must keep the machine and report no address"
  }
}

run "sizes_and_budget" {
  command = plan
  variables {
    size          = "xlarge"
    disk_gb       = 300
    budget_email  = "ops@example.com"
    budget_usd    = 90
    allowed_cidrs = ["198.51.100.7/32", "192.0.2.0/28"]
  }
  assert {
    condition     = aws_instance.this.instance_type == "m7i.4xlarge" && aws_instance.this.root_block_device[0].volume_size == 300
    error_message = "xlarge must map to m7i.4xlarge with the requested disk"
  }
  assert {
    condition     = length(aws_budgets_budget.this) == 1 && aws_budgets_budget.this[0].limit_amount == "90.00"
    error_message = "a budget address must create the budget"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.ssh) == 2
    error_message = "one SSH rule per allowed CIDR"
  }
}

run "refuses_the_whole_internet" {
  command = plan
  variables {
    allowed_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.allowed_cidrs]
}

run "refuses_no_cidrs" {
  command = plan
  variables {
    allowed_cidrs = []
  }
  expect_failures = [var.allowed_cidrs]
}

run "refuses_unknown_size" {
  command = plan
  variables {
    size = "huge"
  }
  expect_failures = [var.size]
}
