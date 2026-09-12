# Demo

Ten minutes at a keyboard, in order, with the URL for every page. Read it once
before you run it; the beats depend on each other and the fifth one is the point.

**The one claim this demo makes.** The pipeline reads public notices, throws away
most of them for nothing, and puts the rest in front of a person. It cannot approve
one of them — not as a setting somebody chose, but as a database privilege it does
not hold. A named human approves, and the only way a record leaves is a file
somebody carries. Everything below is in service of showing that live rather than
asserting it.

Say the times out loud to yourself in rehearsal. The temptation is to spend six
minutes on the queue page, which is the least interesting thing here.

---

## Before the audience arrives

Five minutes, not part of the ten. Do it in a separate terminal and leave both
windows open: one terminal, one browser.

```bash
cd /home/user/FB_test                    # or /opt/monitor on the pilot host
set -a && . ./.env && set +a             # the three database URLs; nothing works without this
make status                              # proves the database is up and has notices in it
```

**The queue must not be empty when you start.** Candidates reach it from
`monitor stage`, so run one:

```bash
make stage
```

Run on 2026-09-12 this printed:

```
scored notices 110, candidates created 88, joined 22
staged 9, held by the per-source cap 0, below threshold 79
```

Nine candidates in the queue, scoring 68 to 72, all from TED, all in the Europe
region. If it prints `staged 0`, there is nothing to demo and no amount of clicking
will produce it: either nothing has been scored yet (`make run` first, and see the
caveats at the end of this file), or everything scored below the staging threshold of
60. Find that out now, not in front of people.

Then start the review app and leave it running:

```bash
make review                              # uvicorn on 127.0.0.1:8080, foreground
```

Open <http://127.0.0.1:8080/> and check the queue is not empty. Have these six tabs
ready, in this order — they are the demo:

| # | Tab | URL |
| --- | --- | --- |
| 1 | Queue | <http://127.0.0.1:8080/> |
| 2 | One candidate | `http://127.0.0.1:8080/candidate/<id>` — click the top row to get the id |
| 3 | Decided | <http://127.0.0.1:8080/decided> |
| 4 | Audit | <http://127.0.0.1:8080/audit> |
| 5 | Sources | <http://127.0.0.1:8080/sources> |
| 6 | Export | <http://127.0.0.1:8080/export> |

---

## The ten minutes

| From | Beat | Where |
| --- | --- | --- |
| 0:00 | What it is, and the claim | talking |
| 1:00 | What a night's reading costs | terminal, `make status` |
| 2:30 | The queue | `/` |
| 4:00 | One candidate, and the record it proposes | `/candidate/<id>` |
| 6:30 | **The checkpoint: it is refused, twice** | `/candidate/<id>` and the terminal |
| 8:00 | Approve under your own name, and the audit trail | `/decided`, `/audit` |
| 9:00 | The only way out is a file | `/export` and the terminal |
| 10:00 | Close | talking |

### 0:00 — What it is, and the claim (1 minute)

Two sentences and no slides:

> This reads the public procurement portals and donor feeds for 47 countries every
> morning, scores what it finds against our 33 PFM functions, and puts the few that
> matter in a queue. It cannot create an opportunity record — only a named reviewer
> can, and the only way anything leaves is a CSV that a person imports by hand.

Then say what you are about to prove, so the audience knows what to watch for: that
the second half of that sentence is enforced rather than promised.

### 1:00 — What a night's reading costs (1.5 minutes)

In the terminal:

```bash
make status
```

Run on 2026-09-12:

```
source       state     notices  passed  dropped  drop rate  needs tr.  unfiltered
fts          healthy        25       0       23       100%          0           0
prozorro     healthy       390       0      390       100%          0           0
ted          healthy       806      71      698        91%          0           0
worldbank    healthy        13       0        7       100%          0           0

considered 1189, passed 71, dropped 1118, drop rate 94.0%
```

The number to point at is **94%, dropped for no model cost at all.** That column is
a CPV-code check and a keyword match against the notice in its own language, both
free. Prozorro is the clearest case: tyres, fuel, food and vehicle parts, 390
notices, all dropped on their classification alone, none of which reached a model or
cost anything.

