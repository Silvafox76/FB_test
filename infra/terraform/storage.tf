# The object store and the image registry. Architecture v0.4 section 6, Object
# store and Containers.
#
# ONE BUCKET, THREE KINDS OF OBJECT, AND ONLY ONE OF THEM IS THE RECORD.
#
#   raw/        raw pages and documents keyed by content hash. Evidence: what a
#               source actually served, re-fetchable in principle and the thing a
#               back-test reads. Archived at 180 days.
#   documents/  attachments read with Textract. Same character, same rule.
#   exports/    export batches and their manifests. EXEMPT from the lifecycle rule.
#               This is the only way anything leaves the system (rule 16), and a
#               person who needs a batch back needs it now, not after a restore.
#   backups/    the weekly pg_dump, kept 90 days and then expired.
#
# The lifecycle rules below are written as an INCLUSION list of prefixes to
# archive, never as one blanket rule. S3 lifecycle filters have no NOT: there is no
# way to say "everything except exports/". A single rule with an empty prefix -
# which is what "archive the bucket at 180 days" looks like when written the
# obvious way - would swallow the export batches too, and the failure is silent: a
# batch stays listed, stays downloadable, and only the retrieval cost and class
# change. Getting this backwards archives the one thing a person needs to fetch
# back and leaves untouched the raw pages nobody reads twice.
#
# variables.tf enforces the same thing structurally: `archive_prefixes` refuses a
# value starting with exports/ and refuses an empty prefix.

resource "aws_s3_bucket" "raw" {
  bucket = var.raw_bucket_name

  tags = {
    Name = var.raw_bucket_name
  }
}

# Versioning ON (section 6). It is what makes an overwrite recoverable, and with
# content-hash keys an overwrite should never happen - so a noncurrent version
# appearing at all is a signal worth looking at. Nothing expires noncurrent
# versions: this is an evidence store, and evidence that deletes itself on a
# schedule nobody set is not evidence.
resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id

  versioning_configuration {
    status = "Enabled"
  }
}

# SSE-S3 rather than a customer-managed KMS key. Section 6 says "encrypt it" and
# stops there. AES256 is server-side encryption with no key policy to get wrong and
# no second place where access can be granted or revoked; a CMK would add a key
# policy that has to agree with the instance role's bucket policy, and two policies
# that must agree is how an object becomes unreadable during a restore drill. The
# bucket holds public notice text and export CSVs, not personal data (rule 19), so
# the extra key control buys little here.
resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# All four switches on. Nothing in this system is public, and rule 17's "no inbound
# network path" would be a strange rule to hold while a bucket served the same
# content to anyone who guessed the name.
resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "raw_bucket" {
  # S3 accepts plain HTTP. Every client here speaks TLS already (the security group
  # allows 443 out, and port 80 exists for portals rather than for AWS), so this
  # statement costs nothing and closes the case where a future script gets the
  # scheme wrong and ships an export batch in clear text.
  statement {
    sid    = "DenyNonTLSRequests"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.raw.arn,
      "${aws_s3_bucket.raw.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = data.aws_iam_policy_document.raw_bucket.json

  # A bucket policy that denies can lock out the account's own callers if it lands
  # before the public access block; ordering them makes the apply deterministic.
  depends_on = [aws_s3_bucket_public_access_block.raw]
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  # One rule per archived prefix. GLACIER_IR - Instant Retrieval - and not Flexible
  # or Deep Archive: a back-test that needs a year-old raw page needs it in the same
  # session, not in twelve hours (section 6 names Instant Retrieval explicitly).
  dynamic "rule" {
    for_each = toset(var.archive_prefixes)

    content {
      id     = "archive-${trimsuffix(rule.value, "/")}"
      status = "Enabled"

      filter {
        prefix = rule.value
      }

      transition {
        days          = var.glacier_transition_days
        storage_class = "GLACIER_IR"
      }
    }
  }

  # The weekly pg_dump expires; it does not move to Glacier. A dump older than 90
  # days is superseded by every dump taken since, so archiving it would be paying
  # to keep something nobody would restore.
  rule {
    id     = "expire-database-dumps"
    status = "Enabled"

    filter {
      prefix = var.db_backup_prefix
    }

    expiration {
      days = var.db_backup_retention_days
    }
  }

  # Not an object rule: this cleans up the parts of uploads that failed halfway.
  # They are billed, they are invisible in the console's object list, and with a
  # bucket this quiet nobody would ever notice them. It cannot touch a completed
  # object, which is why an unfiltered rule is safe here and nowhere else in this
  # file. Seven days is a literal rather than a variable on the same reasoning
  # monitor/connectors/worldbank.py uses for its country-name table: rule 6 puts
  # thresholds and weights in config because a person tunes them, and nobody tunes
  # how long a half-finished upload is kept.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.raw]
}

# ---------------------------------------------------------------------------
# Image registry
# ---------------------------------------------------------------------------

# "images in ECR" (section 6). GitHub Actions builds and pushes with the OIDC role
# in identity.tf; the host pulls with the instance profile.
#
# Tags are MUTABLE because step 12's deploy workflow does not exist yet -
# .github/workflows/ci.yml runs tests and nothing else - so the tagging scheme is
# not settled. IMMUTABLE would be the better setting the moment that workflow tags
# by commit SHA, and it is a one-line change then. Choosing it now would mean a
# workflow that pushes a moving tag fails on its second run with an error nobody
# would connect to this file.
resource "aws_ecr_repository" "images" {
  for_each = toset(var.ecr_repositories)

  name                 = "${var.project}/${each.value}"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "${var.project}/${each.value}"
  }
}
