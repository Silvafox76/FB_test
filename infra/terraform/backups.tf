# Backups. Architecture v0.4 section 6: "AWS Backup: daily EBS snapshot, 7-day
# retention; weekly pg_dump to S3, 90-day retention. Restore drill in week 10."
#
# TWO BACKUPS THAT ARE NOT THE SAME BACKUP, which is why the document names both:
#
#   The EBS snapshot is the HOST. It restores a machine - the containers, the
#   Postgres data directory, the raw payload tree, the checkout - to yesterday.
#   Seven days of them, and it is what a person restores after losing the instance.
#
#   The weekly pg_dump is the DATABASE, in a form that can be read without the
#   host: a file in S3 (the backups/ prefix in storage.tf, 90-day expiry) that can
#   be loaded into a fresh Postgres or opened on a laptop. It is what answers "what
#   did the queue look like in week 4" at the week 14 gate, which a block-level
#   snapshot of a dead instance cannot.
#
# ONLY THE FIRST OF THE TWO IS IN THIS FILE. The pg_dump is a scheduled command on
# the host - a systemd timer beside the ones in deploy/systemd/ - and Terraform's
# part of it is the lifecycle rule that expires it at 90 days. Writing a Lambda or
# an SSM Association here to run the dump would put the pipeline's schedule in two
# places (deploy/README.md is explicit that the registry and the host's timers own
# scheduling), and the one that was wrong would be the one nobody reads.

resource "aws_backup_vault" "this" {
  name = "${var.project}-vault"

  tags = {
    Name = "${var.project}-vault"
  }
}

resource "aws_backup_plan" "daily" {
  name = "${var.project}-daily-ebs"

  rule {
    rule_name         = "daily-ebs-snapshot"
    target_vault_name = aws_backup_vault.this.name
    schedule          = var.ebs_backup_cron

    # How long the job may wait for a window and how long it may run. Both are
    # AWS Backup defaults made explicit: a snapshot that silently never started is
    # the failure mode a backup plan has, and these are the numbers that decide
    # whether it is reported as expired rather than pending forever.
    start_window      = 60
    completion_window = 180

    lifecycle {
      delete_after = var.ebs_backup_retention_days
    }
  }
}

# Selection by tag, not by ARN. A rebuild gives the instance a new id; a plan that
# named the ARN would keep pointing at a volume that no longer exists and would
# report nothing wrong while backing up nothing. compute.tf tags the instance
# backup = daily.
resource "aws_backup_selection" "tagged" {
  name         = "${var.project}-tagged-daily"
  iam_role_arn = aws_iam_role.backup.arn
  plan_id      = aws_backup_plan.daily.id

  selection_tag {
    type  = "STRINGEQUALS"
    key   = "backup"
    value = "daily"
  }

  depends_on = [aws_iam_role_policy_attachment.backup]
}
