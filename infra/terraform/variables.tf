# Every knob in the deployment, with the document section each default comes from.
#
# Rule 6 is why this file is long: an instance type, a bucket name, a retention
# period or an alarm threshold written into a resource is a value nobody can find
# and nobody can change without reading Terraform. Here each one has a name, a
# default and the sentence that justifies the default. A resource elsewhere in this
# workspace that carries a bare number or a bare string is a finding, with two
# stated exceptions: the backend block in versions.tf (Terraform evaluates it
# before variables exist) and AWS's own fixed identifiers - service principals,
# Canonical's AWS account id, the GitHub OIDC issuer URL - which are facts about
# AWS and GitHub, not settings.

variable "project" {
  description = "Name prefix for every resource, and the value of the `project` tag. Architecture v0.4 section 6 names the member account fb-opp-monitor."
  type        = string
  default     = "fb-opp-monitor"
}

variable "aws_region" {
  description = <<-EOT
    The one region. ca-central-1 (Montreal), Architecture v0.4 section 6.
    D1 and D19 require confirming Haiku 4.5 and Sonnet 5 are available in-region
    BEFORE the Bedrock cutover; that confirmation is a person's check against the
    Bedrock console, not something Terraform can assert (see identity.tf).
  EOT
  type        = string
  default     = "ca-central-1"
}

variable "availability_zone" {
  description = "Which default subnet the host lands in. One AZ: there is one host and no load balancer, so spreading across AZs would buy nothing and cost a second subnet to reason about."
  type        = string
  default     = "ca-central-1a"
}

