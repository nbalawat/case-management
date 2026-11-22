# Plans the operator role against a mocked provider. The policy document data source is mocked, so the checks read the
# statements the module declares: no IAM writes, state limited to the machine, EC2 limited to the regions.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/caseforge-machine-operator" }
  }
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::123456789012:instance-profile/caseforge-machine" }
  }
}

variables {
  regions          = ["us-east-1"]
  state_bucket_arn = "arn:aws:s3:::caseforge-tofu-state-123456789012"
  state_key_arn    = "arn:aws:kms:us-east-1:123456789012:key/mock"
}

run "roles_and_profile" {
  command = apply
  assert {
    condition     = aws_iam_role.operator.name == "caseforge-machine-operator" && aws_iam_instance_profile.machine.name == "caseforge-machine"
    error_message = "names must follow the prefix"
  }
  assert {
    condition     = aws_iam_role.operator.max_session_duration == 14400
    error_message = "a session must outlast a first start"
  }
  assert {
    condition     = output.operator_role_arn == "arn:aws:iam::123456789012:role/caseforge-machine-operator" && output.instance_profile_name == "caseforge-machine"
    error_message = "outputs are wrong"
  }
}

run "no_iam_writes_and_scoped_state" {
  command = plan
  assert {
    condition     = length([for s in data.aws_iam_policy_document.operator.statement : s if length([for a in s.actions : a if startswith(a, "iam:") && a != "iam:PassRole" && a != "iam:GetInstanceProfile" && a != "iam:GetRole"]) > 0]) == 0
    error_message = "the operator must not be able to create or change IAM"
  }
  assert {
    condition     = one([for s in data.aws_iam_policy_document.operator.statement : s.resources if s.sid == "MachineState"]) == toset(["arn:aws:s3:::caseforge-tofu-state-123456789012/machine/*"])
    error_message = "the operator may write only the machine's state"
  }
  assert {
    condition     = jsonencode(one([for s in data.aws_iam_policy_document.operator.statement : s.condition if s.sid == "MachineNetworkInstanceAndDisk"])) == jsonencode([{ test = "StringEquals", values = ["us-east-1"], variable = "aws:RequestedRegion" }])
    error_message = "EC2 writes must be limited to the allowed regions"
  }
}
