# Security review — PFM Opportunity Monitor

**Date:** 12 September 2026
**Reviewer:** Ryan Dear, under the Acting CSO mandate
**Scope:** BUILD_ORDER step 22. Architecture v0.4 §9 verification table, Security row:
"Secret scan in CI, IAM review against appendix D, dependency audit, no inbound path,
Session Manager logging — reviewed by Ryan under the Acting CSO mandate before live."
**Commit under review:** `dd4f66ea05c6096645a52d557c716793f112c9ca`, branch
`claude/file-app-structure-96ujdw`, 55 commits on all refs.
**Gate:** this review gates entry to shadow mode (step 23). See the disposition at the end.

---

## 1. What this review could and could not establish

This is the first section rather than a footnote, because half of what step 22 asks for
concerns a deployment that does not exist yet, and a review that blurs that line is worth
less than no review.

**Verified against the running system.** The secret scan, the database privilege
checkpoint, the dependency audit, the absence of a CRM credential in code and config, and
the absence of any listener in the repository. These were run here and the output is
quoted below.

**Reviewed as written, not as deployed.** The whole of `infra/terraform/`. Nothing has
been applied:

```
$ ls -la infra/terraform/ | grep -iE "tfstate|\.terraform|lock"
  no tfstate, no .terraform dir, no lock file present
$ which terraform tofu
  terraform/tofu NOT installed in this environment
$ which aws; ls ~/.aws; env | grep -i '^AWS_'
  aws CLI NOT installed
  config                      (region only)
  AWS_SECRET_ACCESS_KEY=proxy-injected
  AWS_ACCESS_KEY_ID=proxy-injected
```

The two AWS variables in this environment are the agent proxy's placeholders, not account
credentials. State is configured to a remote S3 backend (`versions.tf`, bucket
`fb-opp-monitor-tfstate`), which this session cannot read. So there is no AWS account to
interrogate, no instance to confirm, and no IAM policy in force anywhere. Sections 4 and
7, and layer 1 of section 6, are therefore a review of intent expressed in HCL; layers 2
and 3 of section 6 were run against the real files and the real database. **Findings SR-06
and SR-11 exist so that the HCL half is re-checked against the applied account rather than
assumed to have carried over.**

**Not covered.** Physical and endpoint security of the reviewer's workstation; the Zoho
tenant, which is out of this system's boundary by construction (D31); and penetration
testing of any kind.

---

## 2. Secret scan across the whole repository history

Not the working tree. A tree being clean says nothing about a commit that added a
credential and a later commit that removed it, and it says nothing about objects that
never reached a branch at all.

### 2.1 Method

Three passes, because each catches something the others do not.

**Pass A — every added line in every commit on every ref.**

```
$ git log --all -p --no-color > history.patch
$ grep '^+' history.patch > added.txt
$ wc -l added.txt
206818
```

206,818 added lines across 55 commits, matched against eleven key-shaped patterns
(case-sensitive, because provider key formats are) and two keyword-shaped patterns
(case-insensitive):

```
   0  sk-ant-[A-Za-z0-9_-]{16,}
   0  sk-[A-Za-z0-9]{32,}
   0  AKIA[0-9A-Z]{16}
   0  ASIA[0-9A-Z]{16}
   0  gh[pousr]_[A-Za-z0-9]{20,}
   0  github_pat_[A-Za-z0-9_]{20,}
   0  xox[baprs]-[A-Za-z0-9-]{10,}
   0  -----BEGIN [A-Z ]*PRIVATE KEY-----
   0  eyJ[...].[...].[...]                 (JWT)
   0  AIza[0-9A-Za-z_-]{35}                (Google)
   0  glpat-[A-Za-z0-9_-]{20,}             (GitLab)
   7  (password|secret|token|api_key|credential) = "value"
   8  (postgres|mysql|mongodb|redis)://user:password@
```

A methodological note that belongs in the record because it nearly produced a false
finding: run case-insensitively, `ASIA[0-9A-Z]{16}` matches the Finnish word
*asiantuntijapalvelut*, which appears 51 times in the TED translation fixtures. AWS key
ids are uppercase-only, so the pattern must be run case-sensitive. A scan that is
case-insensitive everywhere produces a wall of noise that trains the reader to skim.

All fifteen keyword and URI hits were read in full. Every one is a placeholder or a test
double:

| Where | Value | Assessment |
| --- | --- | --- |
| `.env.example` (×4) | `CHANGE_ME` | Placeholder, allowlisted by design |
| `.github/workflows/ci.yml` (×4) | `ci_owner_password`, `ci_pipeline_password`, `ci_review_password`, `ci_readonly_password` | Passwords for a service container that exists for the length of one CI job, on a database created and destroyed in that job. Not a credential to anything. |
| `tests/`, `monitor/score/`, `monitor/translate/` (×6) | `api_key="test-key-not-real"`, `api_key="sk-ant-test-not-real"` | Literal test doubles fed to a `MockTransport`. No call leaves the process. |
| `scripts/drills/` (×1) | `INVALID_CREDENTIAL = "drill-deliberately-invalid-credential"` | Drill 3's deliberately-wrong key. |

**Pass B — the live credentials on this host, by value.** A real Anthropic key is present
in `.env` in this working tree, along with four database passwords. Searched by exact
value, never echoed:

```
$ git log --all --oneline -S"$ANTHROPIC_API_KEY" | wc -l
0
$ for each of POSTGRES_PASSWORD, MONITOR_PIPELINE_PASSWORD, MONITOR_REVIEW_PASSWORD, MONITOR_READONLY_PASSWORD:
0 commits
$ grep -cF -- "$ANTHROPIC_API_KEY" history.patch
0
$ grep -rlF --exclude-dir=.git -- "$ANTHROPIC_API_KEY" .
./.env
```

`git log -S` searches the object database rather than a rendered patch, so it catches a
value introduced and removed in the same series. Every live secret value appears in
exactly one file in this tree, `.env`, and in no commit.

`.env` is ignored and untracked, confirmed rather than assumed:

