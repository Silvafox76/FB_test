# The network, which is almost entirely about what is NOT here.
#
# Architecture v0.4 section 6: "One dedicated member account, one region, one Linux
# host in the pilot, NO INBOUND NETWORK PATH AT ALL." Rule 17 says the same thing
# as a blocking rule. So this file declares a security group with ZERO ingress
# rules - not ingress from an office IP, not ingress from the EC2 Instance Connect
# or SSM prefix list, not ingress from itself. Zero.
#
# THE QUESTION THIS RAISES, ANSWERED HERE SO NOBODY HAS TO REDISCOVER IT: how does
# anyone reach the host? Session Manager is entirely outbound. The SSM agent on the
# instance opens the connection to ssmmessages/ec2messages over 443 and holds it;
# the AWS side never initiates anything, so there is nothing for an ingress rule to
# admit. The reviewer's access to the review app is an SSM port forward
# (`aws ssm start-session --document-name AWS-StartPortForwardingSession`), which
# rides that same outbound tunnel and arrives at the app as a connection from
# 127.0.0.1 - which is the only address the app binds to (docker-compose.yml).
#
# WHAT IS DELIBERATELY ABSENT, because each of these is a thing a reader may reach
# for and each would break rule 17 or rule 1:
#
#   - No VPC interface endpoints. They look like the "more private" option, but an
#     interface endpoint is an ENI with its own security group, and that group
#     needs an INGRESS rule on 443 from the instance for the instance to use it.
#     That is an ingress rule in the account, which is exactly what section 6
#     forbids. The public subnet plus 443 egress reaches the same AWS APIs over TLS
#     with no ingress anywhere.
#   - No load balancer, no target group, no Route 53 record, no ACM certificate.
#     Nothing serves inbound traffic, so none of it has anything to point at.
#   - No SSH key pair and no port 22. compute.tf sets no `key_name` for the same
#     reason.
#   - No custom network ACL. The default VPC's NACL allows everything both ways and
#     is left that way: the security group is the control, and a second control
#     that says something slightly different is how a rule ends up being enforced in
#     one place and not the other (rule 1).
#   - No IPv6 egress rules. The default VPC has no IPv6 CIDR unless someone adds
#     one; adding IPv6 rules "just in case" would be egress this deployment cannot
#     see or reason about.

data "aws_vpc" "default" {
  default = true
}

# The default subnet for the chosen AZ - one subnet, public, per section 6. Using
# the default VPC is the document's decision: a purpose-built VPC would need
# subnets, route tables and an internet gateway to be rebuilt in under an hour for
# a single host that accepts no inbound traffic.
data "aws_subnet" "default" {
  availability_zone = var.availability_zone
  default_for_az    = true
  vpc_id            = data.aws_vpc.default.id
}

# No inline ingress or egress blocks, and that is load-bearing twice over.
#
# AWS attaches an "allow all outbound" rule to every new security group. Terraform
# removes that rule when the resource declares no egress of its own, which is what
# makes the two rules below the complete egress surface rather than two rules
# alongside an implicit allow-all. Keeping the rules in separate
# `aws_vpc_security_group_egress_rule` resources means a plan that adds network
# access shows up as a new resource in the diff, not as a line inside an existing
# one.
#
# The absence of any `ingress` block here is the whole of rule 17's enforcement in
# this workspace. A reviewer of this file should be able to confirm it by searching
# for the word once.
resource "aws_security_group" "host" {
  name        = "${var.project}-host"
  description = "Pilot host: no ingress at all (rule 17); egress 443 and 80 only."
  vpc_id      = data.aws_vpc.default.id

  tags = {
    Name = "${var.project}-host"
  }
}

# Egress on 443 and 80 only (Architecture v0.4 section 6).
#
# PORT 80 IS NOT AN OVERSIGHT. Several West African portals still have no TLS, and
# they arrive at step 17. Removing it as a tidy-up would take four wave-2 sources
# offline with a connection timeout that looks like the portal being down.
#
# What a 443/80-only egress group does NOT break, verified against AWS's own
# documentation rather than assumed, because getting this wrong would leave a host
# that cannot resolve a hostname and therefore cannot be reached by Session Manager
# either: "Security groups do not filter traffic destined to and from the
# following: Amazon Domain Name Services (DNS), Amazon Dynamic Host Configuration
# Protocol (DHCP), Amazon EC2 instance metadata, Amazon ECS task metadata
# endpoints, License activation for Windows instances, Amazon Time Sync Service,
# Reserved IP addresses used by the default VPC router."
# (docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html, read
# 2026-09-12.) So DNS to the VPC resolver, NTP to the Time Sync Service and the
# instance metadata call that fetches the instance profile's credentials all still
# work with no rule of their own.
resource "aws_vpc_security_group_egress_rule" "host" {
  for_each = var.egress_rules

  security_group_id = aws_security_group.host.id
  description       = each.value.description
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port

  tags = {
    Name = "${var.project}-egress-${each.key}"
  }
}
