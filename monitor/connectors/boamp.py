"""BOAMP, France's Bulletin officiel des annonces de marchés publics.

Recorded from real calls on 2026-09-12. The fixture is
`tests/contract/fixtures/boamp.json` and this parser is written against the
document shapes that are in it.

**Which host, and why this one.** BOAMP's notices are reachable by four routes and
three of them are closed to this pipeline. Every one was tried on 2026-09-12:

  1. `api.boamp.fr`, the documented Opendatasoft API - BLOCKED by this
     environment's egress policy (`CONNECT tunnel failed, response 502`). A
     blocked host is reported, not worked around (`scripts/check_egress.py`).
  2. `data.gouv.fr`, where the same dataset is catalogued - BLOCKED the same way
     (`Recv failure: Connection reset by peer`).
  3. `boamp-datadila.opendatasoft.com`, the Opendatasoft instance behind
     www.boamp.fr - reachable, HTTP 200, and **disallowed by robots.txt**. Its
     robots.txt (identical to the one www.boamp.fr serves) carries
     `User-agent: * / Disallow: /api/`, and allows `/api/` for `Googlebot` alone.
     Reading it would need either a user agent that disguises this client or
     ignoring robots.txt, and rule 21 forbids both. The portal says the same thing
     in its own words: "Pour accéder à cette API, vous devez accepter les
     conditions générales du portail ainsi que la licence du jeu de données."
  4. `echanges.dila.gouv.fr/OPENDATA/BOAMP/`, DILA's own open-data flux -
     reachable, no robots.txt at all (404, so nothing is disallowed), no
     registration, no key, no CAPTCHA. This is the route this connector reads, and
     it is the most first-party of the four: DILA is the publisher of BOAMP.

The flux is a dated Apache file index, one XML file per annonce:
`OPENDATA/BOAMP/{yyyy}/{mm}/{dd}/{idweb}.xml`. There is no query surface of any
kind - no CPV parameter, no type parameter, no index of what is in a day - so the
window is whole publication days and every file in them is fetched.

Four measured facts that shaped the request:

  1. **A day directory is the publication date and is closed once the day is
     over.** `DATE_PUBLICATION` is the directory's own date on all 414 files of
     2026-09-11, and across the eleven days visible on 2026-09-12 no day directory
     had been written to after its own midnight. So there is no client-side date
     cut to make and no sort to trust: the path is the filter.
  2. **A day directory fills all day long.** Of the 414 files published on
     2026-09-11, 223 were written in the 04:46 batch and the other 191 arrived
     between 07:45 and 19:45. A run that reads *today* sees about half of it. That
     is why the window is the two *completed* days before today: yesterday is whole
     by any morning schedule, and the day before it is the one run of overlap that
     lets a failed run be recovered by the next one instead of by hand.
  3. **The index's timestamps are Europe/Paris, not UTC.** The listing shows
     `2026-09-11 04:46` for the file whose `Last-Modified` header is
     `Fri, 11 Sep 2026 02:46:42 GMT`. Nothing here depends on that - the window is
     whole directories - but it is the fact that would be got wrong first by
     anyone who later tries to narrow the pass with the listing's own mtimes.
  4. **Notice count per day, measured over eleven days:** 105, 165, 205, 106, 172,
     298, 315, 393, 399, 402, 414. Never zero, weekends included. So a listing that
     parses to nothing is a failure state and raises here (rule 4) rather than
     reporting a quiet day.

**The one place this connector pays for a request it does not use.** The registry's
`exclude_notice_types` is acquisition scope everywhere else in this repository -
TED sends it in the query, DÖE reads it off the listing - so nothing is read and
then thrown away. BOAMP publishes no index, so the notice's own nature
(`GESTION/REFERENCE/TYPE_AVIS/NATURE`) can only be read from the file itself, and
the excluded ones are dropped after being fetched. Measured on 2026-09-11: 125 of
414 files, 30%. There is no cheaper route to the same decision and pretending
otherwise would mean either letting decided tenders into the reviewer's queue or
inventing a filter the source does not offer.

`Notice` has no notice-type column, which is the other half of why this is decided
here: an ATTRIBUTION that reached `notices` would be indistinguishable from a live
tender for the rest of the pipeline. It also carries `TITULAIRES`, the winning
bidders' names, on 77 of the 414 recorded files - third-party business records that
rule 19 keeps out of a model call, which is the same argument sources/simap.yaml
makes for excluding `participant_selection`.

**The payload is a JSON envelope around the XML as served.** `monitor/fetch.py`
reads every payload with `json.loads`, so an XML source has to arrive as JSON or
the shared acquire stage would have to grow a branch per source. The envelope is
the two things the file is addressed by and the bytes themselves: `day`, which the
mapper checks against the notice's own `DATE_PUBLICATION`, and `xml`, verbatim, so
the stored record is still the notice as published (rule 9).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The two completed publication days before today. Yesterday is whole by any
# morning schedule (fact 2 above) and the day before it is one run of overlap, so a
# run that failed is recovered by the next one rather than by a person writing a
# backfill. Lowering this to 1 halves the requests and removes that recovery.
LOOKBACK_DAYS = 2

# The absolute bound on notice requests per run, above the registry's expected_max.
# The two busiest consecutive days seen on 2026-09-12 held 816 files together, so
# this is that with headroom: a day that suddenly holds five thousand annonces stops
# here instead of turning a polite pass into an impolite one (rule 21).
MAX_NOTICE_REQUESTS = 1200

# Apache's autoindex writes one `<a href="26-87466.xml">` per file alongside its
# `?C=N;O=D` sort links and a parent-directory link. An annonce id is two digits of
# year, a hyphen and a sequence number, which is what tells a notice file from the
# rest of the page. The listing is served as ISO-8859-1 and every name in it is
# ASCII, so the charset never reaches this pattern.
NOTICE_FILE = re.compile(r'href="(\d{2}-\d+\.xml)"')

# Every one of the 414 files recorded declares `encoding="UTF-8"`. The bytes are
# decoded with that and nothing else: a file in another encoding is a changed flux
# and raises here rather than arriving as mojibake in a French lexicon match.
ENCODING = "utf-8"

# The notice's wrapper namespace, `jo:ann`. The elements this connector reads are
# all in the unprefixed BOAMP management block, so the namespace is only needed to
# recognise the root.
JO_ANN = "{http://boamp.journal-officiel.gouv.fr/XML/3.2.5}ann"

# The management block, which is the same on all four document formats BOAMP
# publishes (measured: 414 of 414 carry all three of these).
IDWEB = "GESTION/REFERENCE/IDWEB"
NATURE = "GESTION/REFERENCE/TYPE_AVIS/NATURE"
PUBLISHED = "GESTION/INDEXATION/DATE_PUBLICATION"

# The reviewer's page for one annonce. `https://www.boamp.fr/avis/detail/{idweb}`
# answers 301 to this, so the redirect target is what is stored: a click should not
# depend on a redirect still being there. Verified 200 on 2026-09-12.
NOTICE_URL = "https://www.boamp.fr/pages/avis/?q=idweb:{idweb}"


class BoampConnector(FeedConnector):
    """Two completed day directories, every notice in them, the excluded natures dropped."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused by the fetch: the flux has no classification parameter and there is
        # nothing in the index to test a code against, so the CPV decision is the
        # filter's at step 5. Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def days(self, today: date | None = None) -> list[date]:
        """The completed publication days this run reads, oldest first."""
        end = today or date.today()
        return [end - timedelta(days=offset) for offset in range(LOOKBACK_DAYS, 0, -1)]

    def day_url(self, day: date) -> str:
        """The directory index for one publication day.

        Built from the registry's `list_url` and not an `api_url`, because this
        source is a file index and not an API: `access: public_listing` in
        sources/boamp.yaml says the same thing.
        """
        return f"{self.source.list_url.rstrip('/')}/{day:%Y/%m/%d}/"

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        excluded = frozenset(self.source.exclude_notice_types)
        raw_notices: list[RawNotice] = []
        requests = 0
        skipped = 0

        for day in self.days():
            day_url = self.day_url(day)
            listing = client.get(day_url)
            listing.raise_for_status()
            names = listing_names(listing.text, day_url=day_url)

            for name in names:
                if requests >= MAX_NOTICE_REQUESTS:
                    log.warning(
                        "boamp_request_ceiling_reached",
                        requests=requests,
                        ceiling=MAX_NOTICE_REQUESTS,
                        day=day.isoformat(),
                    )
                    break
                if len(raw_notices) >= self.source.expected_max:
                    log.warning(
                        "boamp_ceiling_reached",
                        fetched=len(raw_notices),
                        ceiling=self.source.expected_max,
                        detail="raise expected_items_per_run in sources/boamp.yaml",
                    )
                    break

                response = client.get(day_url + name)
                response.raise_for_status()
                requests += 1

                text = response.content.decode(ENCODING)
                document = parse_document(text, reference=name)
                if notice_nature(document, reference=name) in excluded:
                    skipped += 1
                    continue

                raw_notices.append(
                    self.raw_notice(
                        url=NOTICE_URL.format(idweb=notice_idweb(document, reference=name)),
                        payload=envelope(day, text),
                        mime="application/json",
                    )
                )

        log.info(
            "boamp_fetch",
            notices=len(raw_notices),
            requests=requests,
            excluded=skipped,
            days=[day.isoformat() for day in self.days()],
        )
        return raw_notices


