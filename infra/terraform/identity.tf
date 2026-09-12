# Every AWS identity in the pilot. Architecture v0.4 appendix D, the AWS column.
#
# Appendix D lists six rows. Three of them are "none", and they are the interesting
# ones, so they are written out here as comments rather than left as an absence a
# reader has to infer:
#
#   review app (same host, separate process)
#       No identity of its own. It runs as a container on the same host and uses
#       the same instance profile; appendix D's "none beyond the instance profile's
#       s3 write on the exports/ prefix" describes exactly that. A second role for
#       the review process would have to be assumed by something, and the only
#       thing on the host that could assume it is the host. The separation that
#       matters for review is the DATABASE role (rule 11), not an AWS one:
#       monitor_review is the only identity that can insert into approved_records,
#       and that boundary is enforced in Postgres by migrations/002_roles.sql.
#
#   reporting and ops-analyst
#       None. The ops-analyst agent is read-only by tool allowlist and reads
#       Postgres as monitor_readonly. It has no AWS credential and needs none.
#
#   reviewer and backup (human)
#       None. They reach the review app through an SSM port forward and touch no
#       AWS API. A human IAM user for the reviewer would be a credential to manage
#       for a person whose entire interaction is a web form on localhost.
#
# AND THE ONE THAT DOES NOT APPEAR ANYWHERE IN THIS FILE, deliberately: there is no
# CRM identity, no CRM secret and no role with a scope that could reach one (rule
# 15, D31). The pipeline holds no CRM data to send, which is a property of the
# architecture rather than a rule anyone has to remember.

# ---------------------------------------------------------------------------
# ec2-monitor-role: the instance profile
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "monitor_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "monitor" {
  name               = "${var.project}-ec2-monitor-role"
  description        = "Pilot host. Architecture v0.4 appendix D, ec2-monitor-role."
  assume_role_policy = data.aws_iam_policy_document.monitor_assume.json
}

resource "aws_iam_instance_profile" "monitor" {
  name = "${var.project}-ec2-monitor-role"
  role = aws_iam_role.monitor.name
}

data "aws_iam_policy_document" "monitor" {
  # Bedrock, after the cutover. Two named foundation-model ARNs and no wildcard:
  # `foundation-model/*` would let a prompt-version bump move the pilot onto a
  # model nobody costed against the USD 25 daily cap (rule 22), and the failure
  # would be a bill rather than an error.
  #
  # TWO THINGS A PERSON MUST CONFIRM BEFORE THIS GRANT IS WORTH ANYTHING, neither
  # of which Terraform can assert:
  #   1. D1 and D19: that Haiku 4.5 and Sonnet 5 are available in ca-central-1 at
  #      all, and that model access has been requested in the Bedrock console -
  #      access is granted per account per model and an ungranted model returns
  #      AccessDeniedException no matter what this policy says.
  #   2. That the client in monitor/score/client.py invokes them through
  #      bedrock-runtime InvokeModel. A client that uses a cross-region inference
  #      profile needs an `inference-profile/...` ARN here as well, and one that
  #      streams needs InvokeModelWithResponseStream. Appendix D says
  #      bedrock:InvokeModel on the two named model ARNs, so that is what this is;
  #      widening it to cover a call shape nobody has made yet would be guessing.
  statement {
    sid     = "InvokeTheTwoNamedModels"
    effect  = "Allow"
    actions = ["bedrock:InvokeModel"]

    resources = [
      for model_id in var.bedrock_model_ids :
      "arn:${data.aws_partition.current.partition}:bedrock:${var.aws_region}::foundation-model/${model_id}"
    ]
  }

  # S3 read and write on the one bucket. No s3:DeleteObject anywhere: nothing in
  # this system deletes an object, the lifecycle rules in storage.tf are what
  # expire the dumps, and an export batch that can be deleted by the process that
  # wrote it is a record that can be unwritten (rule 16).
  statement {
    sid    = "ObjectStoreReadWrite"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
    ]

    resources = ["${aws_s3_bucket.raw.arn}/*"]
  }

  statement {
    sid    = "ObjectStoreList"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [aws_s3_bucket.raw.arn]
  }

  # The named secrets and no others. The resource list is built from the secrets
  # this workspace creates, so "named" stays literally true as the set changes -
  # adding a portal credential in terraform.tfvars adds it here and nowhere else.
  statement {
    sid       = "ReadNamedSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [for secret in aws_secretsmanager_secret.this : secret.arn]
  }

  # Textract has no resource-level permissions for this API - the only resource
  # that can be written is "*" - so the scoping that matters is the single action.
  # DetectDocumentText reads text from a page image; it is not AnalyzeDocument,
  # which costs about fifteen times as much and answers questions nobody asked.
  statement {
    sid       = "ReadAttachmentText"
    effect    = "Allow"
    actions   = ["textract:DetectDocumentText"]
    resources = ["*"]
  }

  # Logs, scoped to this deployment's groups. CreateLogGroup is included because
  # the CloudWatch agent creates a group when it is pointed at one that does not
  # exist yet; the prefix keeps that from meaning "any group in the account".
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${var.log_group_prefix}*",
    ]
  }

  # PutMetricData takes no resource, so the namespace condition is the only scope
  # available - and it is a real one: without it, this role could overwrite any
  # metric in the account, including the ones the alarms in observability.tf read.
  # Both namespaces are listed because two different publishers are expected here:
  # the application's own metrics module (which does not exist yet - see
  # observability.tf) and the CloudWatch agent's host metrics.
  statement {
    sid       = "PublishOwnMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.metric_namespace, var.cwagent_namespace]
    }
  }

  # NOT IN APPENDIX D, AND ADDED ON PURPOSE - flagged here because a reviewer
  # should see it as an addition rather than find it by accident. Appendix D's
  # instance-profile row lists bedrock, s3, secretsmanager, textract, logs,
  # cloudwatch and ssm messaging, and no ECR. But section 6 says the containers'
  # "images in ECR", and a host that cannot pull them is a host that cannot run
  # anything: step 12's acceptance ("apply from a clean account reaches a running
  # host") fails at the first `docker compose pull`. These are the three read
  # actions a pull needs plus the authorization token call, which takes no resource.
  statement {
    sid       = "RegistryAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullOwnImages"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]

    resources = [for repository in aws_ecr_repository.images : repository.arn]
  }
}

