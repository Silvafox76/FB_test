# Secrets Manager entries. Architecture v0.4 section 6, Secrets: "model API key,
# portal credentials per source. Read by the instance role only. No CRM secret
# exists to leak."
#
# THE SECRET EXISTS HERE. THE VALUE NEVER DOES.
#
# Every resource below is an `aws_secretsmanager_secret` - a name, a description
# and nothing else. There is deliberately no `aws_secretsmanager_secret_version`
# anywhere in this workspace, and adding one would be blocking under rule 20 even
# though it would look like a convenience. Two reasons, and the second is the one
# people forget:
#
#   1. The value would be in a .tf or .tfvars file, which is a file in a git
#      repository or on somebody's laptop.
#   2. Terraform state holds every attribute of every resource in clear text. A
#      secret version's value is an attribute. It would be in the state object in
#      S3, in every prior version of that object the bucket keeps, and in any local
#      plan file anyone ever wrote to disk - forever, and unredacted. versions.tf
#      says what that implies for who may read the state bucket.
#
# HOW THE VALUE ACTUALLY GETS IN, since "out of band" is not an instruction:
#
#   aws secretsmanager put-secret-value \
#     --secret-id fb-opp-monitor/model-api-key \
#     --secret-string file:///dev/stdin      # paste, then Ctrl-D
#
# run once by a named person from their own session, with the value never on a
# command line (where it would land in shell history and in CloudTrail's request
# parameters). The host then reads it at deploy time to render
# /etc/monitor/monitor.env, which deploy/README.md describes as the one file with
# credentials in it - outside the checkout, 0600, owned by the service account.
#
# NOTE THE GAP RATHER THAN FILLING IT: the three database role passwords
# (MONITOR_PIPELINE_PASSWORD, MONITOR_REVIEW_PASSWORD, MONITOR_READONLY_PASSWORD in
# .env.example) are credentials this host needs and section 6's secrets list does
# not mention. They are not created here, because inventing a secret the
# architecture does not name is how a deployment acquires a credential nobody
# decided to have. If they should live in Secrets Manager rather than being typed
# into the env file on the host, that is one line per password in
# terraform.tfvars - the `secrets` variable is a map for exactly this reason - and
# a sentence in the architecture document.
resource "aws_secretsmanager_secret" "this" {
  for_each = var.secrets

  name        = "${var.project}/${each.key}"
  description = each.value

  # recovery_window_in_days is left at the AWS default of 30 days, and there is a
  # rebuild hazard in that worth knowing before it is met at speed: a destroyed
  # secret is not gone, it is scheduled for deletion, and re-creating one with the
  # same name inside the window fails. So a `terraform destroy` followed by a
  # rebuild - the exact thing section 6's "rebuild in under an hour" invites
  # someone to practise - needs `aws secretsmanager restore-secret` first, or it
  # stops here. The alternative, a zero-day window, would make an accidental
  # destroy permanently delete the value, which is a worse failure than a
  # documented extra command.

  tags = {
    Name = "${var.project}/${each.key}"
  }
}
