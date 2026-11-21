mock_provider "aws" {
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{}" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}

mock_provider "tls" {}

variables {
  name        = "cf-dev-shared"
  vpc_id      = "vpc-123"
  subnet_ids  = ["subnet-a", "subnet-b", "subnet-c"]
  kms_key_arn = "arn:aws:kms:eu-west-2:123456789012:key/mock"
}

override_resource {
  target = aws_eks_cluster.this
  values = {
    certificate_authority = [{ data = "bW9jaw==" }]
    identity              = [{ oidc = [{ issuer = "https://oidc.eks.eu-west-2.amazonaws.com/id/MOCK" }] }]
  }
}

override_resource {
  target = aws_launch_template.node
  values = { id = "lt-mock", latest_version = 1 }
}

override_resource {
  target = aws_iam_openid_connect_provider.this
  values = { arn = "arn:aws:iam::123456789012:oidc-provider/mock" }
}

run "private_api_encrypted_secrets_imdsv2" {
  command = plan

  assert {
    condition     = aws_eks_cluster.this.vpc_config[0].endpoint_public_access == false && aws_eks_cluster.this.vpc_config[0].endpoint_private_access
    error_message = "The API endpoint is private by default."
  }
  assert {
    condition     = aws_eks_cluster.this.encryption_config[0].resources == toset(["secrets"])
    error_message = "Kubernetes secrets are envelope-encrypted with the platform key."
  }
  assert {
    condition     = aws_eks_cluster.this.access_config[0].authentication_mode == "API" && !aws_eks_cluster.this.access_config[0].bootstrap_cluster_creator_admin_permissions
    error_message = "Access entries only; the creator is not silently made admin."
  }
  assert {
    condition = alltrue([for t in aws_launch_template.node :
    t.metadata_options[0].http_tokens == "required" && t.metadata_options[0].http_put_response_hop_limit == 1])
    error_message = "Nodes require IMDSv2 and pods cannot reach node credentials."
  }
  assert {
    condition     = length(aws_eks_cluster.this.enabled_cluster_log_types) == 5
    error_message = "Every control plane log stream is on."
  }
}

run "never_open_to_the_internet" {
  command = plan
  variables {
    public_api_allowed_cidrs = ["0.0.0.0/0"]
  }
  expect_failures = [var.public_api_allowed_cidrs]
}
