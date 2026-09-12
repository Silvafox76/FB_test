# The one provider, and the two data sources every other file reads.
#
# One region, one account, one workspace (Architecture v0.4 section 6, D13). There
# is no second provider alias and no second region: a replica, a failover region or
# a us-east-1 alias would be a second route, and routes are decisions people take
# rather than branches the code carries (rule 1). us-east-1 is worth naming because
# it is the usual reason a second alias appears - ACM certificates for CloudFront,
# or a global WAF. Neither exists here: nothing serves inbound traffic at all
# (rule 17), so nothing needs a certificate.
provider "aws" {
  region = var.aws_region

  # Every resource carries these. The cost tag is what makes the USD 300 budget in
  # account.tf answerable at the resource level rather than only at the account
  # level, and `managed_by` tells a person reading the console that editing this by
  # hand will be reverted on the next apply.
  default_tags {
    tags = merge(
      {
        project    = var.project
        managed_by = "terraform"
        workspace  = "infra/terraform"
      },
      var.tags,
    )
  }
}

data "aws_caller_identity" "current" {}

# There is deliberately no `data "aws_region"`. The region is var.aws_region and
# reading it back from the provider would be a second name for one value, which is
# how a hardcoded region and a variable end up disagreeing in the same ARN.
#
# Not cosmetic: ARNs are built from this rather than from the literal "aws", so a
# GovCloud or China deployment fails on something real instead of silently
# constructing an ARN that matches nothing.
data "aws_partition" "current" {}