If somebody adds up a row and finds notices missing — `fts` shows 25 notices and 23
decided — the answer is that `passed` counts what is waiting for the scorer right
now, so a notice that has been scored has left the column. It is not a rounding
error and it is worth answering straight.

Then the cost of the part that is not free:

```bash
psql "$DATABASE_URL_READONLY" -c "
select purpose, count(*) as calls, round(sum(cost_usd), 4) as usd
from model_calls
where at >= date_trunc('day', now())
group by purpose order by purpose;"
```

Run on 2026-09-12: 85 scoring calls for USD 0.33 and 568 translation calls for USD
1.04, against a hard daily cap of 2,000 calls and USD 25 checked **before** each call.
If anybody asks what happens at the cap: the run stops and says so, it does not
quietly do less.

### 2:30 — The queue (1.5 minutes)

<http://127.0.0.1:8080/>

What is on the page: the count and an estimate of the review time (six minutes a
candidate), a region filter, and one row per candidate — score, title, buyer,
country, region, deadline, value, and how many notices are behind it. Highest score
first, because that is the order a reviewer should work in.

Two things to say while it is on screen:

- **This is the reviewer's whole interface for the pilot.** It is not a placeholder
  for a CRM screen arriving in week 5. There is no CRM integration in any of the 31
  steps.
- **The region filter is the priority market.** West Africa is weighted 1.0, Ukraine
  and the Western Balkans 0.8, the EU and the UK 0.6. Today's nine are all European,
  which is honest: the West African portals are step 17 and are not connected yet.

Then click the top row.

### 4:00 — One candidate, and the record it proposes (2.5 minutes)

`http://127.0.0.1:8080/candidate/<id>` — on 2026-09-12, for example,
<http://127.0.0.1:8080/candidate/C000012>.

Walk down the page. It is one screen for one decision:

- **The score and the title**, in English, at the top.
- **Matched functions with the evidence**, which is the part worth pausing on: each
  matched function names the words *from the notice* that matched it, not a
  restatement of the function. That is what makes the score arguable rather than
  oracular.
- **The notices in this candidate**, with the source, the link to the original, and
  how each notice joined — one row per notice, with the match method. When a World
  Bank notice and a national portal notice describe the same tender, they arrive here
  as one candidate rather than two pieces of work. (Today's nine each have one
  notice. The clustering is visible on the audit page as `duplicate_joined` events.)
- **The original title and body** are shown beside the English rendering whenever the
  notice is not in English. The published text is the record; the English is a
  derived field with a model and prompt stamp on it, and the deadline is always parsed
  from the original — never from a translation.
- **The decision panel**, which is the only orange thing on the page, and the only
  place anything can be written.

Open **"Proposed record — 73 columns, all editable"** inside the panel. This is the
thing to let the audience read for a moment:

- It is shaped like the Opportunity record their CRM already uses, with the columns in
  that order and under those names, because the header row is what the import mapper
  matches on.
- Every field a BD person owns — pricing structure, warranty, bid bond, evaluation
  weighting — carries the same placeholder the CRM already shows for an unfilled
  field. Not a blank, and **not a plausible-looking invented value.** An imported
  record should read like an early-stage record a person started, and arrive a day
  after publication rather than whenever somebody noticed it.
- FreeBalance Products Required is a set, derived from the component map, not a
  sentence somebody wrote.
- Account Name is proposed as text and nothing more. The pipeline never resolves an
  Account; it has no CRM access with which to try.

Last, read the reminder line above the buttons out loud, because it is policy and not
decoration:

> Before approving, check Zoho for an existing Opportunity on this buyer. The Monitor
> never searches the CRM (D31); this check is the only duplicate control.

It is there because it is already known to be necessary: the Ghana tender this system
would surface has had a live Opportunity since December 2025.

### 6:30 — The checkpoint: it is refused, twice (1.5 minutes)

This is the beat the other nine minutes exist for. Do it in two moves.

**Move one, in the browser.** In the decision panel, type **three spaces** into "Your
name, to approve" and press Approve.

You are bounced straight back to the candidate page with the message

```
a reviewer name is required: no candidate is approved by nobody
```

The candidate is still `pending_review`, no record was written, and no event was
logged. Type three spaces rather than leaving the box empty: an empty box is stopped
by the browser, and whitespace is what gets past a check that does not strip. Say
that while you do it.

