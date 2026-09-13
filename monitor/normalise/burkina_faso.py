"""One DGCMEF Quotidien bulletin (as text) -> the notices in its AVIS section.

Written against `tests/contract/fixtures/burkina_faso_avis.pdf`, pages 20 to 47 of
Quotidien n°4478 (2026-09-01), read the way `monitor/fetch.py` reads a bulletin:
`monitor.normalise.ocr.extract(path).text`, which is `pdftotext -layout`. Every
rule below was measured on that text (its manifest, `burkina_faso_avis.json`,
records the counts), and the number beside each rule is what it measured.

The connector yields one `RawNotice` per issue PDF, so this is the one mapper that
returns a list (decision 51): `map_notices(text)` segments the bulletin into its
notices and maps each. It maps and never filters (rule 5): every notice marker
becomes a `Notice`, PFM or not, and the free filter decides later. Nothing here is
a model call.

**Segmentation: one notice per marker line, and a marker is a CENTRED line that
begins with a marker phrase.** The phrase "Avis de demande de prix" occurs 50
times in the fixture and only 25 of them are notices: the other 25 are recital
prose ("Cet Avis de demande de prix fait suite au Plan de passation...") and a
wrapped continuation line that happens to begin with "manifestation d'intérêt a
été publié dans la revue...". What separates the heading from the prose is not
the words, it is the layout: every notice head (section, buyer, title, marker,
reference, financing) is centred on the page and `pdftotext -layout` renders
centring as leading whitespace, which is the reason `monitor/normalise/ocr.py`
runs it in layout mode. Measured: the 28 marker lines start at column 47 to 66;
no prose line in the 28 pages starts past column 15 (a bulleted qualification
list); the other centred head lines start at 21 to 40 and centred title lines at
17 to 40. `CENTRED_FROM_COLUMN` = 16 sits in the gap. The same rule keeps the
RESULTATS PROVISOIRES pages out (`fetch.py` hands the mapper the whole issue, not
just AVIS): `burkina_faso_sample.pdf` prints "Manifestation d'intérêt N°2026-03
pour la constitution d'une base de données..." as a results-table row at column
3, and it is not a marker. The marker phrases are structural properties of this
publication format, not scoring vocabulary, which is why they are a module
constant and not a lexicon entry (rule 6): they name the procedure the notice
opens, exactly as the table of contents on page 1 names the sections. Count on
the fixture: 25 "Avis de demande de prix" (five capitalisations, one with a
trailing colon), 2 "Avis d'Appel d'Offres" (Ouvert Accéléré (AAOA), Ouvert
(AAOO)), 1 "Avis de Manifestation d'Intérêt"; "Demande de propositions" and
"Avis de sollicitation" do not occur, and the constant carries them because the
brief names them as forms this gazette publishes. Two more head forms were
measured on the four other issues read on 2026-09-13 and are in the constant
because without them a notice is silently merged into the body of the one
before it: "Avis à manifestation d'intérêt" (4471, 4477, 4483, 4486, also
"...additif", "...COMPLEMENTAIRE" and "Avis d'appel à manifestation d'intérêt"),
and a bare "Demande de prix" heading (4471 p.80, 4477 p.55, 4486 p.68). The bare
form is anchored to the whole line, the phrase and at most a colon, because
centred lines wrap: 4483 has two title lines beginning "demande de prix alors"
and 4471 one beginning "demande de prix (5 ans expérience". A bare
"manifestation d'intérêt" is NOT a form: zero heads print it in five issues and
the one line that begins with it is a results-table cell. "Demande de prix
N°2026-034/..." on one line is not one either: all four occurrences are results
fiches ("FICHE SYNTHESE RECTIFICATIVE").

**A heading can wrap, and a centred phrase line directly beneath a marker line
is the same heading's second line, not a second notice.** Issue 4485 (p.43)
prints, under "Prestations intellectuelles" / "MINISTERE DE LA CONSTRUCTION DE
LA PATRIE" / a title, the heading "AVIS A MANIFESTATION D'INTERET EN VUE D'UNE"
over "DEMANDE DE PROPOSITIONS ALLEGEE": one sentence too long for one centred
line, both halves beginning with a phrase from the constant. Read as two
markers, the second had no section line of its own and the second live run
raised on it. The page shows one complete head and one heading, so the rule is
the page's: the marker is the first phrase line and the continuation is part of
the heading, and it is the only such wrap in six issues (241 marker lines). The
section-line requirement stands - page 43 has one, like every other head.

**The AVIS part begins at the first complete notice head, and phrase lines
before it are results text.** A full issue is RESULTATS PROVISOIRES first, then
AVIS (the contents page on every issue), and the results tables are centred
text too: "manifestation d'intérêt (Au minimum 600 000 000 FCFA)" at column 61
(4471), "demande de prix" at column 110 (4477). A marker rule alone would raise
on three of five issues there. So the first marker with a section line above it
(back to the previous marker) opens the AVIS part; every phrase line before it
is skipped and counted in the log. Measured: that head is the first AVIS page on
all five issues (4471 p.61, 4477 p.41, 4478 p.20, 4483 p.44, 4486 p.50), and the
contents page is off by one from it on three of them. After it a marker with no
section line above it raises, as a change in the format should.

**Buyer: the line(s) directly beneath the section line.** Every notice head
opens with a centred section line - "Fournitures et Services courants",
"Travaux" or "Prestations intellectuelles", the gazette's own three sections,
which the registry entry asks this module to read - and the buyer is the run of
non-blank lines beneath it, then a blank, then the title, then the marker. The
buyer is found by position and not by capitals, and that was learned on the four
other issues: "nearest all-caps line with a blank beneath it" was right on all 28
notices of the fixture and wrong on three real shapes elsewhere - an all-caps
title with a blank on either side (4471 p.80: "ACQUISITION ET LIVRAISON SUR SITE
DE MATERIEL ET OUTILLAGE SCOLAIRE" under "REGION DE LA BOUCLE DU MOUHOUN"), a
second caps line naming the commune beneath the caps title (4477 p.53: "COMMUNE
DE BAGASSI" under "REGION DE BANKUI"; it becomes part of the title), and a buyer
that is not all caps at all (4483 p.44: "ECOLE NATIONALE DE SANTE PUBLIQUE « Dr.
COMLAN ALFRED A. QUENUM »"). The fixture's own last notice, whose three-line
title is in capitals, is read the same way. Accented capitals print unaccented
("SUPERIEUR", "L'EDUCATION") and nothing here depends on case. A marker inside
the AVIS part with no section line above it, or a section line with a blank
beneath it, raises with the marker line in the message (rule 4): a notice with
no buyer is a layout change and not a notice to carry quietly.

**Title: the non-blank lines between the buyer run and the marker, joined.** One
to three lines in the fixture; one notice (ADEU, page 30) has a blank line
between title and marker, which is why blanks are skipped rather than used as
the end. No title raises.

**Admin level: `local` under a COMMUNE or REGION heading, `national` otherwise.**
`AdminLevel` allows national, regional, local, donor. The bulletin's "Régions"
part heads each notice with the region it falls in, and in all 19 REGION-headed
notices in the fixture the contracting authority named in the prose is a commune
(Boromo under REGION DE BANKUI, Kombissiri under REGION DU NAZINON, Niangoloko
under REGION DES TANNOUNYAN...), never the region itself. So the heading says
sub-national and the measured sub-national level is local; `regional` would be
wrong on every one of the 19. The buyer field still carries the heading as
printed, because the mapper reads structure and not sentences: the commune's name
is in the body for the reviewer. Counted: 5 national (two institutes, a school, an
urban development agency, a regional hospital under the health ministry), 23
local (4 COMMUNE, 19 REGION).

**Reference: the first "N° YYYY-NNN/ORG/..." in the centred head lines from the
marker down.** It is on the line after the marker on 25 notices, on an "Identifiant de
la DPX :" line on one, and four lines down on the AAOO (after "Autorité
contractante" and "Identification de l'AAO"). It is the FIRST such number because
the AAOA prints a second one on the next line ("Suivant autorisation de recours à
la procédure accéléré N°2026-2003/..."), and it is bounded to the centred head
because the prose cites decree "N°2024-1748/PRES/PM/MEF" in the same shape. Inner
spaces ("MS/SG/ CHR-KAYA /DG/PRCP"), a stray "°" ("N° :°2026-002/..."), a doubled
"N° : N°2026-06/..." and a trailing full stop are typesetting and are removed. All
28 carry one; a notice without one is carried with an empty `external_id` and a
log line, since the content hash identifies it.

**Deadline: the first "au plus tard le" or "avant le" followed by dd/mm/yyyy in
the body, parsed by rule (rule 10).** The manifest counts "date limite" 24 times
and "dépôt des offres" 8 times; every one of them is the validity clause ("à
compter de la date limite de dépôt des offres") and none carries a date, so the
phrase the brief named is not this format's anchor. The submission clause is. It
wraps across lines ("au plus\\ntard le 10/09/2026" on page 25), so the search
runs on whitespace-collapsed text. Day before month is established, not assumed:
the reference lines print "du 24/08/2026" and "du 25/08/2026", and the one
notice that writes its deadline in words ("le jeudi 10 septembre", page 32) names
the same day the 27 others print as 10/09/2026, which is a Thursday. That notice
is also one of the two with no parsed deadline: no year is printed and this module
does not supply one (`dates.py`: a wrong deadline is worse than a missing one).
The other is the last notice, truncated at page 47 before its clause 9. One
deadline is printed "01/10/ 2026" with a stray space; whitespace around the
slashes is tolerated the way `sierra_leone.py` tolerates "30- 04-2026", because
it cannot change which number is the day. `dates.py` takes the reshaped ISO date
and makes it the end of that day in UTC, which is also Ouagadougou time. The hour
printed beside it ("à 09 heures 00") stays in the body. Measured: 26 of 28.
On one notice (Ouo, page 45) the submission clause prints no date and the first
"avant le 10/09/2026" is the document-consultation clause; it agrees with the
bid-opening date in clause 12 and is what the rule yields.

**Value: from the first "Montant prévisionnel" (or "Budget prévisionnel",
"Enveloppe financière prévisionnelle") in the body to the end of that paragraph;
the first HTVA figure if one is tagged, else the first TTC figure, else the first
figure.** A header line is the first occurrence on 25 notices (21 "Montant
prévisionnel", 2 "Budget prévisionnel", 2 "Enveloppe financière prévisionnelle");
on the other 3 (EPO, Foutouri, the AAOA) it is the recital sentence "Le montant
prévisionnel de la présente demande de prix est de ...", which is the only place
those three print it. The manifest's "22-23 notices carrying a figure" counted
"Montant prévisionnel" header lines only; with the recital and the two header
variants read, all 28 carry one. HTVA over TTC: the pre-tax figure is the contract value, the TTC one
is the same value with 18% VAT, and the CRM record wants one number that is
comparable across sources that mostly publish net of VAT; when a notice prints
TTC first and HTVA second (Toma, Laye) the HTVA figure is still the one carried.
Two notices spell the tag "HTV", accepted as HTVA. Spelled-out numbers always
carry the digits in parentheses and only the digits are read. A figure is a run
of space-separated thousands groups, which excludes "(03)", "(05)" and "2026"
from a paragraph that lists lots. A multi-lot notice carries its first figure
under the rule above, which is the printed total where the notice prints one
first (Yaba) and lot 1 where it does not (Boromo, Sourgoubila, Laye, Banfora,
Tenkodogo): never a sum, because a sum is a figure the notice did not print.
The currency is the publication's: every amount in this gazette is in the CFA
franc (XOF), spelled "FCFA", "F CFA", "francs CFA", "francs cfa", "Francs CFA",
"franc CFA", "en FCFA", and once "francs CF"; one of the ten sample lines names no
unit at all ("9 322 034 HTVA et 11 000 000 TTC") and is in francs like the rest.
`published_value` in `monitor/normalise/value.py` applies the shared guards.

**Body: the notice's own text from its marker line to the line before the next
notice's head, with page furniture removed.** Furniture is the page footer and
the "www.dgcmef.gov.bf  www.finances.gov.bf" line, both of which change with the
issue and the page and would make a notice reprinted in the next issue hash as
new. The footer is one line of two stamps and a page number on 4477, 4478 and
4483 ("N°3827 – lundi 04 Mars 2024   N° 4478 - Mardi 01 septembre 2026   26")
and two lines on 4471 and 4486 ("N°3827 – lundi 04 Mars 2024   98" then "N° 4486
– Vendredi 11 septembre 2026"), so a footer line is one or two stamps with an
optional page number, and both layouts are stripped. The next head begins at its
section line ("Travaux", "Fournitures et Services courants", "Prestations
intellectuelles"), the centred line directly
above the buyer; it belongs to the bulletin's organisation and not to either
notice, so it is in neither body. The "Financement :" line stays in the body: it
is the head's own text and the reviewer reads it there. Leading layout
whitespace is stripped per line and blank runs collapsed; line breaks stay. Text
before the first marker (cover, contents, results) is not a notice and is
dropped; text after the last notice (in a full issue, the closing page) stays in
the last body.

**Published: the date the issue prints beside its own number.** The issue number
comes from the document's URL, whose file name carries it ("Quotidien
N°4478.pdf", "Quotidien n°4486_0.pdf", "Quotidien N°4480 (bis).pdf" once
URL-decoded): the digits after "n°", case-insensitively, and no digits raises.
The date is then the first line in the text matching "N° <that number> [–-]
<weekday> dd <month> yyyy", which is the masthead near the top of page 1 on all
five issues read on 2026-09-12/13 (4471, 4477, 4478, 4483, 4486: "N° 4486 –
Vendredi 11 septembre 2026", "N° 4477 - Lundi 31 août 2026", en dash or hyphen,
on 4471 and 4486 sharing the line with the masthead's "IFU : 00003223T"). Tying
the date to the number is what makes the stale template stamps harmless: every
issue prints "N°3827 – lundi 04 Mars 2024" in its footers, 4471 and 4486 print
"N° 4106(bis) – Vendredi 28 mars 2025" there too, and the first live run (4486)
failed on a footer-based rule for exactly that reason. No such line raises,
naming the number (rule 4). Reshaped to ISO and handed to `dates.py`, midnight
UTC. It is the publication's own text (rule 9) and the month table is the
connector's, imported, so the spelling of a month lives in one place. Not
measured: a combined issue ("Quotidien n°4473-4474.pdf") yields number 4473 and
its masthead may print "N° 4473-4474"; none of the five is combined.

**Document and URL.** `map_notices` takes `{"url": ..., "text": ...}` as
`fetch.py`'s PDF decoder hands it over (decision 51): the issue's own URL goes on
every notice, because the issue is what a reviewer opens, the way Mali's notices
all carry the shared listing page. A missing key raises.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from urllib.parse import unquote

import structlog

from monitor.connectors.burkina_faso import MONTHS_FR
from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "burkina_faso"
COUNTRY = "BF"
LANGUAGE = "fr"

# The registry declares fr and every page of the fixture is French; asserted by the
# source rather than detected here.
LANGUAGE_CONFIDENCE = 1.0

# The gazette's currency. See the module docstring on why it is the publication's
# and not read per line.
CURRENCY = "XOF"

# A line is centred, and so part of a notice head, at or past this column. Measured
# on the fixture: markers at 47-66, other head lines at 21-40, prose at 0-15.
CENTRED_FROM_COLUMN = 16

# The procedure headings this gazette prints above a notice. Structural to the
# format (see the module docstring), so a constant and not config. Both apostrophes
# and both accent renderings occur in the fixture.
MARKER = re.compile(
    r"^(?:(?:avis de demande de prix"
    r"|avis d[’']appel d[’']offres"
    r"|avis (?:de |(?:d[’']appel )?[àa] )manifestation d[’']int[ée]r[êe]t"
    r"|demande de propositions"
    r"|avis de sollicitation)\b"
    r"|demande de prix\s*:?$)",
    re.IGNORECASE,
)

# The gazette's three sections, printed centred above every notice's buyer line.
SECTION = re.compile(r"^(?:fournitures et services courants|travaux|prestations intellectuelles)$", re.IGNORECASE)

# Buyer headings whose first word places the notice in the bulletin's "Régions" part.
SUB_NATIONAL_HEADINGS = ("COMMUNE", "REGION")

# One issue stamp as the footers print it: "N° 4478 - Mardi 01 septembre 2026",
# "N°3827 – lundi 04 Mars 2024", "N° 4106(bis) – Vendredi 28 mars 2025".
MONTH_NAMES = "|".join(MONTHS_FR)
STAMP = rf"N°\s*\d{{4}}(?:\s*\(bis\))?\s*[–-]\s*\S+\s+\d{{1,2}}\s+(?:{MONTH_NAMES})\s+\d{{4}}"

# Page furniture: a footer line of one or two stamps and an optional page number
# (see the module docstring for both layouts), and the two-site line.
FOOTER = re.compile(rf"^\s*(?:{STAMP}\s*){{1,2}}(?:\d{{1,3}})?\s*$", re.IGNORECASE)
SITES = re.compile(r"^\s*www\.dgcmef\.gov\.bf\s+www\.finances\.gov\.bf\s*$")

# The issue number in the decoded file name: "Quotidien n°4486_0.pdf" -> 4486.
ISSUE_NUMBER = re.compile(r"n°\s*(\d+)", re.IGNORECASE)

# "N° :2026-096/MGDP/SG/ISLO/DG/PRCP du 24/08/2026", "N° : N°2026-06/RTNY/...",
# "N° :°2026-002/ROBR/...", "N° 2026-032/MS/SG/ CHR-KAYA /DG/PRCP".
REFERENCE = re.compile(r"N°\s*:?\s*(?:N°)?\s*°?\s*(\d{4}-\d+(?:\s*/\s*[A-Za-z0-9.\-]+)+)")

DEADLINE = re.compile(r"(?:au plus tard le|avant le)\s+(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", re.IGNORECASE)

VALUE_PHRASE = re.compile(
    r"montants?\s+pr[ée]visionnels?|budget\s+pr[ée]visionnel|enveloppe\s+financi[èe]re\s+pr[ée]visionnelle",
    re.IGNORECASE,
)
FIGURE = re.compile(r"\d{1,3}(?: \d{3})+")
TAX_TAG = re.compile(r"\b(HTVA|HTV|TTC)\b")
WHITESPACE = re.compile(r"\s+")
BLANK_RUN = re.compile(r"\n\s*\n+")

# Contact details (rule 19); see the module docstring for the measurement. A phone
# is four digit pairs with one consistent separator, or eight unbroken digits,
# with an optional +226 prefix; a list of them is joined by "/", "," or ";".
PHONE = r"(?:\(?\+?226\)?[ \-]?)?(?<!\d)\d{2}(?:([ \-_.])\d{2}\1\d{2}\1\d{2}|\d{6})(?!\d)"
EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
CONTACT_LABEL = r"(?:t[ée]l(?:[ée]phone)?|cel(?:lulaire)?|fax|portable|mobile|contact|e-?mail)\s*\.?\s*:?\s*"
CONTACT = re.compile(
    rf"(?:{CONTACT_LABEL})?(?:{PHONE}|{EMAIL})(?:\s*[/,;]\s*(?:{PHONE}|{EMAIL}))*",
    re.IGNORECASE,
)
EMPTY_PARENS = re.compile(r"\(\s*\)")
SPACE_RUN = re.compile(r"[ \t]{2,}")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _blank(line: str) -> bool:
    return not line.strip()


def _centred(line: str) -> bool:
    return not _blank(line) and _indent(line) >= CENTRED_FROM_COLUMN


def _is_marker(line: str) -> bool:
    return _centred(line) and MARKER.match(line.strip()) is not None


def _find_section(lines: list[str], marker: int, floor: int) -> int | None:
    """Index of the section line above `marker`, searched down to `floor`; None if none."""
    j = marker - 1
    while j > floor:
        if _centred(lines[j]) and SECTION.match(lines[j].strip()):
            return j
        j -= 1
    return None


def _head(lines: list[str], section: int, marker: int) -> tuple[int, int, int, int]:
    """(section, buyer start, buyer end, marker): the buyer is the run beneath the section line."""
    buyer_start = section + 1
    if buyer_start >= marker or _blank(lines[buyer_start]):
        raise ValueError(
            f"burkina_faso: no buyer line beneath the section line above marker line {marker + 1} "
            f"{lines[marker].strip()!r}"
        )
    buyer_end = buyer_start
    while buyer_end + 1 < marker and not _blank(lines[buyer_end + 1]):
        buyer_end += 1
    return section, buyer_start, buyer_end, marker


def _reference(lines: list[str], marker: int) -> str:
    """The dossier number in the centred head lines from the marker down, or ""."""
    k = marker
    while k < len(lines) and (_blank(lines[k]) or _centred(lines[k])):
        match = REFERENCE.search(lines[k])
        if match:
            return WHITESPACE.sub("", match.group(1)).rstrip(".")
        k += 1
    return ""


def strip_contact_details(text: str) -> str:
    """Remove phone numbers and email addresses, with a dangling label; keep the rest.

    Only a line something was removed from is tidied (empty parentheses, doubled
    spaces), so a body with no contact detail comes back byte-identical.
    """
    lines = []
    for line in text.split("\n"):
        stripped = CONTACT.sub("", line)
        if stripped != line:
            stripped = SPACE_RUN.sub(" ", EMPTY_PARENS.sub("", stripped)).strip()
        lines.append(stripped)
    return "\n".join(lines)


def _body(lines: list[str], start: int, end: int) -> str:
    kept = [line.strip() for line in lines[start:end] if not (FOOTER.match(line) or SITES.match(line))]
    body = BLANK_RUN.sub("\n\n", "\n".join(kept)).strip()
    return strip_contact_details(body)


def stated_value(text: str) -> tuple[Decimal | None, str | None]:
    """The amount a value passage states, as (Decimal, "XOF"), or (None, None).

    `text` is the passage from the value phrase to the end of its paragraph, or
    one printed line of it: the ten lines the manifest quotes verbatim each yield
    the figure the module docstring says. First HTVA-tagged figure, else first
    TTC-tagged, else the first figure; a tag belongs to the figure it follows.
    """
    flat = WHITESPACE.sub(" ", text)
    figures = list(FIGURE.finditer(flat))
    tagged: list[tuple[str, str]] = []
    for k, match in enumerate(figures):
        window_end = figures[k + 1].start() if k + 1 < len(figures) else len(flat)
        tag = TAX_TAG.search(flat, match.end(), window_end)
        label = tag.group(1) if tag else ""
        # "HTV" is the typo two notices print for HTVA; see the module docstring.
        tagged.append((match.group(), "HTVA" if label.startswith("HTV") else label))

    chosen = ""
    for wanted in ("HTVA", "TTC"):
        chosen = next((figure for figure, label in tagged if label == wanted), "")
        if chosen:
            break
    if not chosen and tagged:
        chosen = tagged[0][0]
    if not chosen:
        return published_value(None, None, source_id=SOURCE_ID)
    return published_value(chosen.replace(" ", ""), CURRENCY, source_id=SOURCE_ID)


def _value_passage(body: str) -> str:
    """From the first value phrase to the end of its paragraph, or "" when none."""
    for paragraph in body.split("\n\n"):
        match = VALUE_PHRASE.search(paragraph)
        if match:
            return paragraph[match.start() :]
    return ""


def _deadline(body: str, external_id: str) -> datetime | None:
    flat = WHITESPACE.sub(" ", body)
    match = DEADLINE.search(flat)
    if match is None:
        log.warning("deadline_not_found", source_id=SOURCE_ID, external_id=external_id)
        return None
    day, month, year = match.groups()
    # Reshaped into the one unambiguous form dates.py accepts, as sierra_leone.py
    # does, so the datetime is still built in one place. Day-month order is measured
    # on the fixture; see the module docstring.
    return parse_deadline(f"{year}-{month.zfill(2)}-{day.zfill(2)}", source_id=SOURCE_ID, url="")


def issue_number(url: str) -> str:
    """The issue number the file name carries, or a loud failure."""
    match = ISSUE_NUMBER.search(unquote(url))
    if match is None:
        raise ValueError(f"burkina_faso: no issue number (digits after n°) in the URL {url!r}")
    return match.group(1)


def _published_at(lines: list[str], number: str, url: str) -> datetime:
    """The date printed beside the issue's own number; see the module docstring."""
    stamp = re.compile(
        rf"N°\s*{number}(?:\s*\(bis\))?\s*[–-]\s*\S+\s+(\d{{1,2}})\s+({MONTH_NAMES})\s+(\d{{4}})", re.IGNORECASE
    )
    for line in lines:
        match = stamp.search(line)
        if match is None:
            continue
        day, month_name, year = match.groups()
        published = parse_published(
            f"{year}-{MONTHS_FR[month_name.lower()]:02d}-{int(day):02d}", source_id=SOURCE_ID, url=url
        )
        if published is not None:
            return published
    raise ValueError(
        f"burkina_faso: no line prints issue number {number} with its date (N° {number} - weekday dd month yyyy)"
    )