variable "tags" {
  description = "Extra tags merged into default_tags on every resource. Empty by default; cost-centre or owner tags go here rather than into individual resources."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

variable "instance_type" {
  description = <<-EOT
    t3.large (2 vCPU, 8 GiB), Architecture v0.4 section 6. The document's own
    escalation: t3.xlarge if the wave-2 window exceeds two hours. That is a
    measurement someone takes and then changes here - it is not something the
    deployment decides for itself (rule 1).
  EOT
  type        = string
  default     = "t3.large"
}

variable "cpu_credits" {
  description = <<-EOT
    STANDARD, not unlimited, and the reason is in Architecture v0.4 section 6:
    "Standard mode means an exhausted credit balance slows the run rather than
    raising the bill." A burst-credit surcharge is a bill nobody approved; a slow
    run is visible in `monitor status` and in the CPUCreditBalance alarm.
  EOT
  type        = string
  default     = "standard"

  validation {
    condition     = var.cpu_credits == "standard"
    error_message = "Credit mode is standard by decision (Architecture v0.4 section 6). Setting unlimited turns an exhausted credit balance into a surcharge instead of a slower run; if that is genuinely wanted, change the decision in the document first."
  }
}

variable "root_volume_gb" {
  description = "100 GB gp3, Architecture v0.4 section 6. It holds the Postgres container's data directory, the raw payload tree and the export batches, so the 80-percent disk alarm in observability.tf is measured against this."
  type        = number
  default     = 100
}

variable "root_volume_type" {
  description = "gp3. Baseline 3,000 IOPS is well above what one Postgres container and a few hundred fetches a day ask for, and gp3 is cheaper than gp2 at this size."
  type        = string
  default     = "gp3"
}

variable "ubuntu_release" {
  description = "Ubuntu 24.04 LTS (noble), Architecture v0.4 section 6. Used in the AMI name filter in compute.tf, which resolves the current image at plan time rather than pinning an AMI id that is region- and date-specific."
  type        = string
  default     = "noble-24.04"
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

variable "egress_rules" {
  description = <<-EOT
    The complete egress surface. Architecture v0.4 section 6: "egress on 443 and 80
    only". There are no ingress rules anywhere in this workspace (network.tf says
    why at length), so this map is the whole of the host's network policy.

    Port 80 is not an oversight and must not be "tidied up": several West African
    portals still lack TLS, and they arrive at step 17. The description travels
    into the AWS console rule description so the next person to read the rule in
    the console sees the reason there too.
  EOT
  type = map(object({
    port        = number
    description = string
  }))
  default = {
    https = {
      port        = 443
      description = "Outbound HTTPS: portals, donor APIs, Bedrock, Secrets Manager, SSM, S3, ECR, CloudWatch."
    }
    http = {
      port        = 80
      description = "Outbound HTTP: several West African portals still have no TLS (Architecture v0.4 section 6). Not an oversight - do not remove."
    }
  }
}

# ---------------------------------------------------------------------------
# Object store
# ---------------------------------------------------------------------------

variable "raw_bucket_name" {
  description = "fb-opp-monitor-raw, Architecture v0.4 section 6. Globally unique across all AWS accounts, so a rebuild in a different account needs this changed or the create fails."
  type        = string
  default     = "fb-opp-monitor-raw"
}

variable "archive_prefixes" {
  description = <<-EOT
    The prefixes whose objects move to Glacier Instant Retrieval. Written as an
    inclusion list, never as a blanket rule with an exclusion, because S3 lifecycle
    filters cannot express "everything except exports/" - there is no NOT in a
    lifecycle filter. A blanket rule would therefore archive the export batches
    too, which is the one thing a person has to be able to fetch back (rule 16:
    the export is the only way out).

    Raw payloads are keyed by content hash under raw/; documents/ is where
    Textract-read attachments land. Both are re-fetchable evidence, not the record
    a person needs at short notice.
  EOT
  type        = list(string)
  default     = ["raw/", "documents/"]

  validation {
    condition     = alltrue([for p in var.archive_prefixes : endswith(p, "/")])
    error_message = "Each archive prefix must end with a slash, or the filter silently matches sibling keys that merely start with the same letters (raw-backup/ would match raw)."
  }

  validation {
    condition     = !anytrue([for p in var.archive_prefixes : startswith(p, "exports/") || p == ""])
    error_message = "exports/ is exempt from the lifecycle rule (Architecture v0.4 section 6) and an empty prefix matches the whole bucket, which includes exports/. Export batches and manifests are kept whole and retrievable."
  }
}

variable "glacier_transition_days" {
  description = "180 days to Glacier Instant Retrieval, Architecture v0.4 section 6. Instant Retrieval rather than Flexible or Deep Archive: a back-test that needs a year-old raw page needs it in the same session, not in twelve hours."
  type        = number
  default     = 180
}

variable "db_backup_prefix" {
  description = "Where the weekly pg_dump lands. Architecture v0.4 section 6 gives it 90-day retention, which is a different lifecycle from both the raw tree and the exports, so it is its own prefix."
  type        = string
  default     = "backups/"
}

variable "db_backup_retention_days" {
  description = "90 days for the weekly pg_dump, Architecture v0.4 section 6. Expiry, not transition: a dump older than 90 days is superseded by every dump since."
  type        = number
  default     = 90
}

variable "ecr_repositories" {
  description = <<-EOT
    Architecture v0.4 section 6: "images in ECR". Two repositories, because the
    repository builds two images and not four: one Dockerfile serves both the
    pipeline and the review service (they differ only in their command, see
    docker-compose.yml), and the Playwright browser image arrives at step 17 in its
    own container. postgres:16 with pgvector is pulled from Docker Hub and is not
    mirrored here - mirroring it would be a second source for the same artefact.
  EOT
  type        = list(string)
  default     = ["monitor", "monitor-browser"]
}

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

variable "bedrock_model_ids" {
  description = <<-EOT
    The two models named in appendix D, as Bedrock model ids. Bedrock ids carry the
    `anthropic.` prefix that the first-party ids do not: config/thresholds.yaml
    prices `claude-haiku-4-5` and `claude-sonnet-5`, and the same two models are
    `anthropic.claude-haiku-4-5` and `anthropic.claude-sonnet-5` here.

    These become foundation-model ARNs in identity.tf and nothing else. Two ids,
    two ARNs, no wildcard: bedrock:InvokeModel on `foundation-model/*` would let a
    prompt-version bump quietly move the pilot onto a model nobody costed.
  EOT
  type        = list(string)
  default     = ["anthropic.claude-haiku-4-5", "anthropic.claude-sonnet-5"]
}

variable "secrets" {
  description = <<-EOT
    Secrets Manager entries to create, as name suffix -> description. The VALUE of
    every one of these is set out of band and never appears in Terraform (rule 20,
    and secrets.tf spells out how they get there and why state would otherwise hold
    them in clear text).

    Architecture v0.4 section 6 lists "model API key, portal credentials per
    source". No wave-1 source needs a portal credential today - the EBRD tender
    archive behind a CAS login (sources/ebrd.yaml) is the first that would - so the
    portal entries are absent rather than invented, and terraform.tfvars.example
    shows the shape of one.
  EOT
  type        = map(string)
  default = {
    "model-api-key" = "Anthropic API key for MODEL_ROUTE=direct. The Bedrock route authenticates with the instance profile instead and reads nothing from here; the direct route stays available as the recorded decision for two weeks after cutover (BUILD_ORDER step 12)."
  }
}

variable "github_repository" {
  description = <<-EOT
    owner/repo of the GitHub repository allowed to assume the deploy role, e.g.
    FreeBalance/pfm-opportunity-monitor. No default on purpose: this is the whole
    of the trust boundary for a role that can push images and run commands on the
    host, and a plausible-looking default is exactly the kind of value that gets
    applied without being read. Step 12 also moves the repository to the
    FreeBalance organisation, so the value changes in that session.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "github_repository must be owner/repo with no protocol, no .git suffix and no branch (e.g. FreeBalance/pfm-opportunity-monitor)."
  }
}

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

