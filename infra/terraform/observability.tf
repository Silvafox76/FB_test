# The seven alarms, one SNS topic, one email. Architecture v0.4 section 6,
# Observability.
#
# READ THIS BEFORE TRUSTING ANY OF THESE ALARMS.
#
# An alarm can only fire on a CloudWatch metric. Five of the seven alarms below are
# not about EC2 at all - they are facts held in Postgres, in the `source_health`,
# `model_calls`, `translations` and `approved_records` tables - and a sixth reads a
# metric only the CloudWatch agent produces. So:
#
#   CPUCreditBalance                 AWS/EC2 publishes it. This alarm works today.
#   disk_used_percent                CWAgent namespace. THE CLOUDWATCH AGENT IS NOT
#                                    INSTALLED by this workspace (user_data.sh says
#                                    what it does and does not do), so nothing
#                                    publishes this yet.
#   three consecutive source failures
#   zero-yield anomaly
#   translation schema failure rate
#   export backlog older than seven days
#   the daily model cap              All five are application facts. NOTHING
#                                    PUBLISHES THEM. They are declared against the
#                                    custom namespace in var.metric_namespace and
#                                    sit in INSUFFICIENT_DATA until something does.
#
# WHICH MODULE WOULD HAVE TO PUBLISH THEM: `monitor/health/metrics.py`. CLAUDE.md's
# layout already names it - `health/ source_health.py, metrics.py` - and it is the
# one file in that layout that has never been written. It would read the four
# tables and call PutMetricData into var.metric_namespace at the end of each pass;
# the instance role already permits exactly that call in exactly that namespace and
# nothing else (identity.tf).
#
# One consequence of rule 11 that whoever writes it will meet immediately: the
# export backlog lives in `approved_records`, and `monitor_pipeline` has NO
# privilege on that table. So the metrics publisher cannot be a step inside the
# pipeline's own connection. It reads as `monitor_readonly`, which is the role that
# exists for reporting and has select on everything.
#
# These alarms are declared now rather than when the publisher lands, on purpose.
# An alarm in INSUFFICIENT_DATA is a visible, checkable statement that nobody is
# watching this yet. An alarm that was silently dropped from the Terraform because
# the metric did not exist is indistinguishable, six weeks later, from an alarm
# that has simply never fired.
#
# AND ON RULE 18, because these are emails and rule 18 says nothing is notified:
# rule 18 governs the APPLICATION. No code path in monitor/ or review/ mails
# anyone, pages anyone or posts anywhere; the reviewer works the queue on a
# schedule they set. These alarms are AWS-side operational alerts about a host and
# a budget, addressed to whoever runs the host, and they are required by section 6.
# Nothing in the pipeline can cause one to be sent except by failing.

resource "aws_sns_topic" "alerts" {
  name         = "${var.project}-alerts"
  display_name = "FB Opportunity Monitor alerts"
}

