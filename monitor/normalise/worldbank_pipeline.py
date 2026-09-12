"""World Bank pipeline project -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/worldbank_pipeline.json`, recorded
2026-09-12. The request that produced it, and the filter hazard that shaped it, is
in `monitor/connectors/worldbank_pipeline.py`'s docstring.

Six things the recorded set settled.

1. **There is no RFP or EOI date in this API, and `deadline_at` carries the
   planned Board approval date instead.** A pipeline project has not been
   approved, so nothing has been tendered and no closing date exists to map. The
   dates the API does offer are `boardapprovaldate` (42 of 42, and the same value
   as `milestones[0].apprvl_date`), `public_disclosure_date` (42 of 42),
   `closingdate` (32, and it is the *loan's* closing date years after
   implementation starts), and inside `milestones` the sparse planning dates:
   `begin_apprsl_date` on 20, `decsn_review_date` on 25, `effctvnss_date` on 27.
   The appraisal date that the step's name suggests is therefore on fewer than
   half the rows, which is why it is not the one carried.

   `boardapprovaldate` is the planned date the Board approves the operation. It is
   not a date on which an RFP is released and this module does not claim it is;
   what it is, is the only forward-looking date present on every row, and the
   procurement plan for an operation becomes live around that approval. It is in
   the future on 37 of the 42. The other 5 are stale entries whose planned date
   has slipped past without the status changing - P173108, Nigeria's Beneficial
   Ownership Transparency project, was disclosed in March 2020 with a Board date of
   February 2020 and last updated in December 2022, and it is still marked
   Pipeline. That date is what the Bank publishes, so it is what is stored; moving
   it to something more plausible is the invention rule 10 forbids, and the
   reviewer sees the real one next to a disclosure date six years old.

   `monitor/stage/record.py`'s `deadline_for()` routes this to appendix E's
   "Expected Release of RFP/EOI?" column rather than to a submission deadline,
   which is correct only while the candidate's `procurement_type` is "other". That
   field is the scorer's output and not a value this module can set: `Notice` has
   no `procurement_type` column and `Source` forbids unknown keys, so neither the
   normaliser nor the registry entry can assert it. What the pipeline does have is
   `monitor/score/prompt.py`'s own rubric - "other: anything else, including a
   donor pipeline entry that is not yet a tender" - which the model can only apply
   if the notice says that is what this is. Hence point 2.

2. **The body is composed from the published fields, and it leads with the
   status.** The model is shown a title, a body and a line reading
   `Deadline: <date>`; nothing else in `monitor/score/client.py`'s user message
   distinguishes a tender from a project that has not been tendered. A body of
   `pdo` alone - the single-field mapping `monitor/normalise/fts.py` uses, because
   OCDS has a single description field - would hand the scorer an infrastructure
   project with an approval date labelled "Deadline" and no way to tell it apart
   from a live bid. So the body opens with the two published fields that say what
   this record is, `status` and `last_stage_reached_name`, then the planned
   approval date, then the Bank's prose. Every line is a published value under a
   field label; no sentence is written here that the Bank did not publish.

   The consequence is that the content hash covers the stage and the planned date,
   so a project that advances from Concept Review to Begin Appraisal, or whose
   Board date is revised, is stored again and scored again. That is intended: both
   changes move the opportunity closer and are the reason a BD reader watches a
   pipeline. One snapshot cannot measure how often it happens; the ceiling is 42
   model calls in a day against rule 22's cap of 600.

   `last_stage_reached_name` across the 42: Concept Review 20, OIS Sign-off 8,
   Begin Appraisal 5, Begin Negotiation 4, Decision Meeting 4, Technical Design 1.

3. **No financing amount is carried into `estimated_value_usd`.** The API states
   several - `totalamt`, `curr_total_commitment`, `lendprojectcost`,
   `curr_project_cost`, and per-financier `fincr_usd_amt` - and they are real
   published USD figures, but every one of them is the cost of the whole
   operation. P518248's USD 85,000,000 buys a dam and an irrigation perimeter.
   Putting that in front of the scorer as "Stated value (USD)" and through it into
   appendix E would answer a question nobody asked: the size of a PFM component
   inside a transport or agriculture loan is not the size of the loan. The figures
   stay in the stored payload for anyone who needs them, and they are kept out of
   the body for the same reason they are kept out of the field.

4. **The buyer is `impagency`, the implementing agency, and it is empty on 7 of
   42.** `borrower` is the sovereign or the finance ministry that signs the loan;
   `impagency` is the agency that will run the operation and therefore procure.
   They are different things rather than two selectors for one value, so there is
   no fallback between them (rule 1) - and the question does not arise anyway,
   because the two are absent on exactly the same 7 rows, all of them projects the
   Bank has disclosed before naming an agency. An unnamed buyer is left empty and
   is visible as empty; `Notice.buyer` defaults to "" for exactly this.

5. **The language is English and the source does not state it.** A project row has
   no language field at all, `apilang=fr` and `apilang=es` are ignored and return
   the English edition, and the public page is served under `/en/`. All 42 recorded
   `project_name` and `pdo` values are English prose. So `language` is "en" with
   confidence 1.0 - a claim resting on the endpoint serving one edition, not on a
   detector, and there is no second path here that would read a different one.
   Nothing is translated (rule 9) and there is nothing to translate.

6. **`countrycode` is a list and all 42 recorded rows carry exactly one code**, and
   they are ISO 3166-1 alpha-2 already - the opposite of the notices API, which
   matches on the Bank's own spellings and needs a table to get back to ISO. Every
   code the connector's filter admits is one of the registry's 19, and all 19 are
   in `monitor/normalise/codes.py`'s `COUNTRY_NAMES`, so the export's
   `country_name()` has a name for each. A row naming two countries raises rather
   than having one picked for it: `Notice.country` holds one country, and which one
   a regional operation belongs to is a decision a person takes.

`id` ("P517776") is the Finance Project ID of Architecture v0.4 appendix E, which
`monitor/stage/record.py` carries as `finance_project_id` and currently leaves
empty. It is `external_id` here; the `notices` table has no column that carries it
through, and adding one is outside this module's lane. Noted so the step that adds
the column knows the value is already in hand, and that on this source it is also
the notice's own identity.
"""

