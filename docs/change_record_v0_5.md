# Change record v0.5: standalone application, regional model, SSO access, pilot geography

19 September 2026. Ryan Dear. Applies to EMEA Pilot Plan v0.4 and Technology and Architecture v0.4
(`docs/reference/`), which stay as issued. Where a section below is named, this record governs.
Weekend Build Plan v1.3 is historical and is not amended.

The decisions are 61 to 64 in `docs/open_decisions.md`. `CLAUDE.md` and `BUILD_ORDER.md` (steps 22a
to 22c) already carry them; this file exists so that someone reading the leadership documents knows
which parts no longer hold.

## What changed, in four lines

1. **Standalone application (61).** The Monitor is its own application with its own database, for 5
   to 10 named users. The export shaped to the CRM Opportunity record is unchanged. CRM integration
   is deferred and has no design in this pilot; the three integration options are withdrawn.
2. **Regional model (62).** Ten sales regions, every country in exactly one. One region is worked in
   the pilot; nine exist in the data model, users and reporting with no sources and no reviewers.
3. **SSO access (63).** Users sign in with the company identity provider through AWS Verified Access.
   The review app has no public listener. The typed reviewer name and the SSM port forward for users
   are gone. Identity provider (Entra ID or Google Workspace) is open.
4. **Pilot geography (64).** Europe & West Africa (41 countries) plus eight francophone West African
   countries as a recorded exception: 49. Bulgaria, Czechia, Hungary, Romania, Slovakia and Portugal
   leave; Andorra, Monaco, Armenia and Georgia join.

## Pilot geography

| | Countries |
| --- | --- |
| Europe & West Africa (Matthew, pilot) | AL AD AT BE BA HR CY DK EE FI FR DE GR IS IE IT XK LV LI LT LU MT MC ME NL MK NO PL SI ES SE CH UA GB, AM GE, GM GH LR NG SL |
| Pilot exception (owned by MENA & Francophone Africa) | BJ BF CI ML MR NE SN TG |
| Left the pilot | BG CZ HU RO SK (Central & Southeast Europe), PT (Lusophone) |
| Joined the pilot | AD MC AM GE |

The exception exists because six West African connectors were live before the regional split and
five of them serve francophone countries. Candidates from those countries carry their owning region;
the exception decides only that the pilot sources, stages and reviews them. It ends when the owning
region is activated.

## Pilot Plan v0.4, sections superseded

| Section | What no longer holds | What holds now |
| --- | --- | --- |
| Title, 1 Verdict | 47 countries; "handed to Zoho as a file"; the three-reason framing around Zoho Creator | 49 countries by region; a standalone application whose approved records export as a file shaped to the CRM Opportunity record |
| 2 Pilot scope | 34 Europe + 13 West Africa; "Explicitly deferred" row naming the integration options | The geography table above; CRM integration deferred with no design |
| 3 Daily workflow | Reviewer "checks Zoho by hand"; import operator "loads it into Zoho" | Reviewer checks the CRM by hand; BD's import operator loads the file into the CRM |
| 5 Sources, "What the BD worksheets change" | D26 proposal to grow to 53 countries; country owners as listed | Scope is set by the regional model; country owners follow the sales-regions workbook |
| 6 Plan, phase 1 exit | Dry-run import "into a Zoho sandbox" | Into a CRM sandbox, by BD |
| 6 Plan, phase 4 | "Zoho integration if the gate takes it" | Removed; any integration is new work after the gate |
| 7 Costs | Zoho line; one reviewer | No CRM line; 5 to 10 users; add AWS Verified Access (confirm price in ca-central-1 at step 22c) |
| 8 Risks | "Named reviewer with real time" as a single person | Named reviewers per region in `config/users.yaml`, including a reviewer for the francophone exception |
| 9 Decisions requested | Approve 47 countries; endorse D31 in its Zoho wording | Approve 49 countries by region; D31 stands as restated by 61 |
| 10 What I still need | "Matthew's priority countries within the 47"; confirmation about checking Zoho | Priorities within the 49; the open items in `docs/open_decisions.md` |

## Architecture v0.4, sections superseded

| Section | What no longer holds | What holds now |
| --- | --- | --- |
| 1, 3 System context | 47 countries; "Zoho appears exactly once in this design" | 49 countries; the CRM appears only as the destination of a file |
| 4 "How an approved record reaches Zoho" | Title and vendor wording | "How an approved record leaves the system": unchanged mechanism, CRM-neutral |
| 4, 5 Review app | Bound to localhost, reached over an SSM port forward, reviewer name typed | Private instance behind AWS Verified Access with OIDC to the company identity provider; identity from the signed assertion; authority by region (rules 23 to 25) |
| 6 Deployment | "No inbound network path at all"; reviewer reaches the app through SSM | No public inbound path. The Verified Access endpoint is the only route to the review app. SSM stays for administration only |
| 7 Data model | No region or user entities | `regions`, `region_countries`, `pilot_exceptions` (migration 017); `users`, `user_regions` (018); `candidates.region` a foreign key; decisions record `reviewer_user_id` |
| 9.3 Pilot operations | One reviewer, two region views | Reviewers by region; the pilot region and the exception measured separately |
| 9.5 The integration decision | The three options and the current read | Withdrawn. CRM integration is deferred with no design |
| 10 Decision register | D2, D3, D10, D11, D28, D29, D30 wording that names Zoho components | Already superseded by D31; add 61 to 64 |
| 10 Open, reviewer rows | "One reviewer for EMEA; one per region"; "Keep 47" | Resolved by 62 and 64 |
| 11 From Matthew | Checking Zoho by hand | Checking the CRM by hand; the open items for Matthew in `docs/open_decisions.md` |
| 12 Risks | "A column the Zoho import mapper rejects" | A column the CRM import mapper rejects |
| Appendix D, identity matrix | No user identities | Add the Verified Access trust provider and the `users` table; no identity holds a CRM credential |
| Appendix E, export columns | Vendor wording on Account, Owner and import | Columns, order, naming and placeholders unchanged |

## What did not change

The checkpoint (one write path, two runtime roles, the database grant and its test), the export
file and its column specification, the acquisition-ethics rules, the model caps, the staging
threshold, the West African geography weights, the step order before 22a, and every gate from step 23
onward except that 22a to 22c now also gate shadow entry.