def listing_names(html: str, *, day_url: str) -> list[str]:
    """The notice file names of one day directory, in the order the index lists them.

    De-duplicated: an index that listed a name twice would otherwise be fetched
    twice and stored once, which reads as a polite pass in the log and is not one.

    Zero names raises. Every one of the eleven days visible on 2026-09-12 held
    between 105 and 414 notices, weekends included, so an empty index is Apache's
    autoindex turned off or a path that has moved - a failure state, not a quiet
    day (rule 4).
    """
    names: list[str] = []
    seen: set[str] = set()
    for name in NOTICE_FILE.findall(html):
        if name not in seen:
            seen.add(name)
            names.append(name)

    if not names:
        raise ValueError(
            f"BOAMP day index {day_url} lists no notice files; the flux layout changed or the directory index is off"
        )
    return names


def parse_document(text: str, *, reference: str) -> ET.Element:
    """One annonce file as an element tree, checked for the wrapper and the block every format shares.

    Takes the decoded XML rather than the bytes so that the connector and the mapper
    read the same function over the same string: the connector decodes the response
    once, stores that string in the envelope, and the mapper parses it back.
    """
    root = ET.fromstring(text)
    if root.tag != JO_ANN:
        raise ValueError(f"BOAMP notice {reference} has root {root.tag!r}, not {JO_ANN!r}")

    for path in (IDWEB, NATURE, PUBLISHED):
        if root.find(path) is None:
            raise ValueError(f"BOAMP notice {reference} has no {path}")
    return root


