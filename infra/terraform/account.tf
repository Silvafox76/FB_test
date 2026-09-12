# Account-level guardrails: the audit trail and the budget. Architecture v0.4
# section 6, Account: "member account fb-opp-monitor; budget alert at USD 300 a
# month; CloudTrail on. A dedicated account keeps cost, IAM and blast radius
# separate from delivery workloads (D13)."
#
# Both of these are answers to the same question - "what is happening in this
# account, and what is it costing" - which a dedicated member account makes
# answerable at all. In a shared account the USD 300 would be noise in someone
# else's bill and the trail would be someone else's haystack.

# ---------------------------------------------------------------------------
# CloudTrail
# ---------------------------------------------------------------------------

# A SEPARATE BUCKET FROM fb-opp-monitor-raw, and not out of tidiness. The instance
# role has s3:PutObject on every key in the raw bucket (identity.tf). If the trail
# wrote there, the host could write into its own audit log - and the audit log is
# most valuable in exactly the case where the host is doing something it should
# not. Two buckets means the host has no permission on the trail at all.
resource "aws_s3_bucket" "cloudtrail" {
  bucket = "${var.project}-cloudtrail"

  tags = {
    Name = "${var.project}-cloudtrail"
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id

  rule {
    id     = "expire-trail-objects"
    status = "Enabled"

    filter {}

    expiration {
      days = var.cloudtrail_retention_days
    }

    # Versioning is on for the same reason as the raw bucket - an overwrite should
    # be recoverable - so noncurrent versions need their own expiry or they
    # outlive the objects they are versions of.
    noncurrent_version_expiration {
      noncurrent_days = var.cloudtrail_retention_days
    }
  }

  depends_on = [aws_s3_bucket_versioning.cloudtrail]
}

# The trail ARN is CONSTRUCTED here rather than read from aws_cloudtrail.this.arn,
# and that is not a style choice: the trail cannot be created until the bucket
# policy admits it, and the bucket policy conditions on the trail ARN. Referencing
# the resource would be a dependency cycle. The ARN of a trail is fully determined
# by partition, region, account and name, so building it is exact rather than a
# guess.
locals {
  cloudtrail_arn = "arn:${data.aws_partition.current.partition}:cloudtrail:${var.aws_region}:${data.aws_caller_identity.current.account_id}:trail/${var.project}"
}

data "aws_iam_policy_document" "cloudtrail_bucket" {
  statement {
    sid     = "AWSCloudTrailAclCheck"
    effect  = "Allow"
    actions = ["s3:GetBucketAcl"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    resources = [aws_s3_bucket.cloudtrail.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.cloudtrail_arn]
    }
  }

  statement {
    sid     = "AWSCloudTrailWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }

    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceArn"
      values   = [local.cloudtrail_arn]
    }
  }

  statement {
    sid    = "DenyNonTLSRequests"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.cloudtrail.arn,
      "${aws_s3_bucket.cloudtrail.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.cloudtrail]
}

# Management events only. Data events - every S3 GetObject and PutObject the
# pipeline makes - would be tens of thousands of records a day describing a host
# reading its own bucket, at a cost per event, to answer a question nobody has.
# What this trail is for is the other kind of question: who changed an IAM policy,
# who read a secret, who started a Session Manager session and when.
#
# log_file_validation matters here more than usual: an audit trail nobody can prove
# is unmodified is an audit trail that loses the argument it exists to win.
resource "aws_cloudtrail" "this" {
  name                          = var.project
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true

  depends_on = [aws_s3_bucket_policy.cloudtrail]
}

# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

# USD 300 a month across the whole member account (section 6). No cost filter: in a
# dedicated account every dollar is this pilot's, which is the point of D13.
#
# One notification, on ACTUAL spend at 100 percent. A forecast alert would fire in
# the first days of the first month - AWS forecasts from a partial month and a
# rebuild drill looks like runaway spend to it - and an alert that cries wolf in
# week one is an alert nobody reads in week ten. A forecast threshold is a decision
# someone can take later, on evidence.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.alerts]
}