variable "alarm_email" {
  description = <<-EOT
    The one address every alarm and the budget notification reaches, through the
    one SNS topic (Architecture v0.4 section 6). No default: a wrong address is an
    alarm that fires into nobody's inbox, which is worse than no alarm because it
    reads as covered.

    AWS sends a confirmation mail when this subscription is created and the
    subscription stays `pending confirmation` until a person clicks it. An
    unconfirmed subscription delivers nothing - `terraform apply` succeeding is not
    evidence that an alarm can reach anyone.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alarm_email))
    error_message = "alarm_email must be a single email address."
  }
}

variable "metric_namespace" {
  description = <<-EOT
    The CloudWatch namespace the application's own metrics would be published
    under. NOTHING PUBLISHES THEM TODAY - observability.tf names the module that
    would have to, and five of the seven alarms sit in INSUFFICIENT_DATA until it
    exists. Deliberately not "AWS/..." (a reserved prefix) and deliberately one
    namespace, so the instance role's PutMetricData grant can be conditioned on it.
  EOT
  type        = string
  default     = "FBOppMonitor"
}

variable "cwagent_namespace" {
  description = "Where the CloudWatch agent publishes host metrics. `CWAgent` is the agent's own default; the disk alarm reads it. The agent is not installed by this workspace - see observability.tf."
  type        = string
  default     = "CWAgent"
}

variable "log_group_prefix" {
  description = "Prefix for the workspace's CloudWatch log groups. Session Manager transcripts and the host's structlog output live under it, and the instance role's logs grant is scoped to it."
  type        = string
  default     = "/fb-opp-monitor"
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps log events. 90 days covers the 14-week pilot end to end, so a question asked at the week 14 gate can still be answered from week 1's logs. Architecture v0.4 does not set this number; it is chosen here and named so it can be argued with."
  type        = number
  default     = 90
}

variable "monthly_budget_usd" {
  description = "USD 300 a month, Architecture v0.4 section 6. The budget covers the whole member account, which is the point of a dedicated account (D13): this number is the pilot's bill and nothing else's."
  type        = number
  default     = 300
}

variable "alarm_thresholds" {
  description = <<-EOT
    The seven alarms' thresholds, Architecture v0.4 section 6. Each one is named
    rather than written into the alarm, because four of these seven numbers also
    exist somewhere else in the repository and the day they disagree is the day the
    alarm is lying:

      source_consecutive_failures  3, matching max_consecutive_failures in every
                                   sources/*.yaml (all 14 carry 3 today).
      zero_yield_runs              1. Rule 4: zero yield on a source that normally
                                   yields is a failure state, not an empty success,
                                   so the first one is worth an email. Two is what
                                   monitor/health/source_health.py calls `watch`.
      cpu_credit_balance           50 credits remaining on the t3.large.
      disk_used_percent            80 percent of the 100 GB volume.
      translation_schema_failure_pct
                                   2 percent, matching BUILD_ORDER step 14's
                                   acceptance of schema validity above 98 percent.
      export_backlog_days          7 days, matching BUILD_ORDER step 16's alarm on
                                   the oldest unexported approved record.
      daily_model_calls            2,000 - the CURRENT cap in
                                   config/thresholds.yaml, raised from 600 on
                                   2026-09-12 because one day of TED alone needed
                                   1,132 translate calls. Architecture v0.4's
                                   "sized for 600 calls a day" is stale; this
                                   number is the one rule 22 enforces, so it is the
                                   one the alarm and the Bedrock quota request use.
  EOT
  type = object({
    source_consecutive_failures    = number
    zero_yield_runs                = number
    cpu_credit_balance             = number
    disk_used_percent              = number
    translation_schema_failure_pct = number
    export_backlog_days            = number
    daily_model_calls              = number
  })
  default = {
    source_consecutive_failures    = 3
    zero_yield_runs                = 1
    cpu_credit_balance             = 50
    disk_used_percent              = 80
    translation_schema_failure_pct = 2
    export_backlog_days            = 7
    daily_model_calls              = 2000
  }
}

# ---------------------------------------------------------------------------
# Backups and audit
# ---------------------------------------------------------------------------

variable "ebs_backup_cron" {
  description = "Daily EBS snapshot, Architecture v0.4 section 6. 07:00 UTC is 03:00 in Montreal: after the overnight passes and before the working day, so a snapshot is never taken mid-fetch. AWS Backup cron expressions are six fields and always UTC."
  type        = string
  default     = "cron(0 7 * * ? *)"
}

variable "ebs_backup_retention_days" {
  description = "7-day retention on the daily EBS snapshot, Architecture v0.4 section 6. The weekly pg_dump in S3 is what covers anything older, with its own 90 days."
  type        = number
  default     = 7
}

variable "cloudtrail_retention_days" {
  description = "How long CloudTrail objects are kept before expiry. Architecture v0.4 section 6 says only \"CloudTrail on\" and sets no retention, so 365 days is chosen here: long enough that a question about who changed what in the pilot is still answerable a year later, short enough that the trail bucket does not grow without bound against a USD 300 budget."
  type        = number
  default     = 365
}