data "aws_iam_policy_document" "alerts" {
  # CloudWatch alarms in this account may publish. This is narrower than the
  # default topic policy, which permits every principal in the account.
  statement {
    sid     = "AllowCloudWatchAlarms"
    effect  = "Allow"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    resources = [aws_sns_topic.alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  # Budgets needs an explicit grant; without this statement the budget in
  # account.tf is created, evaluates correctly and delivers nothing.
  statement {
    sid     = "AllowBudgets"
    effect  = "Allow"
    actions = ["SNS:Publish"]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    resources = [aws_sns_topic.alerts.arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts.json
}

# One topic, one subscription, email (section 6). AWS sends a confirmation mail and
# the subscription stays pending until a person clicks the link in it - so a green
# apply is NOT evidence that an alarm can reach anyone. Confirming it is a step in
# the bring-up, and `aws sns list-subscriptions-by-topic` is how to check.
resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---------------------------------------------------------------------------
# Log groups
# ---------------------------------------------------------------------------

# Session Manager transcripts (section 6, Access: "Every session is logged"). The
# SSM document in compute.tf streams into this group; CloudTrail separately records
# that a session started and who started it.
resource "aws_cloudwatch_log_group" "session_manager" {
  name              = "${var.log_group_prefix}/session-manager"
  retention_in_days = var.log_retention_days
}

# Where the host's structlog JSON would land. Created with a retention period so
# the group cannot quietly become the thing that fills the disk's replacement - but
# like the disk metric, it is the CloudWatch agent that would ship into it, and the
# agent is not installed. Empty until then, and that is the honest state.
resource "aws_cloudwatch_log_group" "host" {
  name              = "${var.log_group_prefix}/host"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------------------
# The seven alarms
# ---------------------------------------------------------------------------

# Every THRESHOLD below is a variable, because each one is a number that exists
# somewhere else in the repository or in a document and has to be able to move.
# Every PERIOD and evaluation count is a literal, because those are alarm mechanics
# - how long to look and how many datapoints to believe - and nobody tunes them
# against a document.
#
# treat_missing_data is "missing" on every alarm here, which keeps an unpublished
# metric in INSUFFICIENT_DATA rather than in ALARM. "breaching" would mail the
# operator every period from the moment of apply for five metrics nobody publishes,
# and an inbox that is trained to ignore this topic is worse than no topic.

# 1. Three consecutive failures on one source. Matches max_consecutive_failures in
# every sources/*.yaml (all fourteen carry 3) and the `unhealthy` transition in
# monitor/health/source_health.py.
#
# ONE ALARM, NOT ONE PER SOURCE, and the reason is rule 6: the source list lives in
# sources/*.yaml and the registry owns it. Generating an alarm per source would
# copy that list into Terraform, where it would be wrong within a week of the next
# source landing. The metric is therefore the maximum consecutive-failure count
# across all sources, and `monitor status` is what names which one.
resource "aws_cloudwatch_metric_alarm" "source_consecutive_failures" {
  alarm_name        = "${var.project}-source-consecutive-failures"
  alarm_description = "A source has failed ${var.alarm_thresholds.source_consecutive_failures} runs in a row and is unhealthy. Which source: `monitor status`. Published by monitor/health/metrics.py, which does not exist yet."

  namespace           = var.metric_namespace
  metric_name         = "SourceConsecutiveFailures"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.alarm_thresholds.source_consecutive_failures
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 2. Zero yield on a source that normally yields. Rule 4 calls this a failure
# state, not an empty success: a portal that silently changes its markup returns
# HTTP 200 and nothing, and without this the morning report is clean while the
# pipeline sees nothing. Threshold 1 - the first occurrence is worth an email;
# source_health only reaches `watch` at 2.
resource "aws_cloudwatch_metric_alarm" "zero_yield" {
  alarm_name        = "${var.project}-zero-yield"
  alarm_description = "A source that normally yields returned nothing (rule 4). Published by monitor/health/metrics.py, which does not exist yet."

  namespace           = var.metric_namespace
  metric_name         = "ZeroYieldRuns"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.alarm_thresholds.zero_yield_runs
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 3. CPU credit balance. The one alarm here that works on the day of apply: AWS/EC2
# publishes CPUCreditBalance for a t3 every five minutes. In standard credit mode
# an exhausted balance throttles the instance to its baseline, so this is the early
# warning that a run is about to take hours instead of minutes - and the signal
# that section 6's "t3.xlarge if the wave-2 window exceeds two hours" has arrived.
# Three periods rather than one: a single dip during a fetch burst is normal.
resource "aws_cloudwatch_metric_alarm" "cpu_credit_balance" {
  alarm_name        = "${var.project}-cpu-credit-balance"
  alarm_description = "CPU credits below ${var.alarm_thresholds.cpu_credit_balance}. Standard credit mode means the run slows rather than the bill rising; consider the t3.xlarge escalation in Architecture v0.4 section 6."

  namespace           = "AWS/EC2"
  metric_name         = "CPUCreditBalance"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  threshold           = var.alarm_thresholds.cpu_credit_balance
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = aws_instance.monitor.id
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 4. Disk above 80 percent of the 100 GB volume. NOT an EC2 metric - EC2 publishes
# nothing about a filesystem it cannot see inside. This reads the CloudWatch
# agent's `disk_used_percent`, with the dimensions the agent emits when it is
# configured with append_dimensions InstanceId and the "/" resource. The agent is
# not installed by this workspace, so this alarm is INSUFFICIENT_DATA until it is,
# and the dimensions below are the contract that agent configuration has to match.
resource "aws_cloudwatch_metric_alarm" "disk_used_percent" {
  alarm_name        = "${var.project}-disk-used-percent"
  alarm_description = "Root volume above ${var.alarm_thresholds.disk_used_percent} percent. Requires the CloudWatch agent, which is not installed by infra/terraform."

  namespace           = var.cwagent_namespace
  metric_name         = "disk_used_percent"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = var.alarm_thresholds.disk_used_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = aws_instance.monitor.id
    path       = "/"
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 5. Translation schema failure rate above 2 percent. BUILD_ORDER step 14 accepts
# schema validity above 98 percent, so this alarm and that acceptance test are the
# same number seen from opposite sides. A rising rate means the translator's strict
# JSON contract is breaking - usually a body long enough to truncate the response
# mid-token - and every failure parks a notice.
resource "aws_cloudwatch_metric_alarm" "translation_schema_failures" {
  alarm_name        = "${var.project}-translation-schema-failure-rate"
  alarm_description = "Translation schema failures above ${var.alarm_thresholds.translation_schema_failure_pct} percent (BUILD_ORDER step 14 accepts 98 percent validity). Published by monitor/health/metrics.py, which does not exist yet."

  namespace           = var.metric_namespace
  metric_name         = "TranslationSchemaFailureRate"
  statistic           = "Average"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.alarm_thresholds.translation_schema_failure_pct
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 6. Export backlog older than seven days. An approved record that has not left the
# system is work a reviewer has already done that nobody can act on; under D31 the
# export file is the only way out (rule 16), so a backlog is not a queue, it is a
# stall. BUILD_ORDER step 16 asks for this alarm by name.
resource "aws_cloudwatch_metric_alarm" "export_backlog" {
  alarm_name        = "${var.project}-export-backlog-age"
  alarm_description = "Oldest approved, unexported record is more than ${var.alarm_thresholds.export_backlog_days} days old (BUILD_ORDER step 16). Published by monitor/health/metrics.py, which does not exist yet and must read approved_records as monitor_readonly (rule 11)."

  namespace           = var.metric_namespace
  metric_name         = "ExportBacklogAgeDays"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.alarm_thresholds.export_backlog_days
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# 7. The daily model cap (rule 22).
#
# THE THRESHOLD IS 2,000, NOT 600. config/thresholds.yaml was raised from 600 to
# 2,000 on 2026-09-12: one day of TED alone needed 1,132 translate calls, so a
# single source ran 532 calls over the old cap and the backlog grew every day.
# Architecture v0.4's "Bedrock quota sized for 600 calls a day" is stale - the
# document has not caught up with the amended rule 22, and the Service Quotas
# request for bedrock InvokeModel in ca-central-1 (which is a person's request, not
# a Terraform resource) must be sized against 2,000.
#
# Reaching the cap is not a cost problem - 2,000 Haiku calls of this shape is about
# USD 8, well inside the USD 25 daily cap that actually binds. It means the run
# STOPPED before it finished, and the notices it did not read are still waiting.
resource "aws_cloudwatch_metric_alarm" "daily_model_calls" {
  alarm_name        = "${var.project}-daily-model-call-cap"
  alarm_description = "Model calls today reached the rule 22 cap of ${var.alarm_thresholds.daily_model_calls}; the run stopped before it finished. Published by monitor/health/metrics.py, which does not exist yet."

  namespace           = var.metric_namespace
  metric_name         = "ModelCallsToday"
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  threshold           = var.alarm_thresholds.daily_model_calls
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "missing"

  alarm_actions = [aws_sns_topic.alerts.arn]
}

# No ok_actions on any of the seven. An "it is better now" email for every flap is
# how a topic becomes noise, and the state is on the alarm itself for anyone who
# looks. The USD 25 daily cost cap has no alarm of its own here: section 6 asks for
# seven alarms and these are the seven. If the cost cap should be the eighth, that
# is a line in the architecture document and then a resource here.