**Move two, in the terminal**, because the message above is a courtesy and not the
enforcement:

```bash
uv run python scripts/drills/drill5_pipeline_insert.py
```

Run on 2026-09-12, abridged — the real output carries five checks and a header:

```
  [ok]  the drill is connected as the pipeline's own role
        current_user 'monitor_pipeline'
  [ok]  the insert is refused, and refused for want of privilege rather than for want of a row
        InsufficientPrivilege: permission denied for table approved_records
  [ok]  reading the table is refused too: no privilege of any kind, select included
  [ok]  the control: monitor_review reads the same table, so the refusal is about the role
  PASS  drill 5: the pipeline cannot write, or even read, an approved record (5 checks)
```

What to say: **the pipeline's database role has no privilege on the approved-records
table — not insert, not even select.** So a bug, a bad prompt, an over-helpful future
change or somebody's clever idea cannot create an approved record. It is not a policy
in a document or a check in the code that a later edit could remove; it is a grant,
and one test asserts it on every single commit. Exactly one code path in the whole
repository inserts into that table, inside the reviewer's own decision transaction.

(There is a scripted version of move one too, `drill4_blank_reviewer.py`, which also
shows Postgres refusing the same transition directly. Prefer the live form in front
of an audience and keep the drill for the sceptic who stays behind.)

### 8:00 — Approve under your own name, and the audit trail (1 minute)

Now type your actual name and press Approve. You land on <http://127.0.0.1:8080/decided>,
with the candidate, your name, whether the record was edited, and whether it has been
exported yet.

Then <http://127.0.0.1:8080/audit>, newest first, filterable by entity type. The
approval you just made is two rows:

- `approved` on the candidate, actor **your name**, `pending_review` → `approved as <record id>`;
- `created` on the approved record, actor `system (post-approval, as <your name>)`.

The second actor string is deliberate and worth ten seconds: the record was written by
software, in a transaction a named person opened, and the audit trail says both
things rather than pretending a person typed the JSON. If you edit a field before
approving, each changed field gets its own `edited` row with the before and after.
Nothing in this table is ever updated or deleted — neither runtime role holds delete
on it.

### 9:00 — The only way out is a file (1 minute)

<http://127.0.0.1:8080/export>

The page shows how many approved records are waiting to leave and the date range they
were approved in, pre-filled. Type your name in Operator and press **Produce the
batch**. The panel that comes back names the batch, the row count, the CSV path, the
manifest path and the sha256.

Then, in the terminal, show that the file is a file:

```bash
B=B0117                                 # the batch id the page just gave you
sha256sum exports/$B/$B.csv             # matches the hash on the page and in the manifest
jq . exports/$B/$B.manifest.json        # batch, operator, rows, range, reviewer names, sha256
head -1 exports/$B/$B.csv               # the 73 appendix E column names, in order
head -c 3 exports/$B/$B.csv | od -c     # 357 273 277 — the UTF-8 byte order mark
```

**Read the batch id off the page rather than guessing it.** The ids come from a
sequence that every rehearsal and every drill advances — it stood at 116 on this host
on 2026-09-12 — and a sequence is never rolled back, so the series has gaps and the
next number is not the next one you saw.

`make export OPERATOR='Full Name' FROM=YYYY-MM-DD TO=YYYY-MM-DD` is the same
operation from the command line; all three arguments are required, because a default
date window would silently decide which approved records are *not* in the batch.

Three sentences while that is on screen:

- **The byte order mark is not decoration.** Without it Excel and Zoho's import mapper
  read the file as the local codepage and every accented buyer name in the West
  African set arrives mangled.
- **A record is exported once.** A second export of the same range produces a batch
  with a header row and no rows, so "we ran it on Tuesday and there was nothing new"
  is a recorded fact rather than something somebody remembers.
- **There is no import path, no write-back and no CRM client anywhere in this
  repository.** The file lands in a directory. A named person imports it and checks
  each buyer against the CRM by hand, which is the only duplicate control there is,
  and every duplicate that gets through is counted at the week 14 gate.

### 10:00 — Close (30 seconds)

Three facts, and then stop talking:

