# The one host. Architecture v0.4 section 6, Compute and Access.
#
# t3.large, Ubuntu 24.04 LTS, standard credit mode, Elastic IP, reached only
# through Session Manager. Four things here are easy to get subtly wrong and each
# carries its reason at the resource.

# Ubuntu 24.04 LTS, resolved at plan time from Canonical's own AMIs.
#
# An AMI id is region- and date-specific: `ami-0abc...` is a different image in
# ca-central-1 than in us-east-1, and Canonical publishes a new one every few weeks
# with the security updates in it. Pinning one would mean a rebuild in month three
# launches a host that is three months behind on patches, which is worse than the
# small risk that `most_recent` moves the image between a plan and an apply.
#
# 099720109477 is Canonical's AWS account. The owner filter is what makes this
# safe: "ubuntu-noble-24.04-amd64-server-*" is a name anyone can publish, and
# matching on name alone across all public AMIs is how a community image ends up
# running the pipeline.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name = "name"
    # hvm-ssd and hvm-ssd-gp3 are both published for noble; the glob accepts either
    # rather than the workspace having to know which one Canonical is currently
    # promoting in this region.
    values = ["ubuntu/images/hvm-ssd*/ubuntu-${var.ubuntu_release}-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_instance" "monitor" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.default.id
  vpc_security_group_ids = [aws_security_group.host.id]
  iam_instance_profile   = aws_iam_instance_profile.monitor.name

  # No key_name, deliberately. There is no SSH port to use a key on (network.tf),
  # and a key pair that exists is a key pair someone will eventually open a port
  # for. Access is Session Manager only (Architecture v0.4 section 6, Access).
  # Ubuntu Server 24.04 LTS is on AWS's list of AMIs with the SSM agent
  # preinstalled
  # (docs.aws.amazon.com/systems-manager/latest/userguide/ami-preinstalled-agent.html,
  # read 2026-09-12), so there is no installation step here - only the instance
  # profile in identity.tf, which is what makes the host a managed node. That same
  # page warns the preinstalled version may not be current, and says to check the
  # agent is actually running before trusting Session Manager on a new instance;
  # for this deployment that check is the first bring-up step, because a stopped
  # agent means no access path at all.

  # Standard, never unlimited. Section 6's reason, verbatim: "Standard mode means
  # an exhausted credit balance slows the run rather than raising the bill." The
  # CPUCreditBalance alarm in observability.tf is what tells a person the run got
  # slower.
  credit_specification {
    cpu_credits = var.cpu_credits
  }

  # The 100 GB gp3 volume from section 6. It is the root volume rather than a
  # second attached volume: one volume is one thing to snapshot, one thing to fill
  # and one disk alarm. Encrypted with the account's default EBS key - the Postgres
  # data directory, the raw payload tree and the export batches all live on it.
  # delete_on_termination stays true: AWS Backup (backups.tf) holds the daily
  # snapshot, and an orphaned volume left behind by a rebuild is an unowned copy of
  # the database that nobody is watching.
  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = var.root_volume_type
    encrypted             = true
    delete_on_termination = true

    tags = {
      Name = "${var.project}-root"
    }
  }

  # IMDSv2 required: the token-based metadata service cannot be read by a
  # server-side request forgery through a fetched portal page, which is a real
  # shape of risk for a process whose whole job is fetching arbitrary HTML.
  #
  # hop_limit = 2 is not a default and the deployment does not work without it:
  # the pipeline runs in a Docker container on the bridge network, so its metadata
  # request takes one extra network hop and a limit of 1 silently returns nothing.
  # The symptom would be the instance profile's credentials being unavailable to
  # the container only - Bedrock and S3 failing inside the container while the same
  # call works from the host shell.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  # First boot only: the service account, the directories deploy/README.md already
  # standardises on, and Docker. Deployments after that go through SSM Run Command
  # from GitHub Actions (identity.tf), not through a rewritten user_data - which is
  # why `user_data_replace_on_change` is left at its default of false. Editing the
  # script changes what a REBUILT host starts with; it does not touch a running
  # one, and pretending otherwise by replacing the instance would destroy the
  # database volume to apply a shell script.
  user_data = file("${path.module}/user_data.sh")

  tags = {
    Name = "${var.project}-host"
    # AWS Backup selects by this tag (backups.tf). A tag rather than an ARN so the
    # plan keeps working across a rebuild that gives the instance a new id.
    backup = "daily"
  }

  # The instance is useless without its profile and its log destination, and
  # Terraform cannot infer the second dependency from an attribute reference.
  depends_on = [
    aws_iam_role_policy_attachment.monitor_ssm_core,
    aws_cloudwatch_log_group.session_manager,
  ]
}

# A fixed outbound address. Nothing inbound uses it - there is nothing inbound -
# and that is worth saying because an Elastic IP usually means "somewhere to point
# DNS". Here it is the source address portals see (rule 21 asks for an identified,
# honest client; an address that changes on every reboot is the opposite of that),
# and the address a portal operator allowlists when asked to. It also survives a
# stop/start, so a t3 that is stopped to save money on a quiet week comes back at
# the same address.
resource "aws_eip" "monitor" {
  domain = "vpc"

  tags = {
    Name = "${var.project}-eip"
  }
}

resource "aws_eip_association" "monitor" {
  instance_id   = aws_instance.monitor.id
  allocation_id = aws_eip.monitor.id
}

# "Every session is logged" (Architecture v0.4 section 6, Access).
#
# CloudTrail records that a session started and who started it. This document is
# what records WHAT WAS TYPED: Session Manager reads its preferences from an SSM
# document with this exact reserved name, and streaming to CloudWatch Logs is the
# setting that turns transcripts on. The name is fixed by AWS, so an account that
# already has one - set up by hand in the console, say - makes this create fail
# with an "already exists" error. That is the right failure: two sources for the
# same preference is how session logging ends up quietly off.
#
# cloudWatchEncryptionEnabled is false because the log group uses CloudWatch's
# default encryption rather than a customer-managed KMS key. With it true and no
# KMS key on the group, Session Manager refuses to start a session at all - the
# access path fails closed, which in this deployment means no access path.
resource "aws_ssm_document" "session_preferences" {
  name            = "SSM-SessionManagerRunShell"
  document_type   = "Session"
  document_format = "JSON"

  content = jsonencode({
    schemaVersion = "1.0"
    description   = "Session Manager preferences for the ${var.project} pilot host: every session transcript to CloudWatch Logs."
    sessionType   = "Standard_Stream"
    inputs = {
      cloudWatchLogGroupName      = aws_cloudwatch_log_group.session_manager.name
      cloudWatchEncryptionEnabled = false
      cloudWatchStreamingEnabled  = true
      s3BucketName                = ""
      s3EncryptionEnabled         = true
      idleSessionTimeout          = "20"
      shellProfile = {
        # A port forward never runs a shell, so this affects interactive sessions
        # only: land in the checkout deploy/README.md defines rather than in /usr/bin.
        linux = "cd /opt/monitor"
      }
    }
  })
}