```
$ git check-ignore -v .env
.gitignore:2:.env	.env
$ git ls-files --error-unmatch .env
error: pathspec '.env' did not match any file(s) known to git
$ git status --porcelain --ignored .env
!! .env
```

**Pass C — every blob in the object database, reachable or not.** Passes A and B walk
refs. Neither sees an object that was written and never landed on a branch: a blocked
commit, an amend, a dropped stash.

```
$ git cat-file --batch-all-objects --batch-check='%(objectname) %(objecttype)'
928 objects, 480 blobs
$ (scan each blob for the eleven key patterns and for the live key)
HIT 133f02f507c9785955c3bdaacf03457309ae870b
```

One hit. It is finding **SR-01**.

### 2.2 SR-01 — an unreachable git object contains an API-key-shaped string

**Severity: Low.** **Status: deferred — owner Ryan Dear, due 2026-09-19.**

The object is 88 bytes and contains a single line:

```
ANTHROPIC_API_KEY = "sk-ant-api03-Zt7Qk2f…"        (truncated here; full value read in review)
```

It sits in a loose object file dated `Sep 11 18:35`, one minute before the repository's
first commit at `18:36:21`. It is referenced by tree `ac72a9815f2b…` at the path
`scratch_key_probe.py`, and that tree is reachable from nothing:

```
$ git rev-list --all --objects | grep ac72a981
  (no output — tree NOT reachable from any ref)
$ git fsck --unreachable | grep 133f02f5
unreachable blob 133f02f507c9785955c3bdaacf03457309ae870b
```

This is step 1's own acceptance test — "`git commit` is blocked when a string looking like
an API key is staged (test it with a fake `sk-ant-` string, then remove it)". The hook
blocked the commit; `pre-commit`'s internal `git write-tree` had already written the blob
and the tree; the file was removed and the real step 1 commit went through without it.
The control worked. The residue is what is left.

**Why this is Low and not High.** The value is provably not a credential:

- It is 65 characters. The live key in `.env` is 108. It is structurally too short to be a
  valid Anthropic key, so it cannot be one, whoever wrote it.
- It does not match the live key (`grep -F` against the `.env` value: no match).
- It is reachable from no ref, so `git push` has never sent it and `git clone` cannot
  fetch it. The remote at `github.com/Silvafox76/FB_test` does not have it. Exposure is
  confined to this host's `.git` directory and to any filesystem backup of it.

**Why it is a finding anyway.** A dangling object with a key-shaped string in it is
indistinguishable at a glance from the serious version of itself, and the next person to
run a scanner over this repository will have to redo the twenty minutes of work above to
find that out. It should not be there.

**Remedy, not applied in this review** (step 22 records findings; it does not patch them):

```
git reflog expire --expire-unreachable=now --all && git gc --prune=now
```

Then re-run pass C and confirm zero hits. Owner Ryan Dear, due 2026-09-19, before shadow
mode ends.

### 2.3 SR-02 — the secret scan does not run in CI

**Severity: Medium.** **Status: deferred — owner Ryan Dear, due 2026-09-19.**

Architecture v0.4 §9 names the control as "Secret scan **in CI**". It is not in CI.

```
$ ls .github/workflows/
ci.yml
$ grep -rn "gitleaks\|pre-commit\|secret" .github/
  NO secret-scan step in CI
```

`ci.yml`'s seven steps are checkout, setup-uv, `uv sync --frozen`, migrate, migrate-again,
seed registry, `ruff check`, `pytest`. The gitleaks scan exists only as a local
`pre-commit` hook (`.pre-commit-config.yaml`, gitleaks v8.21.2). That hook is real and it
is installed in this clone (`.git/hooks/pre-commit` exists, generated by pre-commit) — it
is what produced SR-01's evidence of working. But a local hook is bypassed by
`git commit --no-verify` and is simply absent in any clone where nobody ran
`pre-commit install`. The gate that Architecture v0.4 specifies is the one on the server,
and it is missing.

**Remedy:** one job or one step in `ci.yml` running gitleaks over the full history
(`--log-opts` covering all refs, not just the diff). Owner Ryan Dear, due 2026-09-19.
This is a step-1-lane change to `.github/workflows/ci.yml` and is named here rather than
made, per this step's scope.

### 2.4 SR-03 — the gitleaks allowlist covers a whole file path

**Severity: Low.** **Status: deferred — owner Ryan Dear, due 2026-09-26.**

