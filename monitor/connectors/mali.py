"""Mali's national public procurement notices, SIGMAP `dossier-sigmap` endpoint.

Recorded from one real call on 2026-09-13: `GET
https://marchespublics.ml/portail/api/sigmap/dossiers/dossier-sigmap` answered
HTTP 200, `application/json`, no cookie, no token, and the fixture
(`tests/contract/fixtures/mali.json`) is that response verbatim - 98 rows, the
whole corpus, matching `sources/mali.yaml`'s own count on both 2026-09-12 and
2026-09-13.

**One request, no query parameters, the whole corpus every time.** Unlike TED,
Prozorro or the World Bank, `sources/mali.yaml` records no parameter this
endpoint honours at all - the Angular bundle calls it bare - so, the same shape
as `monitor/connectors/ebrd.py`, there is nothing to ask for and the window is
applied here rather than at the query. There is also no second, per-notice
endpoint to fetch: every field this connector reads - reference number, buyer,
title, notice type, status and date - is already on the listing row, so this is
a single GET, not a listing-then-detail pair the way Liberia, DÖE or EBRD are.

**The rows are not reliably sorted, so every row is read rather than walked.**
98 rows is too small a sample to trust a `srt`-style claim on, and none was
observed anyway: 96 of the 98 rows in the recorded fixture run newest to
oldest by `dosDate`, but the two oldest (2017-02-02 and 2017-05-24, both
outside any window this connector uses) sit out of that order at the tail. A
window that walked down and stopped at the first old row would still have
worked on this particular recording, but nothing here promises that holds
forever the way the search-page-with-a-required-sort connectors can promise it
of their own `sortField`, so this connector filters the whole array the way
`monitor/connectors/ebrd.py`'s `in_scope` does, and for the same reason: a
sort that quietly stops holding must not be able to look like a quiet week.

**Every one of the 98 rows carries `dosStatus: 1` and `dtpCode` `AAO` (52) or
`AMI` (46).** `sources/mali.yaml`'s own assessment says this endpoint answers
only pre-decision notices and names both facts as measured rather than
documented; `parse_dossiers` checks both on every row; and, going forward, a
build against decided or unknown-typed rows never happens by construction. A
third `dtpCode` or a non-1 `dosStatus` raises rather than being read as if it
were an ordinary AAO or AMI, per that entry's own warning that the app names
several other avis sub-types (annulation, prorogation, report, and a separate
manifestation-interet listing) this 98-row snapshot never surfaced a single
example of.

**WINDOW: 7 days.** `sources/mali.yaml`'s own two measurements a day apart put
the trailing-7-day count at 26 both times, arriving bursty by contracting
authority (13 of the 26 from two buyers issuing several notices the same day)
across a ~5-week visible window whose rolling 7-day sums ranged 2 to 28 with 0
to 14 published on any one day. A window as short as a day, or even two or
three, would read a legitimate quiet day as a zero-yield failure exactly the
way `monitor/health/source_health.py` is built to catch (rule 4); seven days
is the shortest window the registry's own measurement never showed empty, the
same reasoning `monitor/connectors/liberia.py` uses for the same figure. The
daily schedule (`sources/mali.yaml`, 11:30 UTC) re-reads six of those seven
days on every run; that costs nothing extra server-side, since there is only
ever the one request, and downstream deduplication is by content hash, not by
this connector remembering yesterday's ids.

**What every row carries, one example value each, from the recorded fixture's
first row (`id` 175527):**

    id          175527                      int, this connector's own record key
    dosId       238937                      int, SIGMAP's internal dossier id
    dosParId    null                        parent dossier id; null on all 98
    dosRevId    null                        revision id; null on all 98
    dosNr       "0639/P-2026"               the tender's own published reference
    mktId       639895                      int, the underlying "marche" id
    ctrId       1106                        int, contracting-authority id
    ctrName     "OFFICE DU NIGER"           contracting authority name (buyer)
    dptId       "10632100001001200"         department/budget-line code, a string
    dptName     "OFFICE DU NIGER"           issuing department name
    dtpCode     "AMI"                       notice type: AAO or AMI, see above
    lotNr       null                        lot number; null on all 98
    dosLnkId    null                        linked-dossier id; null on all 98
    dosLnkType  null                        linked-dossier type; null on all 98
    dosName     "Auscultations Barrage de Markala et Ouvrages annexes"  the title
    dosStatus   1                           int, always 1 on this endpoint
    dosContent  null                        null on all 98; no inline document text
    dosDate     "20260911"                  publication date, YYYYMMDD, always 8 digits

`ctrName` and `dptName` agree on every row sampled where both are the same
office; where they differ (not observed in this fixture but not ruled out by
it) they are still both institutional office names, never a person. **No row
carries a stated value or currency anywhere** - no `montant`, no `devise`, no
price field of any kind; that data lives only on the separate `marche-sigmap`
award endpoints this connector does not read (see `sources/mali.yaml`).
**No row carries any personal contact data** - no email, phone, fax or named
individual appears on this listing at all, only office-level `ctrName` and
`dptName` strings (e.g. "CAISSE MALIENNE DE SECURITE SOCIALE",
"MINISTERE DE L'EDUCATION NATIONALE"). Both findings are for whoever writes
`monitor/normalise/mali.py` next: there is no contact block to strip before a
model call (rule 19) because none is fetched here, and `estimated_value` /
`value_currency` on the mapped `Notice` should both be `None` for this source
until an award endpoint is read (rule 5: this connector's job is what SIGMAP
publishes on the pre-decision listing, not the separate awards data).

**There is no established per-notice URL.** The human page for this listing,
`https://marchespublics.ml/appels-offres`, is a single Angular route that
renders the whole list rather than one notice, and `sources/mali.yaml`'s own
assessment stops short of resolving a per-dossier detail route: the only
per-id endpoint it names, `apiUrlDownload`, downloads a *document* keyed by a
file id that comes from a per-dossier detail fetch this connector does not
make (documented there as "not pursued a second time", consistent with rule
21's one-polite-pass expectation). Inventing a deep-link URL pattern that was
never observed would be exactly the "built against documentation memory"
CLAUDE.md forbids, so `RawNotice.url` is the one page a person actually reaches
this data from, for every notice: `NOTICE_URL` below.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See WINDOW in the module docstring: the shortest window the registry's own
# two measurements never showed empty.
LOOKBACK_DAYS = 7

# Every field this connector's mapper (and the window cut) needs, present on
# all 98 rows of the recorded fixture. A missing one is a changed API, not a
# notice to skip (rule 4).
REQUIRED_FIELDS = ("id", "dosNr", "ctrName", "dptName", "dtpCode", "dosName", "dosStatus", "dosDate")

# The only dtpCode values measured across the whole 98-row corpus on both
# 2026-09-12 and 2026-09-13. sources/mali.yaml names several other avis
# sub-types the app's own route list carries that this endpoint has never
# surfaced a single example of; an unrecognised code raises rather than being
# read as an ordinary AAO or AMI.
EXPECTED_DTP_CODES = ("AAO", "AMI")

# The only dosStatus value measured. sources/mali.yaml: this endpoint answers
# only pre-decision notices; a decided one would carry a different status and
# belongs to the separate marche-sigmap endpoints this connector does not read.
EXPECTED_DOS_STATUS = 1

# The human page this data is rendered on. See "There is no established
# per-notice URL" in the module docstring for why this, and not a per-dossier
# deep link, is what RawNotice.url carries for every notice.
NOTICE_URL = "https://marchespublics.ml/appels-offres"


class MaliConnector(FeedConnector):
    """One GET of the whole corpus, filtered to the trailing window here."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: sources/mali.yaml states plainly that no CPV code appears
        # anywhere in this platform's JSON, so every notice reaches the
        # lexicon stage. Taken so every connector is built the same shape.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest dosDate this run keeps. Daily schedule, 7-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        response = client.get(self.source.api_url)
        response.raise_for_status()
        document = response.json()

        rows = parse_dossiers(document)
        wanted = within_window(rows, cutoff)

        ceiling = self.source.expected_max
        if len(wanted) > ceiling:
            log.warning(
                "mali_ceiling_reached",
                in_window=len(wanted),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/mali.yaml",
            )

        raw_notices = [
            self.raw_notice(
                url=NOTICE_URL,
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in wanted[:ceiling]
        ]

        log.info(
            "mali_fetch",
            corpus_rows=len(rows),
            in_window=len(wanted),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_dossiers(document: object) -> list[dict]:
    """Every row of the corpus, checked for shape and for both vocabularies.

    Three checks, in the order a failure is cheapest to explain:

      1. That the response is a bare JSON array, as it was on 2026-09-12 and
         2026-09-13, so a response wrapped in a new container (`{"items": [...]}`,
         say) raises instead of every row below failing with a confusing
         "not a dict" error.
      2. That every row carries what the window cut and the mapper need.
      3. That `dosStatus` and `dtpCode` are still the closed vocabularies
         measured on the whole corpus, so a status or type this endpoint has
         never answered with is never read as if it were an ordinary one.
    """
    if not isinstance(document, list):
        raise ValueError(f"Mali dossier-sigmap response is not a JSON array; got {type(document).__name__}")

    for position, row in enumerate(document):
        if not isinstance(row, dict):
            raise ValueError(f"Mali dossier-sigmap row {position} is not a JSON object; got {type(row).__name__}")
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"Mali dossier {position} ({row.get('id', '?')}) is missing {missing}")
        if row["dosStatus"] != EXPECTED_DOS_STATUS:
            raise ValueError(
                f"Mali dossier {row['id']} has dosStatus {row['dosStatus']!r}, not {EXPECTED_DOS_STATUS!r}; "
                "this endpoint was measured to answer only pre-decision notices, so a different status means "
                "either a decided dossier leaked onto this listing or the endpoint's own scope changed"
            )
        if row["dtpCode"] not in EXPECTED_DTP_CODES:
            raise ValueError(
                f"Mali dossier {row['id']} has dtpCode {row['dtpCode']!r}, not one of {EXPECTED_DTP_CODES}; "
                "sources/mali.yaml names other avis sub-types this endpoint has never surfaced an example of, "
                "and this one should be checked before it is read as an ordinary AAO or AMI"
            )
    return document


def within_window(rows: list[dict], cutoff: date) -> list[dict]:
    """Every row published on or after the cutoff, read from the whole array.

    Not a walk that stops at the first old row: see "The rows are not reliably
    sorted" in the module docstring. A sort that quietly stopped holding must
    not be able to look like a quiet week.
    """
    return [row for row in rows if dossier_date(row) >= cutoff]


def dossier_date(row: dict) -> date:
    """A row's `dosDate`, an eight-digit `YYYYMMDD` string. Raises on anything else.

    The window cut reads this, so an unparseable value is a changed API rather
    than a row to skip. Measured on all 98 rows of the recorded fixture: every
    one is exactly 8 digits, from `"20170202"` to `"20260911"`.
    """
    raw = row.get("dosDate")
    if not isinstance(raw, str) or len(raw) != 8 or not raw.isdigit():
        raise ValueError(f"Mali dossier {row.get('id', '?')} has dosDate {raw!r}, not an 8-digit YYYYMMDD string")
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError as cause:
        raise ValueError(f"Mali dossier {row.get('id', '?')} has dosDate {raw!r}: {cause}") from cause
