# infra/terraform

One workspace, one region, one account: the pilot host and everything around it.
Architecture v0.4 section 6 (deployment) and appendix D (identity) are what this
builds; BUILD_ORDER step 12 is the step it belongs to.

Read the files in this order the first time: `network.tf` (which is mostly about
what is absent), `compute.tf`, `identity.tf`, then `storage.tf`,
`observability.tf`, `account.tf`, `backups.tf`. Every non-obvious decision names
the rule number, the document section or the measured fact behind it.

## Before the first `terraform init`

The state bucket has to exist before Terraform can store state in it, and Terraform
cannot create the bucket it stores its own state in. This is the one thing done by
hand, once, in the new member account:

```bash
aws s3api create-bucket --bucket fb-opp-monitor-tfstate \
  --region ca-central-1 --create-bucket-configuration LocationConstraint=ca-central-1
aws s3api put-bucket-versioning --bucket fb-opp-monitor-tfstate \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket fb-opp-monitor-tfstate \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket fb-opp-monitor-tfstate \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```

Versioning is not optional there: it is the only undo for a corrupted state file,
and it is also why read access to that bucket has to be held as tightly as the
secrets themselves — see **State** below.

```bash
cp terraform.tfvars.example terraform.tfvars   # two values: an email, a repo name
terraform init
terraform plan
terraform apply
```

## After the apply, before believing anything works

`terraform apply` returning green means AWS accepted the declarations. It does not
mean the system can reach a model, alert anyone, or run. Five things are a person's
job, in this order:

1. **Confirm the SNS email subscription.** AWS mails a link; until it is clicked
   the subscription is `PendingConfirmation` and all seven alarms deliver into
   nothing. `aws sns list-subscriptions-by-topic --topic-arn <alerts_topic_arn>`.
2. **Put the secret values in.** The secrets exist and are empty (this is rule 20,
   not an oversight):
   `aws secretsmanager put-secret-value --secret-id fb-opp-monitor/model-api-key --secret-string file:///dev/stdin`,
   then paste and Ctrl-D. Never on the command line — that lands in shell history
   and in CloudTrail's request parameters.
3. **Request Bedrock model access in ca-central-1**, for both models in
   `bedrock_model_ids`. It is per-account and per-model; the IAM grant in
   `identity.tf` does nothing without it, and an ungranted model answers
   `AccessDeniedException` no matter how correct the policy is. Confirm at the same
   time that Haiku 4.5 and Sonnet 5 are offered in-region at all (D1, D19) — if
   either is not, the cutover is a different decision, not a Terraform change.
4. **Size the InvokeModel quota against 2,000 calls a day, not 600.** Architecture
   v0.4 says "sized for 600 calls a day" and is stale: `config/thresholds.yaml` was
   raised to 2,000 on 2026-09-12 after one day of TED alone needed 1,132 translate
   calls. Service Quotas increases are a person's request, not a resource here.
5. **Deploy.** GitHub Actions assumes `github_deploy_role_arn` over OIDC, pushes to
   the ECR repositories in `ecr_repository_urls`, and runs the deploy with SSM Run
   Command. That workflow does not exist yet — `.github/workflows/ci.yml` runs
   tests only.

## What this workspace deliberately does not do

- **It does not publish five of the seven alarms' metrics.** Three consecutive
  source failures, zero yield, translation schema failure rate, export backlog age
  and the daily model cap are facts in Postgres, not CloudWatch metrics. They sit
  in `INSUFFICIENT_DATA` until `monitor/health/metrics.py` exists — a module
  CLAUDE.md's layout already names and nobody has written. It must read
  `approved_records` as `monitor_readonly`, because `monitor_pipeline` has no
  privilege on that table (rule 11). `observability.tf` says this at the alarms.
- **It does not install the CloudWatch agent**, so the disk alarm has no publisher
  either. The alarm's dimensions are the contract that agent configuration has to
  match when someone adds it.
- **It does not deploy the application.** `user_data.sh` creates the service
  account, the directories `deploy/README.md` standardises on, and Docker. Nothing
  else, and no credential.
- **It does not run the weekly `pg_dump`.** That is a timer on the host beside the
  ones in `deploy/systemd/`; Terraform's part is the 90-day expiry on the
  `backups/` prefix.
- **It does not touch `monitor/score/client.py`.** The Bedrock route is chosen by
  `MODEL_ROUTE`, by a person, and nothing here detects or falls back (rule 1).

## Rebuild in under an hour: the two things that will stop you

Section 6 sets rebuild-in-an-hour as the bar, which invites practising a destroy
and re-apply. Two resources do not come back cleanly:

- **Secrets.** A destroyed secret is *scheduled for deletion*, not gone, for 30
  days, and re-creating one with the same name inside that window fails.
  `aws secretsmanager restore-secret --secret-id fb-opp-monitor/model-api-key`
  first. (A zero-day recovery window would make an accidental destroy permanent,
  which is the worse failure — `secrets.tf` argues this.)
- **`SSM-SessionManagerRunShell`.** The name is reserved by AWS and there is one
  per account. If it already exists — set up in the console by someone, or left by
  a partial destroy — the create fails. That failure is correct: two sources for
  one preference is how session logging ends up quietly off.

## State

State lives in `s3://fb-opp-monitor-tfstate/pilot/terraform.tfstate`, encrypted,
versioned, locked with S3 native locking (`use_lockfile`, no DynamoDB table).

Terraform state is plaintext JSON holding every attribute of every resource. Today
that is infrastructure detail only — this workspace declares no
`aws_secretsmanager_secret_version` and no resource with a password attribute, so
no secret *value* is in it. That property is one commit away from being false: add
a version resource and the value is in state, in every prior version of that
object, and in any local plan file anyone wrote to disk.

So read access to the state bucket is the same privilege as read access to the
secrets. It belongs to the same small set of people. It is not granted to the
reviewer, and it is not granted to CI: `github-deploy-role` can push images and
send one SSM command, and cannot read state.