def notice_nature(root: ET.Element, *, reference: str) -> str:
    """`APPEL_OFFRE`, `ATTRIBUTION`, `RECTIFICATIF` or `MODIFICATION`.

    BOAMP states the nature as the name of the single child element of `NATURE`
    rather than as text, which is how the whole `TYPE_AVIS` block is written.
    Exactly one child on all 414 recorded files; anything else raises, because a
    nature that could not be read would be compared against the registry's exclude
    list and silently kept.
    """
    return sole_child(root.find(NATURE), path=NATURE, reference=reference)


def notice_idweb(root: ET.Element, *, reference: str) -> str:
    """The annonce number, `26-87466`. It is the external id and the reviewer's URL."""
    value = (root.findtext(IDWEB) or "").strip()
    if not value:
        raise ValueError(f"BOAMP notice {reference} has an empty {IDWEB}")
    return value


def sole_child(parent: ET.Element | None, *, path: str, reference: str) -> str:
    """The tag of the one child of a BOAMP code element. Raises on none or several."""
    children = list(parent) if parent is not None else []
    if len(children) != 1:
        raise ValueError(
            f"BOAMP notice {reference}: {path} has {len(children)} children "
            f"({[child.tag for child in children]}), expected exactly one"
        )
    return children[0].tag


def envelope(day: date, text: str) -> str:
    """The stored payload: the day the file came from, and the XML as served.

    See the module docstring. `monitor/fetch.py` reads every payload with
    `json.loads`, and the XML is carried through it untouched so the record is still
    the notice as published (rule 9).
    """
    return json.dumps(
        {"day": day.isoformat(), "xml": text},
        ensure_ascii=False,
        sort_keys=True,
    )