from __future__ import annotations

import structlog

from monitor.connectors.worldbank_pipeline import PROJECT_URL
from monitor.models import Notice
from monitor.normalise.codes import country_alpha2
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "worldbank_pipeline"

# From sources/worldbank_pipeline.yaml, which is the authority; the contract test
# asserts the two agree. The project is published by the financier rather than by
# the buyer, which is what lets a pipeline entry and the national notice for the
# procurement it eventually produces land in one candidate cluster.
ADMIN_LEVEL = "donor"

# Point 5 of the module docstring. The endpoint serves one edition and states no
# language; 42 of 42 recorded rows are English prose.
LANGUAGE = "en"

# Trailing punctuation left on an agency name by the Bank's own data entry:
# "Public Company Republic of Srpska Motorways (PC RS Motorways)," is 1 of the 35
# named agencies. The comma separates the agencies in a multi-agency value and is
# an artefact when it is the last character, not part of the name the reviewer
# proposes to the CRM.
BUYER_TRIM = " ,"


def map_notice(raw: dict) -> MappedNotice:
    """One World Bank pipeline project to a Notice. Raises on anything unmappable."""
    external_id = raw["id"]
    url = PROJECT_URL.format(id=external_id)

    title = raw["project_name"].strip()
    if not title:
        raise ValueError(f"{external_id}: project has no project_name")

    # Parsed once and used twice, so the date in the body and the date in
    # `deadline_at` cannot disagree, and so the body never states a date the
    # deadline rule could not read.
    approval_at = parse_deadline(raw["boardapprovaldate"], source_id=SOURCE_ID, url=url)
    body = compose_body(raw, approval_at)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        buyer=buyer(raw),
        country=country_alpha2(single_country(raw)),
        admin_level=ADMIN_LEVEL,
        # `public_disclosure_date` is "2026-07-23", a day with no time.
        # `parse_published` gives a bare date its midnight, which is what the other
        # mappers do and what keeps the value aware on its way into timestamptz.
        published_at=parse_published(raw["public_disclosure_date"], source_id=SOURCE_ID, url=url),
        # The planned Board approval date, not a bid deadline. Point 1 above.
        deadline_at=approval_at,
        language=LANGUAGE,
        language_confidence=1.0,
        # No CPV code and no procurement value anywhere in this source; the lexicon
        # stage decides these and point 3 explains the value.
        body=body,
        status="detected",
    )
    # Published in English, with no separate English rendering to carry. Empty
    # means "no English was published", never "the original was English"
    # (monitor/normalise/mapped.py), so step 14 is not asked to translate English
    # into English and nothing here claims a translation happened.
    return MappedNotice(notice=notice)


def compose_body(raw: dict, approval_at) -> str:
    """The published fields that describe the project, under their field labels.

    Point 2 of the module docstring: the status and the stage lead, because they
    are what tells a reader - and the scorer, whose rubric has a category for it -
    that this is a project with no tender rather than a bid to respond to.

    `project_abstract` is on 30 of the 42 and is omitted where it is absent rather
    than labelled and left blank, which would read as a project with nothing to say
    about itself. `pdo` is on all 42, 83 to 299 characters.
    """
    blocks = [
        f"Project status: {raw['status']}",
        f"Stage reached: {raw['last_stage_reached_name'].strip()}",
    ]
    if approval_at is not None:
        blocks.append(f"Planned Board approval: {approval_at.date().isoformat()}")

    objective = (raw.get("pdo") or "").strip()
    if objective:
        blocks.extend(["", "Development objective:", objective])

    abstract = (raw.get("project_abstract") or "").strip()
    if abstract:
        blocks.extend(["", "Project abstract:", abstract])

    return "\n".join(blocks)


def buyer(raw: dict) -> str:
    """`impagency`, the agency that will run the operation and procure for it.

    Empty on the 7 recorded rows where the Bank has not named one, which are the
    same 7 on which `borrower` is also absent. Point 4 of the module docstring.
    """
    return (raw.get("impagency") or "").strip(BUYER_TRIM)


def single_country(raw: dict) -> str:
    """The one ISO2 code on `countrycode`. More than one, or none, raises.

    The connector has already proved at least one of them is covered; this is the
    separate question of which single country the notice carries. All 42 recorded
    rows name exactly one, and a regional operation naming two is a real change
    that needs a person rather than a silent pick of the first (point 6).
    """
    codes = raw["countrycode"]
    if not isinstance(codes, list) or len(codes) != 1:
        raise ValueError(
            f"{raw['id']}: countrycode is {codes!r}; a Notice carries one country and all 42 recorded "
            "projects name exactly one"
        )
    return codes[0]
