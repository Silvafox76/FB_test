# What a person needs after an apply, and nothing a person should not have.
#
# No output is marked sensitive because none of these values is a secret: they are
# identifiers, ARNs and two commands. The secret ARNs are included on purpose - an
# ARN is the thing you need in order to PUT a value into a secret, and the value
# itself is never in Terraform at all (secrets.tf).

output "instance_id" {
  description = "The pilot host. Also the target of every `aws ssm` command below."
  value       = aws_instance.monitor.id
}

output "public_ip" {
  description = "The Elastic IP. Nothing listens on it - there is no ingress rule anywhere (rule 17) - this is the source address portals see, and the one to give a portal operator who asks what to allowlist."
  value       = aws_eip.monitor.public_ip
}

output "ami_id" {
  description = "The Ubuntu 24.04 image this host was launched from, resolved from Canonical's AMIs at plan time. Worth recording: a rebuild a month from now will resolve a newer one."
  value       = data.aws_ami.ubuntu.id
}

output "raw_bucket" {
  description = "The one object store. exports/ is exempt from the Glacier lifecycle rule; raw/ and documents/ are not."
  value       = aws_s3_bucket.raw.bucket
}

output "ecr_repository_urls" {
  description = "Where the deploy workflow pushes images and the host pulls them."
  value       = { for name, repository in aws_ecr_repository.images : name => repository.repository_url }
}

output "alerts_topic_arn" {
  description = "The one SNS topic. The email subscription is PENDING until a person clicks the confirmation link - until then every alarm delivers nothing."
  value       = aws_sns_topic.alerts.arn
}

output "secret_arns" {
  description = "The secrets that exist and are empty. Set each value out of band with `aws secretsmanager put-secret-value`; secrets.tf says why never from here."
  value       = { for name, secret in aws_secretsmanager_secret.this : name => secret.arn }
}

output "instance_role_arn" {
  description = "ec2-monitor-role (appendix D). The review app runs under this same identity; it has none of its own."
  value       = aws_iam_role.monitor.arn
}

output "github_deploy_role_arn" {
  description = "The role the GitHub Actions workflow assumes over OIDC. Paste into the workflow's aws-actions/configure-aws-credentials step; there is no access key to copy anywhere."
  value       = aws_iam_role.github_deploy.arn
}

output "backup_vault_name" {
  description = "Where the daily EBS snapshots land, 7-day retention. The week 10 restore drill starts here."
  value       = aws_backup_vault.this.name
}

output "session_command" {
  description = "Open a shell on the host. No SSH port exists; this rides the SSM agent's outbound connection."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.monitor.id}"
}

output "review_port_forward_command" {
  description = "How the reviewer reaches the review app (Architecture v0.4 section 6, Access). Run it, then open http://127.0.0.1:8080 - the app binds to localhost on the host and is never exposed."
  value       = "aws ssm start-session --region ${var.aws_region} --target ${aws_instance.monitor.id} --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8080\"],\"localPortNumber\":[\"8080\"]}'"
}