resource "aws_iam_role_policy" "monitor" {
  name   = "${var.project}-ec2-monitor"
  role   = aws_iam_role.monitor.id
  policy = data.aws_iam_policy_document.monitor.json
}

# "ssm messaging" from appendix D, as the managed policy rather than a hand-written
# one. This is the documented minimum for a managed node: UpdateInstanceInformation
# plus the ssmmessages and ec2messages channels Session Manager and Run Command
# ride on. Hand-rolling it to match appendix D's three words more exactly would
# risk omitting one action and locking out THE ONLY ACCESS PATH TO THE HOST, with
# no SSH port to fall back to (network.tf). It grants a little more than appendix D
# says - notably s3:GetObject on AWS's own ssm distribution buckets, which is how
# the agent updates itself - and that is the trade being made knowingly.
resource "aws_iam_role_policy_attachment" "monitor_ssm_core" {
  role       = aws_iam_role.monitor.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# ---------------------------------------------------------------------------
# github-deploy-role: OIDC, no keys
# ---------------------------------------------------------------------------

# Section 6: "GitHub Actions with an OIDC role... No long-lived AWS keys anywhere."
# The provider is what lets a workflow's short-lived token be exchanged for a role.
#
# No thumbprint_list: it is optional in the current AWS provider and AWS no longer
# validates a thumbprint for this issuer. A hardcoded thumbprint is a value that
# silently goes stale when GitHub rotates its certificate chain, and the failure
# arrives as a deploy that cannot authenticate on a day nobody changed anything.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  tags = {
    Name = "${var.project}-github-oidc"
  }
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # THE ONE CONDITION THAT MAKES THIS ROLE SAFE. The federated principal above is
    # "GitHub Actions", not "our repository": without a `sub` condition, ANY
    # workflow in ANY repository on github.com can assume this role and push images
    # into our registry and run commands on our host. The value is
    # repo:<owner>/<repo>:* - every branch and every environment of the one
    # repository, which is as narrow as it can be while still letting a release
    # branch deploy.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.project}-github-deploy-role"
  description        = "GitHub Actions: build and push images, deploy through SSM Run Command. Architecture v0.4 appendix D."
  assume_role_policy = data.aws_iam_policy_document.github_assume.json
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "RegistryAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PushImages"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]

    resources = [for repository in aws_ecr_repository.images : repository.arn]
  }

  # "ssm:SendCommand on the one instance" (appendix D). SendCommand is authorised
  # against BOTH the instance and the document, so both ARNs are here; the document
  # is AWS-owned, which is why its ARN has an empty account field. Naming
  # AWS-RunShellScript specifically means this role can run a shell command on the
  # host and cannot, for example, run the document that changes the host's
  # inventory or applies a patch baseline.
  statement {
    sid     = "DeployToTheOneInstance"
    effect  = "Allow"
    actions = ["ssm:SendCommand"]

    resources = [
      "arn:${data.aws_partition.current.partition}:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.monitor.id}",
      "arn:${data.aws_partition.current.partition}:ssm:${var.aws_region}::document/AWS-RunShellScript",
    ]
  }

  # Also not in appendix D's three words, and also deliberate: SendCommand returns
  # a command id and nothing else. Without these two reads the workflow cannot tell
  # whether the deploy succeeded, and a deploy step that always reports success is
  # worse than no deploy step. Both are read-only and neither can start anything.
  statement {
    sid    = "ObserveTheDeploy"
    effect = "Allow"

    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "${var.project}-github-deploy"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}

# ---------------------------------------------------------------------------
# backup-role
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "backup_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name               = "${var.project}-backup-role"
  description        = "AWS Backup: daily EBS snapshot within the plan. Architecture v0.4 appendix D."
  assume_role_policy = data.aws_iam_policy_document.backup_assume.json
}

# Appendix D: "EBS snapshot create and delete within the plan". AWS's own managed
# policy for a backup role is what grants that; it also covers resource types this
# plan never selects, which is the cost of using the policy AWS keeps current
# instead of a hand-written one that breaks the day AWS Backup adds a required
# action.
#
# Restore is NOT granted here. The week 10 restore drill (section 6, Backups) is
# run by a person, and if that drill is done through AWS Backup rather than by
# creating a volume from the snapshot directly, it needs
# AWSBackupServiceRolePolicyForRestores attached to this role. That is a decision
# for the week it happens, not a standing permission to restore over a live volume.
resource "aws_iam_role_policy_attachment" "backup" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}