def _admin_level(buyer: str) -> str:
    words = buyer.split()
    return "local" if words and words[0] in SUB_NATIONAL_HEADINGS else "national"


def map_notices(document: dict) -> list[MappedNotice]:
    """Every notice in one bulletin, `{"url": ..., "text": ...}`. Raises when it holds none."""
    for key in ("url", "text"):
        if key not in document:
            raise ValueError(f"burkina_faso: the decoded bulletin has no {key!r}; fetch.py's PDF decoder supplies it")
    url, text = document["url"], document["text"]
    number = issue_number(url)

    # pdftotext ends each page with a form feed on the first line of the next; it
    # is layout and not text, and stripping it first keeps the line indents honest.
    lines = text.replace("\f", "").split("\n")
    # A phrase line right under a marker line is that heading wrapped, not a new
    # marker; see the module docstring (issue 4485, page 43).
    markers = [
        index for index, line in enumerate(lines) if _is_marker(line) and not (index and _is_marker(lines[index - 1]))
    ]
    if not markers:
        raise ValueError("burkina_faso: the bulletin text has no centred notice marker line; see MARKER")
    published_at = _published_at(lines, number, url)

    heads: list[tuple[int, int, int, int]] = []  # (section, buyer_start, buyer_end, marker)
    skipped = 0
    for position, marker in enumerate(markers):
        floor = markers[position - 1] if position else -1
        section = _find_section(lines, marker, floor)
        if section is None:
            if heads:
                raise ValueError(
                    f"burkina_faso: no section line above marker line {marker + 1} {lines[marker].strip()!r}"
                )
            # Results text until the first head; see the module docstring.
            skipped += 1
            continue
        heads.append(_head(lines, section, marker))
    if not heads:
        raise ValueError(
            f"burkina_faso: {len(markers)} marker line(s) but none with a notice head above it "
            "(section line, buyer, blank, title); the AVIS part was not found"
        )
    if skipped:
        log.info("burkina_faso_results_markers_skipped", source_id=SOURCE_ID, skipped=skipped)

    mapped: list[MappedNotice] = []
    for position, (_, buyer_start, buyer_end, marker) in enumerate(heads):
        block_end = heads[position + 1][0] if position + 1 < len(heads) else len(lines)

        buyer = " ".join(lines[k].strip() for k in range(buyer_start, buyer_end + 1))
        title = " ".join(lines[k].strip() for k in range(buyer_end + 1, marker) if not _blank(lines[k]))
        if not title:
            raise ValueError(
                f"burkina_faso: no title between buyer {buyer!r} and marker line {marker + 1} {lines[marker].strip()!r}"
            )

        body = _body(lines, marker, block_end)
        external_id = _reference(lines, marker)
        if not external_id:
            log.info("reference_not_found", source_id=SOURCE_ID, buyer=buyer, title=title)

        estimated_value, value_currency = stated_value(_value_passage(body))
        if estimated_value is None:
            log.info("value_not_stated", source_id=SOURCE_ID, external_id=external_id)

        notice = Notice(
            content_hash=content_hash(title, body),
            source_id=SOURCE_ID,
            external_id=external_id,
            url=url,
            title=title,
            buyer=buyer,
            country=COUNTRY,
            admin_level=_admin_level(buyer),
            published_at=published_at,
            deadline_at=_deadline(body, external_id),
            language=LANGUAGE,
            language_confidence=LANGUAGE_CONFIDENCE,
            estimated_value=estimated_value,
            value_currency=value_currency,
            body=body,
        )
        # French only; the English rendering is step 14's to derive.
        mapped.append(MappedNotice(notice=notice))

    log.info("burkina_faso_segmented", notices=len(mapped))
    return mapped