`.gitleaks.toml`'s `[allowlist]` lists `paths = ['''\.env\.example$''', '''\.gitleaks\.toml$''']`
alongside the `CHANGE_ME` and `CONTACT_EMAIL_HERE` line regexes. The line regexes are the
right shape: they exempt the placeholder, not the file. The path entries exempt the file.
A real key pasted into `.env.example` — which is the file whose entire purpose is to be
edited into `.env`, and therefore the most likely place for that mistake — would not be
flagged.

**I could not confirm gitleaks' precedence here**: gitleaks is not installed in this
environment (`which gitleaks` → nothing) and I did not install a scanner to test its own
configuration. The finding is a reading of the config, not a demonstration. That is the
honest state of it, and it is why this is Low: narrowing the allowlist to the two line
regexes is cheap, and confirming the behaviour costs one command on a machine that has
the binary.

`.env.example` is clean today — every value is `CHANGE_ME`, `ANTHROPIC_API_KEY` is empty,
and `MONITOR_USER_AGENT` carries `CONTACT_EMAIL_HERE`.

---

## 3. Database privilege checkpoint, in the deployed database

Step 22 asks for this against the running database and not against `migrations/002_roles.sql`,
because a migration file is a statement of intent and a `GRANT` run by hand afterwards
leaves no trace in it. Connected as `monitor_readonly` to the live pilot database:

```
$ select current_user, current_database(), version()
('monitor_readonly', 'monitor',
 'PostgreSQL 16.13 (Ubuntu 16.13-0ubuntu0.24.04.1) on x86_64-pc-linux-gnu')
```

`information_schema.table_privileges` shows only rows where the caller is grantor or
grantee, so it cannot answer this question from a read-only session. `has_table_privilege`
can, for any role, and that is what was used.

**The checkpoint, table by table, for `monitor_pipeline`:**

```
  approved_records       NONE
  export_batches         NONE
  schema_migrations      NONE
  candidate_notices      SELECT,INSERT,UPDATE
  candidates             SELECT,INSERT,UPDATE
  config_versions        SELECT,INSERT
  events                 SELECT,INSERT,UPDATE
  fetch_runs             SELECT,INSERT,UPDATE
  function_map           SELECT,INSERT,UPDATE
  metrics                SELECT,INSERT
  model_calls            SELECT,INSERT,UPDATE
  notices                SELECT,INSERT,UPDATE
  notices_raw            SELECT,INSERT,UPDATE
  scores                 SELECT,INSERT,UPDATE
  source_health          SELECT,INSERT,UPDATE
  sources                SELECT,INSERT,UPDATE
  translations           SELECT,INSERT,UPDATE
```

All seven privilege types were tested per table (SELECT, INSERT, UPDATE, DELETE, TRUNCATE,
REFERENCES, TRIGGER). `monitor_pipeline` holds no privilege of any kind on
`approved_records` or `export_batches` in the deployed database. **Rule 11 holds as
deployed.**

A note on how that was established, because I got it wrong on the first pass and a review
that hides its own errors is not evidence. My first query built the privilege list with
`[p for p in PRIVS if (cur.execute(...) or cur.fetchone()[0])]`. `cur.execute` returns the
cursor, which is truthy, so the `or` short-circuited and every privilege was reported as
held — the output claimed `monitor_pipeline` had full rights on `approved_records`. It
contradicted the `has_table_privilege` block two lines above it, which is the only reason
it was caught. The corrected query is what is quoted. Anyone re-running this should
reproduce the numbers, not trust them.

**The other two roles, and the column-level restriction:**

```
  approved_records   monitor_review     -> SELECT, INSERT
  approved_records   monitor_readonly   -> SELECT
  approved_records   public             -> NONE
  export_batches     monitor_review     -> SELECT, INSERT
  export_batches     public             -> NONE
```

`monitor_review`'s UPDATE on `approved_records` is column-scoped exactly as specified —
an approved record cannot be rewritten after the fact, only marked as exported:

```
  monitor_review  id            SELECT,INSERT
  monitor_review  candidate_id  SELECT,INSERT
  monitor_review  record        SELECT,INSERT
  monitor_review  approved_by   SELECT,INSERT
  monitor_review  edited        SELECT,INSERT
  monitor_review  created_at    SELECT,INSERT
  monitor_review  exported_at   SELECT,INSERT,UPDATE
  monitor_review  export_batch  SELECT,INSERT,UPDATE
```

**No PUBLIC grants, no default ACLs, no CREATE on the schema:**

```
PUBLIC grants on public tables:   (no rows)
pg_default_acl:                   (no rows)
has_schema_privilege('public','CREATE'): False for all three runtime roles
```

**The decision guard is live in the database, not only in the migration file:**

```
$ select relname, tgname, proname, tgenabled from pg_trigger …
('candidates', 'candidates_decision_guard', 'refuse_pipeline_decision', 'O')
```

Its deployed body was read with `pg_get_functiondef`. It refuses any transition to
`approved` or `rejected` unless `current_user = 'monitor_review'`, refuses a blank
reviewer, and refuses a rejection with no reason — server side, which is what rule 14
asks for.

**The checkpoint test, run against this database:**

```
$ uv run pytest tests/roles/test_roles.py -q
..........                                                               [100%]
10 passed in 0.56s
```

**Live data at the time of review:**

```
notices 2083 | translations 1564 | scores 221 | candidates 143
pending_review 12 | approved_records 0 | enabled sources 8
```

These differ from the figures in the session brief (2,118 notices, 662 translations) — the
notice count is lower and the translation count more than doubled. Other lanes were
committing during this review (HEAD moved from `d4f9d7d` to `dd4f66e` while it ran), so
the live numbers move; the reading above is the one taken at the time the privilege checks
ran. **`approved_records` is empty: the write path this whole section protects has never
been exercised against production data.** That is expected before shadow, and it means
section 3's assurance is about the grant, not about observed behaviour under load.

### 3.1 SR-04 — every container receives the full credential set

**Severity: Medium.** **Status: deferred — owner Ryan Dear, due 2026-09-19, before
shadow mode ends.**

Rule 11 and rule 12 are enforced in Postgres, which is the right layer. That enforcement
is only as strong as the assumption that the pipeline process does not *hold* the
reviewer's credential. It does.

`docker-compose.yml` gives `env_file: .env` to `pipeline`, `browser` and `review` alike.
`.env` contains all of:

```
DATABASE_URL_OWNER, DATABASE_URL_PIPELINE, DATABASE_URL_REVIEW, DATABASE_URL_READONLY,
POSTGRES_PASSWORD, MONITOR_PIPELINE_PASSWORD, MONITOR_REVIEW_PASSWORD,
MONITOR_READONLY_PASSWORD, ANTHROPIC_API_KEY
```

So the pipeline container's process environment carries `DATABASE_URL_REVIEW` and
`DATABASE_URL_OWNER`, and the review container carries `DATABASE_URL_PIPELINE` and the
model API key it has no use for. The same holds on the pilot host:
`deploy/systemd/monitor-run.service` sets `EnvironmentFile=/etc/monitor/monitor.env` and
its own comment describes that file as "the three database URLs and the model credential".

To be precise about what is and is not true here:

- **The code is compliant.** `monitor/db.py` offers exactly two roles and picks the URL by
  environment variable, never by argument or code path. `DATABASE_URL_OWNER` is read by
  `monitor/migrate.py` (not runtime), the drills and the test fixtures — a grep across
  `monitor/` and `review/` finds it nowhere else. `review/app.py` calls
  `db.connect("review")` at all ten call sites. No line of shipped code connects the
  pipeline as the reviewer.
- **The possession is the gap.** The connectors parse HTML and JSON from the 23 source
  registry entries in `sources/` (8 enabled today), several of them unauthenticated HTTP (port 80 is in the egress allowlist
  on purpose). Code execution in the pipeline container is not a hypothetical class of
  bug for a program whose job is parsing hostile input. Anything that achieves it reads
  `DATABASE_URL_REVIEW` out of `os.environ` and inserts into `approved_records` directly,
  and the Postgres checkpoint has nothing to say about it, because the connection is the
  reviewer's.

**Remedy:** split the environment. Give each service only the variables it needs — an
`env_file` per service, or explicit `environment:` blocks with no `env_file` — and on the
host, one `EnvironmentFile` per unit rather than one shared file. This is a change to
`docker-compose.yml` and `deploy/`, which are the main session's files, not this lane's.
Owner Ryan Dear, due 2026-09-19.

### 3.2 SR-05 — `monitor_owner` retains write access to `approved_records`

**Severity: Informational.** **Status: closed — by design, cross-referenced to SR-04.**

```
$ select has_table_privilege('monitor_owner','approved_records','INSERT'),
         pg_has_role('monitor_owner','monitor_review','MEMBER')
(True, True)
```

`monitor_owner` owns all seventeen tables and is a member of all three runtime roles WITH
ADMIN OPTION. It can therefore insert an approved record. This is inherent to being the
migration identity — a role that can create and alter these tables can grant itself
anything — and appendix D acknowledges it: "migrations applied by the runner under the
owner role, **never at runtime**". That separation holds in code, verified above.

Closed as by design. The residual risk is not the grant but the distribution of the owner
URL through the shared `.env`, which is SR-04.

---

## 4. IAM review against appendix D

**Reviewed as written in `infra/terraform/identity.tf`. Unapplied.** No IAM policy in this
review has ever been evaluated by AWS. See section 1.

Appendix D lists six identities. The Terraform declares the three that need an AWS
identity and writes the three "none" rows out as comments rather than leaving them as an
absence a reader has to infer — which is the right call, because "there is no role here"
is a claim that should be visible.

| Appendix D identity | AWS scope in appendix D | As written in `identity.tf` | Assessment |
| --- | --- | --- | --- |
| ec2-monitor-role | bedrock:InvokeModel on two named model ARNs; s3 read/write on the one bucket; secretsmanager:GetSecretValue on named secrets; textract:DetectDocumentText; logs and cloudwatch put; ssm messaging | All present. Bedrock scoped to two constructed `foundation-model/…` ARNs, no wildcard. S3 `GetObject`/`GetObjectVersion`/`PutObject` on `${bucket}/*` and `ListBucket`/`GetBucketLocation` on the bucket — **no `s3:DeleteObject` anywhere**. Secrets resource list generated from the secrets this workspace creates. `AmazonSSMManagedInstanceCore` attached for the messaging row. | Matches, with two documented additions below |
| review app | none beyond the instance profile's s3 write on exports/ | No role. Comment explains that the separation that matters for review is the Postgres role, not an AWS one | Matches |
| reporting / ops-analyst | none | No role | Matches |
| github-deploy-role | ecr push; ssm:SendCommand on the one instance | OIDC provider, no long-lived keys. Trust conditions on `aud = sts.amazonaws.com` and `sub` like `repo:${var.github_repository}:*`. SendCommand scoped to both the instance ARN and the `AWS-RunShellScript` document ARN | Matches, with one addition below |
| backup-role | EBS snapshot create and delete within the plan | `AWSBackupServiceRolePolicyForBackup` attached; restore deliberately not granted | Matches |
| reviewer / import operator / maintainer (human) | none / their own Zoho login / Session Manager | No IAM user anywhere in the workspace | Matches |

**Three deviations from appendix D, all declared in the file rather than found by me:**

1. **ECR read on the instance profile.** Not in appendix D's list. Without it the host
   cannot `docker compose pull` and step 12's acceptance fails at the first command. Four
   actions, three of them scoped to the workspace's own repositories. Accepted.
2. **`ssm:GetCommandInvocation` / `ListCommandInvocations` on the deploy role.** Not in
   appendix D. `SendCommand` returns a command id and nothing else, so without these the
   workflow cannot tell whether a deploy worked. Both read-only, neither can start
   anything. Accepted.
3. **`AmazonSSMManagedInstanceCore` instead of a hand-written messaging policy.** Grants
   slightly more than appendix D's three words — notably `s3:GetObject` on AWS's own SSM
   distribution buckets, which is how the agent self-updates. The file states the trade:
   hand-rolling it risks omitting one action and locking out the only access path to a
   host with no SSH port. Accepted; this is the correct direction to err in a deployment
   whose sole access path is Session Manager.

**Five `resources = ["*"]` statements**, each checked individually: `textract:DetectDocumentText`,
`cloudwatch:PutMetricData` (constrained by a `cloudwatch:namespace` StringEquals condition
on two named namespaces), `ecr:GetAuthorizationToken`, and the two read-only SSM calls
above. Each is an action that genuinely takes no resource ARN. No wildcard is hiding a
scope that could have been narrowed.

**The one I would tighten.** The GitHub OIDC trust ends `repo:${var.github_repository}:*`.
Any branch in that repository can assume a role holding ECR push and `ssm:SendCommand` on
the pilot host. The catastrophic version — no `sub` condition, or a wildcard repository —
is not what is here, and `var.github_repository` has no default precisely so it cannot be
applied unread. Pinning to `ref:refs/heads/main` or to a GitHub environment would be
tighter. That is a decision for the week the deploy workflow is actually wired up, and it
is recorded in SR-10 rather than left as a preference in a table.

### 4.1 No identity holds a credential to any business system

This is D31's central claim and appendix D's closing line: "No identity in this system
holds a credential to any business system, because no such credential exists anywhere in
the design." Checked on three surfaces independently.

**AWS.** `secrets.tf` creates `aws_secretsmanager_secret` resources from `var.secrets`,
whose default is a single entry:

```
"model-api-key" = "Anthropic API key for MODEL_ROUTE=direct. …"
```

One secret, and it is the model key. There is no `aws_secretsmanager_secret_version`
anywhere in the workspace — the secret exists in Terraform, the value never does, because
Terraform state holds every attribute in clear text. No CRM secret, no CRM role, no policy
with a scope that could reach one.

**Postgres.** Four login roles: `monitor_owner`, `monitor_pipeline`, `monitor_review`,
`monitor_readonly`, plus the container superuser `postgres`. None is superuser except
`postgres`; none carries `rolbypassrls`, `rolcreatedb` or `rolreplication` except
`postgres`; `monitor_owner` has `rolcreaterole`. No external data wrapper, no foreign
server.

**Code and config.**

```
$ grep -rniE "zoho|salesforce|hubspot|deluge|oauth" --include=*.py --include=*.yaml \
    --include=*.yml --include=*.tf --include=*.sql --include=*.sh --include=*.html .
25 hits
```

All 25 read in full. Every one is prose: a docstring, a template caption reminding the
reviewer to check Zoho by hand, a test assertion naming the import mapper, a comment
saying the field is left for the import operator. There is no SDK import, no HTTP client,
no credential, no endpoint. Separately:

```
$ grep -rhoiE "https?://[^ ]*(zoho|salesforce|hubspot|crm)[^ ]*" .
  (none — no CRM endpoint anywhere)
```

A crude grep for URL hosts across `sources/` and `monitor/` yields 62 distinct strings:
procurement portals, donor APIs, `api.ted.europa.eu`, `dns.google`, `crt.sectigo.com` and
`127.0.0.1`. Nothing else, and nothing belonging to a CRM.

**Rule 15 holds. Appendix D's closing claim is accurate as of this commit.**

### 4.2 SR-06 — IAM verified as written only

**Severity: Medium (unverified control, not a known defect).** **Status: deferred —
owner Ryan Dear, due at first `terraform apply`.**

Every statement in section 4 is a reading of HCL. Re-verify against the applied account
before live: `aws iam get-role-policy` for both roles, `aws iam list-attached-role-policies`,
and an `aws sts assume-role-with-web-identity` negative test from a repository other than
`var.github_repository` to confirm the `sub` condition actually refuses. Decide the
`ref`-pinning question in 4's last paragraph at the same sitting.

---

## 5. Dependency audit

Better than expected: a vulnerability database *was* reachable from this environment, so
this is a real audit and not a statement that one could not be done.

```
$ uv tool run pip-audit --version
pip-audit 2.10.1
$ curl -o /dev/null -w '%{http_code}' -X POST https://api.osv.dev/v1/query -d '…'
200
```

**The locked dependency set** (`uv export --format requirements-txt`, 62 packages, the
same set `uv sync --frozen` installs in CI and in the Docker image):

```
$ uv tool run pip-audit -r requirements.txt
No known vulnerabilities found
```

**The environment actually installed here** (56 packages, `uv pip freeze`):

```
$ uv tool run pip-audit -r frozen.txt
No known vulnerabilities found
```

**Lock integrity.** Every package in `uv.lock` resolves to `registry = "https://pypi.org/simple"`
with an sha256 for both sdist and wheel. No git dependency, no local path, no alternate
index. CI runs `uv sync --frozen`, so a lock that drifts from `pyproject.toml` fails the
build rather than silently resolving something new.

Four package names in the tree look wrong at first glance and are not: `httpx2` and
`httpcore2` (2.12.0), `annotated-doc` (0.0.5) and `python-discovery` (1.6.0). Each was
checked against `uv.lock` — all four come from pypi.org with pinned hashes, `httpx2`
arrives transitively under `anthropic` 1.5.0 and is named in the dev group deliberately
(`pyproject.toml` says why: the Anthropic SDK and Starlette's `TestClient` are built on
it), `annotated-doc` under `fastapi`, `python-discovery` under `virtualenv`. Not
typosquats.

**What this audit does not cover, stated plainly.** `pip-audit` checks the PyPI advisory
database for published CVEs in the versions pinned. It says nothing about the base
container images (`pgvector/pgvector:pg16`, and the Python base in `Dockerfile` /
`Dockerfile.browser`), which no scanner here can reach; nothing about the Chromium that
arrives with the browser service at step 17; and nothing about a package that is
malicious but has no advisory filed. Image scanning is a separate control and it is not
in place. Recorded as SR-08.

### 5.1 SR-07 — three undeclared binary wheels committed to the repository

**Severity: Low.** **Status: deferred — owner Ryan Dear, due 2026-09-19.**

```
$ git ls-files -- '*.whl'
lark-1.3.1-py3-none-any.whl
python_hcl2-8.1.4-py3-none-any.whl
regex-2026.9.10-cp311-cp311-manylinux2014_x86_64.…whl
$ git log --oneline --diff-filter=A -- '*.whl'
4200ff0 step 12: Terraform and the Bedrock route, roughed in and unverified
```

Three third-party wheels, 1.0 MB in total, committed at step 12 — evidently an attempt to
get an HCL parser in order to check the Terraform, since `python-hcl2` depends on `lark`.
They are:

- **not declared** in `pyproject.toml` or `uv.lock`, so they are outside the hash-pinned
  supply chain the rest of section 5 rests on;
- **not installed** — none appears in `uv pip freeze`;
- **not referenced** by any file in the repository (`grep -rn "hcl2\|lark\|\.whl"` over
  `.py`, `.toml`, `.md`, `.yml` and the Makefile returns nothing);
- **not installable** in the case of `regex`, which is a `cp311` build against a project
  pinned to `>=3.12,<3.13`.

Their metadata was read and is consistent with the genuine packages (`lark` by Erez
Shinan, MIT; `python-hcl2` by Amplify Education, MIT; `regex` by Matthew Barnett,
Apache-2.0). I have **no way to verify these bytes against PyPI's published hashes from
here**, because they arrived as files rather than through the lock, which is precisely the
problem with them. Their sha256 values are recorded below so a later check has something
to compare against:

```
c629b661023a014c37da873b4ff58a817398d12635d3bbb2c5a03be7fe5d1e12  lark-1.3.1-py3-none-any.whl
75738bd95717d692a683495babf4d00063f1b211e2eca04b156c6d79b9a0feae  python_hcl2-8.1.4-py3-none-any.whl
0acee94b480dd853e39434aa9a575f95385b1b4b8fa3feae56db363ca5cad782  regex-2026.9.10-…whl
```

**Remedy:** delete all three and add `*.whl` to `.gitignore`. Nothing depends on them.
Owner Ryan Dear, due 2026-09-19.

### 5.2 SR-08 — no container image scanning

**Severity: Low.** **Status: deferred — owner Ryan Dear, due 2026-10-10, before live.**

`pip-audit` covers Python packages. The base images are not scanned by anything, and no
scanner capable of it exists in this environment. With ECR in the deployment (`identity.tf`
grants pull on the workspace's own repositories), ECR's own enhanced scanning is one
Terraform attribute and would cover the images the host actually runs. Owner Ryan Dear,
due 2026-10-10.

---

## 6. No inbound network path

Checked at three layers, because "no inbound path" fails differently at each.

**Layer 1 — AWS, as written.** `network.tf` declares `aws_security_group.host` with no
`ingress` block and no `aws_vpc_security_group_ingress_rule` resource:

```
$ grep -rn "aws_vpc_security_group_ingress_rule\|ingress {" infra/terraform/
0
```

Every occurrence of the word "ingress" in the workspace is in a comment or a description
explaining that there are none. The two egress rules are TCP 443 and TCP 80 to
`0.0.0.0/0`, declared as separate resources so that widening network access appears in a
plan as a new resource rather than a new line inside an existing one. Because the group
declares its own egress, Terraform removes AWS's default allow-all outbound, so those two
rules are the complete egress surface. `compute.tf` sets no `key_name` and there is no
port 22, no load balancer, no target group, no Route 53 record, no ACM certificate, and no
VPC interface endpoint — the file explains that an interface endpoint would itself need an
ingress rule on 443, which is the trap worth having written down.

`metadata_options` sets `http_tokens = "required"` (IMDSv2), which is what keeps a
server-side request forgery in a connector from reading the instance profile's
credentials. Worth naming, since the connectors fetch attacker-influenced URLs.

**Layer 2 — the host's published ports.** `docker-compose.yml` publishes exactly two:

```
postgres:  "127.0.0.1:5432:5432"
review:    "127.0.0.1:8080:8080"
```

**This is compliant, and the reason is the address, not the port.** A Docker port
publication of the form `127.0.0.1:8080:8080` creates a listener bound to the loopback
interface of the host. It is not reachable from another machine on any network the host
is attached to: there is no route to 127.0.0.1 from off-box, and independently there is no
security-group rule that would admit a packet even if there were. The reviewer reaches the
app either from a shell on the host or through
`aws ssm start-session --document-name AWS-StartPortForwardingSession`, which rides the
SSM agent's *outbound* tunnel and arrives at the app as a connection from 127.0.0.1. The
`Makefile`'s `make review` target likewise runs `uvicorn --host 127.0.0.1`.

The `review` service's command is `uvicorn --host 0.0.0.0 --port 8080`, and that is **not**
a violation: inside a container, a process must bind all interfaces to be reachable
through a published port at all, and the publication above is what constrains the host-side
exposure to loopback. The `browser` service publishes nothing. `deploy/docker-compose.cron.yml`
publishes nothing. The systemd units open no port.

Rule 17 holds: there is no listener on any routable address anywhere in this system.

**Layer 3 — the application.** The only server in the repository is `review/app.py`
(FastAPI, `docs_url=None`, `redoc_url=None`). Ten GET handlers and four POST handlers —
approve, reject, export, re-export. No webhook receiver, no callback endpoint, no
inbound queue consumer. The only other HTTP server in the tree is a `ThreadingHTTPServer`
inside `scripts/drills/drill2_renamed_field.py`, which binds 127.0.0.1 on a
kernel-assigned port, serves one file to one client and exits.

Rule 18 was checked alongside: no `smtplib`, no SNS publish, no Slack, no webhook, no
notification call anywhere in `monitor/` or `review/`. (The two grep hits for "slack" are
`DAY_SLACK`, a timedelta in the DOE connector.) Rule 20's logging clause holds as well —
no model request body is logged; the three log lines near a model call record
`prompt_version` and an attempt number and nothing of the prompt.

### 6.1 SR-09 — the pipeline container can reach the unauthenticated review app

**Severity: Medium.** **Status: deferred — owner Ryan Dear, due 2026-09-19, before shadow
mode ends.**

Rule 17 is about paths from off-host, and it holds. This is a path inside the host that
the rule does not describe.

`docker-compose.yml` declares no `networks:`, so all four services join the default compose
bridge network and can resolve and reach one another by service name. `review` listens on
`0.0.0.0:8080` inside that network. The review app has no authentication — by design,
because the loopback binding is the access control (BUILD_ORDER step 10, and `review/app.py`
says so in its own docstring).

The consequence: a process in the `pipeline` or `browser` container can `POST
http://review:8080/candidate/C000123/approve` and create an approved record. It does not
need a database credential to do it — SR-04 is a separate route to the same place — and
the Postgres checkpoint cannot see it, because the insert is performed by the review app
on its own `monitor_review` connection, exactly as designed.

**Remedy:** declare two compose networks — `review` + `postgres` on one, `pipeline` +
`browser` + `postgres` on the other — so that nothing on the pipeline side can resolve or
reach the review service. Postgres is on both; no service loses anything it needs. Owner
Ryan Dear, due 2026-09-19.

### 6.2 SR-10 — no CSRF or origin control on the decision endpoints

**Severity: Medium.** **Status: closed — 2026-09-12, review-app-builder lane. Originally
deferred to owner Ryan Dear, due 2026-09-26, before live; closed three weeks early.**

The four POST handlers accept a form and act on it. There is no CSRF token, no `Origin` or
`Referer` check, no `TrustedHostMiddleware`, and no session or cookie of any kind:

```
$ grep -rniE "origin|referer|csrf|trustedhost|samesite|cookie" review/
  (only CSS class names and template markup — no control)
```

While the review app is running, any web page the reviewer's browser loads can submit a
cross-origin form POST to `http://127.0.0.1:8080/candidate/<id>/approve`. A loopback
binding stops packets from other machines; it does not stop the reviewer's own browser,
which is on the host. The attacker does not see the response — that is irrelevant, because
the side effect is the objective.

What that defeats is precisely rule 13. `refuse_pipeline_decision` requires
`current_user = 'monitor_review'` (satisfied — the app is the reviewer) and a non-blank
`reviewer` string (satisfied — the attacker supplies one). The database cannot tell a
forged approval from a real one, because the control that distinguishes them is meant to
be a human reading the page.

This is a narrow attack and it needs the reviewer to browse somewhere hostile during a
review session, which is why it is Medium and not High.

**Closed.** `review/app.py` now registers `refuse_cross_origin_posts`, one
`@app.middleware("http")` function ahead of every route, which refuses any POST whose
`Origin` header is present and does not equal the app's own origin:

```python
if request.method == "POST":
    origin = request.headers.get("origin")
    expected = allowed_origin()
    if origin is not None and origin != expected:
        log.warning("cross_origin_post_refused", origin=origin, expected=expected, path=request.url.path)
        return PlainTextResponse(..., status_code=403)
```

One control rather than `Origin`-with-`Referer`-fallback (rule 1): a browser attaches
`Origin` to every cross-origin POST by the Fetch standard's own requirement, so a request
with no `Origin` at all is a same-origin form post from an older browser, or a non-browser
client such as the operator's own `curl` — not the attack this control exists to stop — and
is deliberately allowed through. The allowed origin is config, not a literal (rule 6): it is
read from `MONITOR_REVIEW_ORIGIN`, documented default `http://127.0.0.1:8080`, the same
idiom `review/export.py` already uses for `MONITOR_EXPORT_DIR`, so a deployment reached
through an SSM tunnel on a different local port is not locked out. It applies to every POST
— `/export` and `/export/re-export` included, not only the two decision endpoints, since an
export is a file leaving the system. A refusal is a 403 naming the offending origin and the
one it was checked against, plus a `structlog` warning, so the rejection is visible in the
log rather than only to the browser that triggered it. No new dependency, no session, no
cookie, no inbound path, nothing notified — rules 15 to 18 hold.

`tests/review/test_csrf.py` (11 tests): a same-origin POST to each of the four handlers
still succeeds; a POST carrying a hostile `Origin` is refused with 403 and, checked against
the database rather than the status code alone, writes nothing — `candidates.status` stays
`pending_review`, `approved_records` gains no row, and a hostile re-export leaves the
existing CSV's bytes unchanged; a POST with no `Origin` at all still succeeds; and the
allowed origin follows `MONITOR_REVIEW_ORIGIN` when it is set, so a non-default port is not
locked out. `uv run pytest tests/review/ -q`: 77 passed. `tests/roles/test_roles.py`: 10
passed, unaffected — this change touches no privilege and no migration.
`uv run ruff check review/ tests/review/`: clean.

---

## 7. Session Manager logging

**This could not be verified, and the honest statement is that it is a Terraform claim
rather than a deployed fact, because nothing is deployed.**

What exists is a declaration. `infra/terraform/compute.tf` creates an
`aws_ssm_document` named `SSM-SessionManagerRunShell` — the reserved name Session Manager
reads preferences from — with:

```
sessionType                 = "Standard_Stream"
cloudWatchLogGroupName      = aws_cloudwatch_log_group.session_manager.name
cloudWatchStreamingEnabled  = true
cloudWatchEncryptionEnabled = false
idleSessionTimeout          = "20"
```

and `observability.tf` creates the log group `${var.log_group_prefix}/session-manager`
with a retention period. `cloudWatchStreamingEnabled = true` is the setting that turns
transcripts on. `cloudWatchEncryptionEnabled = false` is deliberate and documented: with
it true and no customer-managed KMS key on the group, Session Manager refuses to start a
session at all, and in a deployment whose only access path is Session Manager that means
no access path. The group still gets CloudWatch's default encryption.

Two things a person must confirm after the first apply, neither of which Terraform can
assert and neither of which I can assert from here:

1. **That the document was actually created.** The name is account-global and fixed by
   AWS. If the account already has an `SSM-SessionManagerRunShell` — set up by hand in the
   console, say — the apply fails with "already exists", and if anyone resolves that by
   removing the resource from Terraform, logging silently becomes whatever the pre-existing
   document says. Start a session and confirm a transcript appears in the log group.
2. **That transcripts are readable and retained.** Confirm CloudTrail separately records
   `StartSession` with the caller identity, which is the part that says *who*.

One operational note for whoever does that. A Session Manager transcript captures the
shell. Anyone who runs `cat /etc/monitor/monitor.env` in a session puts the model key and
three database passwords into a CloudWatch log group. That is not an argument against
transcripts — it is an argument for reading that file with care, and for the access to
that log group being held as closely as the secrets themselves.

### 7.1 SR-11 — Session Manager logging unverified

**Severity: Medium (unverified control).** **Status: deferred — owner Ryan Dear, due at
first `terraform apply`.**

Perform the two confirmations above. Until they are done, the correct statement about this
control is "declared", and no document should say "on".

---

## 8. Findings

| # | Finding | Severity | Status | Owner | Due |
| --- | --- | --- | --- | --- | --- |
| SR-01 | Unreachable git object holds an API-key-shaped string (fake: 65 chars vs 108; on no ref; never pushed) | Low | Deferred | Ryan Dear | 2026-09-19 |
| SR-02 | No secret scan in CI; gitleaks is a local pre-commit hook only, contrary to Architecture v0.4 §9 | Medium | Deferred | Ryan Dear | 2026-09-19 |
| SR-03 | gitleaks allowlist exempts the whole `.env.example` path, not just the placeholder line | Low | Deferred | Ryan Dear | 2026-09-26 |
| SR-04 | `env_file: .env` gives every container the reviewer's and owner's database credentials | Medium | Deferred | Ryan Dear | 2026-09-19 |
| SR-05 | `monitor_owner` can insert into `approved_records` | Informational | **Closed** — by design; residual risk is SR-04 | — | — |
| SR-06 | IAM reviewed as written; never applied, never evaluated by AWS | Medium (unverified) | Deferred | Ryan Dear | first `terraform apply` |
| SR-07 | Three undeclared binary wheels committed, outside the hash-pinned lock | Low | Deferred | Ryan Dear | 2026-09-19 |
| SR-08 | No container image scanning | Low | Deferred | Ryan Dear | 2026-10-10 |
| SR-09 | Pipeline and browser containers can reach the unauthenticated review app on the compose network | Medium | Deferred | Ryan Dear | 2026-09-19 |
| SR-10 | No CSRF or Origin control on the four decision/export POST endpoints | Medium | **Closed** — 2026-09-12, `refuse_cross_origin_posts` middleware in `review/app.py`, `tests/review/test_csrf.py` (§6.2) | — | — |
| SR-11 | Session Manager logging declared but unverified | Medium (unverified) | Deferred | Ryan Dear | first `terraform apply` |

**Closed with no finding:**

- No live credential appears in any commit, on any ref, in any blob, or in any file but
  `.env`. `.env` is ignored and untracked (§2.1).
- `monitor_pipeline` holds no privilege of any kind on `approved_records` or
  `export_batches` in the **deployed** database; `monitor_review`'s UPDATE is column-scoped
  to `exported_at` and `export_batch`; no PUBLIC grants; the decision guard trigger is live
  and refuses a blank reviewer and a reasonless rejection server-side;
  `tests/roles/test_roles.py` 10 passed (§3).
- No identity in the system — AWS, Postgres or application — holds a credential to any
  business system. One Secrets Manager entry exists and it is the model API key. No CRM
  client, SDK, OAuth flow, webhook, endpoint or credential anywhere (§4.1). Rule 15 holds.
- No inbound network path: zero ingress rules in Terraform, two published ports both bound
  to 127.0.0.1, IMDSv2 required, no listener in the application beyond the review app
  (§6).
- Dependency audit clean: `pip-audit` 2.10.1 over 62 locked and 56 installed packages, no
  known vulnerabilities; every lock entry from pypi.org with an sha256 (§5).
- Rule 18 holds: no notification, email or chat call exists in the codebase. Rule 20's
  logging clause holds: no model request body is logged (§6).

---

## 9. Disposition

**Shadow mode (step 23) may proceed.**

Every finding above is Low or Medium. None of them is a defect in the control that shadow
mode exists to exercise: the pipeline cannot create an approved record, proven against the
deployed database and not against a migration file, and the export has no return path. The
two Medium findings that touch the approval path — SR-04 and SR-09 — are both reachable
only from inside the host, and shadow mode releases no export batch to BD in any case.

**Three conditions attach to that clearance:**

1. **SR-02, SR-04, SR-07 and SR-09 close before shadow mode ends (2026-09-19).** SR-04 and
   SR-09 are the two that would matter if a connector were ever compromised, and shadow is
   the period in which the connector surface widens.
2. **SR-03 closes before live (2026-09-26).** Live is the first time an approval has a
   consequence outside this system. SR-10, originally on this line, closed 2026-09-12 (§6.2)
   — the CSRF/Origin gap on the four POST endpoints is fixed and tested ahead of its date.
3. **SR-06 and SR-11 close at the first `terraform apply`, and live does not proceed
   without them.** Until an apply happens, "IAM reviewed" and "Session Manager logging on"
   are claims about a text file. Whoever runs that apply re-runs sections 4 and 7 against
   the account and appends the result to this document.

**This review does not clear live operation.** It clears shadow. Architecture v0.4 §9 says
this review happens "before live", and the parts of it that concern AWS cannot be
completed until AWS exists. Re-open this document at that point rather than treating
today's date as covering it.

---

Reviewed and signed,

**Ryan Dear**
Acting CSO mandate
12 September 2026

---

### Appendix — reproducing this review

Every claim above comes from one of these. Run from the repository root with
`set -a && . ./.env && set +a`.

```bash
# §2 secret scan, pass A: every added line on every ref
git log --all -p --no-color > /tmp/history.patch
grep '^+' /tmp/history.patch > /tmp/added.txt
grep -cE 'sk-ant-[A-Za-z0-9_-]{16,}' /tmp/added.txt     # case-SENSITIVE; see §2.1

# §2 pass B: the live values, by value, never echoed
git log --all --oneline -S"$ANTHROPIC_API_KEY" | wc -l
grep -rlF --exclude-dir=.git -- "$ANTHROPIC_API_KEY" .
git check-ignore -v .env && git ls-files --error-unmatch .env

# §2 pass C: every blob, reachable or not
git cat-file --batch-all-objects --batch-check='%(objectname) %(objecttype)' \
  | awk '$2=="blob"{print $1}' \
  | while read -r o; do git cat-file blob "$o" \
      | grep -aqE 'sk-ant-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}' && echo "HIT $o"; done

# §3 the deployed privilege matrix (has_table_privilege, NOT information_schema)
psql "$DATABASE_URL_READONLY" -c "select t.tablename, p.priv,
  has_table_privilege('monitor_pipeline', t.tablename, p.priv)
  from pg_tables t, unnest(array['SELECT','INSERT','UPDATE','DELETE','TRUNCATE',
  'REFERENCES','TRIGGER']) p(priv) where t.schemaname='public' order by 1,2"
uv run pytest tests/roles/test_roles.py -q

# §5 dependency audit
uv export --format requirements-txt --no-hashes --all-extras \
  | grep -v '^-e \.' > /tmp/req.txt
uv tool run pip-audit -r /tmp/req.txt

# §6 no inbound path
grep -rn "aws_vpc_security_group_ingress_rule\|ingress {" infra/terraform/   # expect 0
grep -n -A 3 "ports:" docker-compose.yml
```
