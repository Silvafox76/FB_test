# Versions and state. Architecture v0.4 section 6, "IaC: Terraform, one workspace.
# Rebuild in under an hour."
#
# WHERE THE STATE LIVES, AND WHAT THAT IMPLIES FOR WHO CAN READ IT.
#
# Terraform state is a plaintext JSON record of every attribute of every resource
# it manages. It is not encrypted by Terraform and it is not redacted: anything an
# AWS API returns about a resource is in there. Today that is infrastructure
# detail - instance id, Elastic IP, bucket name, role ARNs, secret ARNs - and no
# secret VALUE, because this workspace declares `aws_secretsmanager_secret` and
# never `aws_secretsmanager_secret_version` (secrets.tf says why). The day anyone
# adds a version resource, or any resource with a password attribute, that value
# is in state in clear text forever, including in every prior version the bucket
# keeps. So: read access to this bucket is the same privilege as read access to
# the secrets, and it is granted to the same small set of people, not to the
# pilot's reviewer and not to CI (the GitHub deploy role in identity.tf can push
# images and send an SSM command; it cannot read state).
#
# The bucket is NOT created by this workspace - it has to exist before `terraform
# init` can store anything in it, which is the one bootstrap step README.md spells
# out. `use_lockfile` is S3-native state locking (Terraform 1.11+), so there is no
# DynamoDB table to create or to forget.
terraform {
  required_version = ">= 1.11.0, < 2.0.0"

  backend "s3" {
    # A backend block cannot read variables - Terraform evaluates it before
    # anything else exists - so these four values are literals here and nowhere
    # else. They are the only literals in the workspace that variables.tf does not
    # own, and that is a property of Terraform rather than a choice (rule 6).
    bucket       = "fb-opp-monitor-tfstate"
    key          = "pilot/terraform.tfstate"
    region       = "ca-central-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Pinned to the 5.x line. Two resources here need it: the separate
      # `aws_vpc_security_group_egress_rule` resource (network.tf) and optional
      # `thumbprint_list` on the OIDC provider (identity.tf), neither of which
      # exists in 4.x. The floor was chosen without registry access, so the first
      # `terraform init` is what confirms it - and it fails loudly if 5.70 is not
      # available, which is the right failure.
      version = "~> 5.70"
    }
  }
}