1. It decided about 1,189 notices and asked a person about nine.
2. It cannot approve one of them. The database refuses the pipeline the privilege, and
   a test proves it on every commit.
3. What leaves is a file with a hash, produced by a named operator, listing the named
   reviewers who approved each row.

---

## If you have twelve minutes

Two extras, in this order of interest:

- **`uv run python scripts/drills/drill6_kill_export.py`** — kills an export at the
  moment between writing the file and stamping the records, and shows that the result
  is either a complete CSV with a matching manifest or no file at all, never a record
  marked exported for a file nobody has. It prints PASS, and it is the best answer
  there is to "what happens if it crashes halfway". Time it in rehearsal: it waits for
  a lock to appear in `pg_locks` before it kills anything, so it is not instant.
- **<http://127.0.0.1:8080/sources>** — every source with its health state, last
  success, consecutive failures and zero-yield runs. The line to say: a source that
  returns HTTP 200 and zero rows is a failure state here, not a quiet day, because
  that is exactly what a portal that changed its markup looks like.

## What not to promise

Say these plainly if they come up. They are all true today.

- **There is no precision or recall number, for any prompt version.** The 30-notice
  golden set is deliberately unlabelled: a person has to label it, because a set
  labelled by the same family of model it measures scores well against its own
  opinion. `make golden` refuses to run until then and exits 2 saying so. Do not put a
  quality number on this system in front of an audience.
- **The free filter is cheap, not precise.** What it passes today includes temporary
  staffing, 360-degree feedback consultancy and graphic design. That is the filter
  doing its job — cutting cost — and the scorer's job is relevance. Step 19 tunes it.
- **The West African portals are not connected.** Four feeds are live: TED, Prozorro,
  Find a Tender and the World Bank. West Africa is steps 17 to 19 and needs a browser
  container that does not exist yet.
- **Translation is its own metered stage and is not part of this walkthrough.** A
  notice in a language with no lexicon waits at `needs translation` rather than being
  dropped, and `make translate` is what releases it, at one model call each.
- **Nothing notifies anybody.** No email, no chat message, no webhook, by design. The
  reviewer works the queue on a schedule they set.
- **Do not run `make run` live** unless you have rehearsed it on that machine that
  morning. It fetches every enabled source, which takes minutes and can legitimately
  produce nothing new, and it spends real model budget. `make stage` before the demo
  is the beat that matters; a live fetch demonstrates patience.

## What the demo leaves behind

Not a sandbox. Beat 8 writes a real approved record and beat 9 writes a real export
batch and two real files, under your name.

- `approved_records` and `events` are **append only by grant**: neither runtime role
  can delete a row, so there is no undo in the app and there should not be.
- Removing a demo record afterwards needs the owner connection and a deliberate
  decision, which is a thing to arrange before the demo rather than after it.
- Export batch ids come from a sequence, so a rehearsal consumes a number. Gaps in the
  B-series are exports that did not finish — including any you killed in the extras.

If the demo must leave nothing at all, rehearse on a scratch database with its own
`.env`, and not by picking a candidate you hope nobody wanted.

## If something goes wrong mid-demo

| What you see | What it is | What to do at the keyboard |
| --- | --- | --- |
| `DATABASE_URL_REVIEW is not set` | The environment was not loaded in this shell | `set -a && . ./.env && set +a`, then restart `make review` |
| The queue is empty | Nothing staged, or everything scored below 60 | `make stage`; if it stages 0, switch to the drills and `/sources`, which need no queue |
| A candidate page 500s | The page builds the whole 73-column record on every load, so anything the builder refuses surfaces here | Go back to `/` and pick another row; do not debug in front of people |
| `permission denied for table approved_records` | The review app was given the pipeline's URL | Check `DATABASE_URL_REVIEW`. This is also beat 6 working, so you can use it |
| `CapExceeded` | The day's model budget is spent | Nothing in this demo needs a model call except a live `make run`. Skip that beat |
| `a reviewer name is required` when you did type a name | The name went into the reject form's box, not the approve form's | Each form has its own name field, on purpose |

`RUNBOOK.md` has the full version of every one of these, plus start, stop, health,
adding a source, changing a keyword, rolling back a prompt, where the logs are and
where the cost line is.
