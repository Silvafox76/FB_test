"""BOAMP's contract, against the day recorded on 2026-09-12.

The fixture is one whole publication day of DILA's flux: the index verbatim, a
census of all 414 files, and the 21 notice files that between them carry every
shape the census found. `selection` in the fixture says why each file is in it, so
a reader can tell a representative fixture from a convenient one.

What this source can get wrong is different from the other five, and the tests are
weighted accordingly. There is no query surface at all - no CPV parameter, no type
parameter, no index of a day's contents - so there is no server-side filter to
distrust, which is most of the World Bank file. What there is instead is **four
document formats** behind one wrapper, each putting the title, the description and
the CPV codes somewhere different, and a notice type that can only be read from
the file after it has been fetched. So the hazards here are a format read through
another format's paths, an excluded nature reaching `notices`, and a deadline
string shape nobody anticipated.

The fixture carries seven distinct deadline shapes on the recorded day. Rule 10
says the deadline is parsed by rule from the original, so each one either parses
or is honestly absent; a shape that parsed to the wrong instant would be worse
than one that did not parse at all.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from monitor.connectors.boamp import (
    ENCODING,
    LOOKBACK_DAYS,
    MAX_NOTICE_REQUESTS,
    NOTICE_FILE,
    BoampConnector,
    envelope,
    listing_names,
    notice_idweb,
    notice_nature,
    parse_document,
)
from monitor.models import Source
from monitor.normalise.boamp import ADMIN_LEVEL, COUNTRY, FORMATS, LANGUAGE, format_paths, map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "boamp.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "boamp.yaml"

RECORDED_DAY = date(2026, 9, 11)


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> BoampConnector:
    return BoampConnector(source, cpv_prefixes=["48", "72", "79"])


@pytest.fixture(scope="module")
def roots(document) -> dict:
    """Every recorded notice file, parsed, keyed by its file name."""
    return {name: parse_document(xml, reference=name) for name, xml in document["notices"].items()}


def mapped(document: dict, name: str):
    return map_notice({"day": document["day"], "xml": document["notices"][name]})


# --- the index ---------------------------------------------------------------


def test_the_index_yields_only_notice_files_and_each_one_once(document):
    """The real 414-row Apache autoindex, not a constructed one."""
    names = listing_names(document["index"], day_url=document["day_url"])

    assert len(names) == document["census"]["files"] == 414
    assert len(set(names)) == len(names), "a name listed twice would be fetched twice and stored once"
    assert all(NOTICE_FILE.search(f'href="{name}"') for name in names)
    # Apache's own furniture: the sort links and the parent directory link.
    assert not any(name.startswith("?") or name.startswith("/") for name in names)


def test_every_recorded_notice_file_is_in_the_index(document):
    """The fixture's 21 files are a subset of the day, not a separate invention."""
    names = set(listing_names(document["index"], day_url=document["day_url"]))

    assert set(document["notices"]) <= names


def test_an_index_that_parses_to_nothing_raises(document):
    """Rule 4. Eleven days were measured and the quietest held 105 files, so zero is broken."""
    with pytest.raises(ValueError, match="no notice files"):
        listing_names("<html><body>nothing here</body></html>", day_url=document["day_url"])


# --- the window --------------------------------------------------------------


def test_the_window_is_the_completed_days_and_never_today(connector):
    """A day directory fills all day long, so today is half a day pretending to be one."""
    today = date(2026, 9, 12)
    days = connector.days(today=today)

    assert days == [date(2026, 9, 10), date(2026, 9, 11)]
    assert today not in days
    assert len(days) == LOOKBACK_DAYS
    assert days == sorted(days), "oldest first, so the newest notices are the last written"


def test_the_day_url_is_built_from_the_registry_and_not_from_a_constant(connector, document):
    assert connector.day_url(RECORDED_DAY) == document["day_url"]


# --- the four formats --------------------------------------------------------


def test_all_four_document_formats_are_present_and_each_maps(document, roots):
    """EFORMS, FNSimple, MAPA and DSP, each with its own title, description and CPV paths."""
    assert set(document["census"]["by_format"]) == set(FORMATS) == {"EFORMS", "FNSimple", "MAPA", "DSP"}

    seen = set()
    for name, root in roots.items():
        declared = format_paths(root, reference=name)
        for key, paths in FORMATS.items():
            if paths is declared:
                seen.add(key)
    assert seen == set(FORMATS), f"a format in the fixture that no test maps: {set(FORMATS) - seen}"


def test_an_unknown_format_raises_rather_than_borrowing_another_formats_paths(document):
    """Reading a fifth format through a fourth format's paths yields an empty notice."""
    name = next(iter(document["notices"]))
    tampered = document["notices"][name].replace("<EFORMS", "<NOUVEAU", 1).replace("</EFORMS", "</NOUVEAU", 1)
    if tampered == document["notices"][name]:  # the sample is a national format
        tampered = document["notices"][name].replace("<initial", "<nouveau", 1).replace("</initial", "</nouveau", 1)

    with pytest.raises(ValueError, match="unknown BOAMP document format|no .* element"):
        map_notice({"day": document["day"], "xml": tampered})


def test_each_format_finds_a_title_through_its_own_paths(document, roots):
    """The census says the title path resolves on 289 of 289 tenders in every format."""
    for name in roots:
        if notice_nature(roots[name], reference=name) != "APPEL_OFFRE":
            continue
        notice = mapped(document, name).notice
        assert notice.title.strip(), f"{name}: empty title"


# --- the excluded natures ----------------------------------------------------


def test_the_registry_excludes_exactly_the_decided_and_unreadable_natures(source):
    assert set(source.exclude_notice_types) == {"ATTRIBUTION", "MODIFICATION", "RECTIFICATIF"}


def test_the_fixture_carries_an_excluded_nature_of_every_kind(document, roots):
    """Otherwise the exclusion below would be asserted against nothing."""
    natures = {notice_nature(root, reference=name) for name, root in roots.items()}

    assert {"ATTRIBUTION", "MODIFICATION", "RECTIFICATIF"} <= natures
    assert "APPEL_OFFRE" in natures


def test_an_excluded_nature_is_dropped_before_it_becomes_a_notice(source, roots):
    """`Notice` has no notice-type column, so an ATTRIBUTION that got through would be
    indistinguishable from a live tender for every later stage."""
    excluded = frozenset(source.exclude_notice_types)
    kept = [name for name, root in roots.items() if notice_nature(root, reference=name) not in excluded]
    dropped = [name for name, root in roots.items() if notice_nature(root, reference=name) in excluded]

    assert dropped, "the fixture must contain something to drop"
    assert kept, "and something to keep"
    assert set(kept) & set(dropped) == set()


def test_the_census_shows_the_exclusion_is_worth_making(document):
    """30% of a day. Measured, not assumed: 414 files, 289 tenders."""
    census = document["census"]
    excluded = census["files"] - census["tenders"]

    assert census["tenders"] == 289
    assert excluded == 125
    assert excluded / census["files"] > 0.25


# --- deadlines, rule 10 ------------------------------------------------------


def test_every_recorded_deadline_shape_either_parses_or_is_absent(document, roots, source):
    """Seven distinct shapes on the recorded day, including a fractional second,
    a bare local time, a Z suffix and two offset signs.

    A shape that parsed to the wrong instant would be worse than one that did not
    parse at all, which is why this asserts tz-awareness rather than only that a
    value came back.
    """
    excluded = frozenset(source.exclude_notice_types)
    assert len(document["census"]["deadline_shapes"]) >= 6

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        notice = mapped(document, name).notice
        if notice.deadline_at is not None:
            assert notice.deadline_at.tzinfo is not None, f"{name}: naive deadline"


def test_a_tender_with_no_stated_deadline_maps_with_none_rather_than_a_guess(document, roots, source):
    """133 of the recorded day state none. Rule 10: none is not inferred from prose."""
    excluded = frozenset(source.exclude_notice_types)
    without = [
        name
        for name, root in roots.items()
        if notice_nature(root, reference=name) not in excluded and mapped(document, name).notice.deadline_at is None
    ]

    assert without, "the fixture carries a tender with no deadline, by its selection rule"
    assert document["census"]["deadline_shapes"]["absent"] == 133


# --- rule 9, and what is stored ----------------------------------------------


def test_the_notice_is_stored_as_published_in_french_with_no_english_rendering(document, roots, source):
    """BOAMP publishes no English of anything, so every notice reaches step 14
    unless the French lexicon has already decided it."""
    excluded = frozenset(source.exclude_notice_types)

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        result = mapped(document, name)
        assert result.notice.language == LANGUAGE == "fr"
        assert result.notice.language_confidence == 1.0
        assert result.title_en == "", f"{name}: an English rendering nobody published"
        assert result.body_en == ""


def test_the_country_and_level_match_the_registry(source):
    """The registry comment promises this test asserts the two agree."""
    assert source.country == COUNTRY == "FR"
    assert source.admin_level == ADMIN_LEVEL == "local"


def test_the_external_id_is_the_notices_own_idweb(document, roots, source):
    excluded = frozenset(source.exclude_notice_types)

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        assert mapped(document, name).notice.external_id == notice_idweb(root, reference=name)


def test_the_url_points_at_the_public_notice_page_not_the_flux(document, roots, source):
    """The flux is how this reads BOAMP; boamp.fr is where a reviewer goes to look."""
    excluded = frozenset(source.exclude_notice_types)

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        url = mapped(document, name).notice.url
        assert url.startswith("https://www.boamp.fr/")
        assert "echanges.dila.gouv.fr" not in url


# --- the shapes the selection rule went looking for --------------------------


def test_a_tender_with_no_description_maps_with_an_empty_body(document, roots, source):
    """17 of the recorded day. An empty body is a fact about the notice, not a failure."""
    excluded = frozenset(source.exclude_notice_types)
    bodies = {
        name: mapped(document, name).notice.body
        for name, root in roots.items()
        if notice_nature(root, reference=name) not in excluded
    }

    assert document["census"]["tenders_without_body"] == 17
    assert any(not body for body in bodies.values()), "the fixture carries one, by its selection rule"


def test_a_tender_with_no_cpv_code_maps_with_an_empty_list(document, roots, source):
    """26 of the recorded day, and MAPA has no CPV element in its schema at all."""
    excluded = frozenset(source.exclude_notice_types)
    codes = {
        name: mapped(document, name).notice.cpv_codes
        for name, root in roots.items()
        if notice_nature(root, reference=name) not in excluded
    }

    assert document["census"]["tenders_without_cpv"] == 26
    assert any(code == [] for code in codes.values())
    assert any(code != [] for code in codes.values()), "and one that does classify itself"


def test_cpv_codes_are_read_through_each_formats_own_paths(document, roots, source):
    """FNSimple and DSP split main and additional objects; eForms uses one element type."""
    excluded = frozenset(source.exclude_notice_types)

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        for code in mapped(document, name).notice.cpv_codes:
            assert code.isdigit(), f"{name}: {code!r} is not a CPV code"


def test_the_value_is_never_converted_from_euros(document, roots, source):
    """Stated in EUR on 96 of 224 eForms notices. A conversion needs a rate and a date."""
    excluded = frozenset(source.exclude_notice_types)

    for name, root in roots.items():
        if notice_nature(root, reference=name) in excluded:
            continue
        assert mapped(document, name).notice.estimated_value is None
        assert mapped(document, name).notice.value_currency is None


# --- the envelope ------------------------------------------------------------


def test_the_envelope_carries_the_xml_verbatim(document):
    """Rule 9: what is stored is the notice as served, not a re-serialisation of it."""
    name = next(iter(document["notices"]))
    xml = document["notices"][name]

    restored = json.loads(envelope(RECORDED_DAY, xml))

    assert restored["xml"] == xml
    assert restored["day"] == RECORDED_DAY.isoformat()


def test_the_mapper_checks_the_day_against_the_notices_own_publication_date(document):
    """The directory is the publication date on all 414 files, and the mapper says so."""
    name = next(iter(document["notices"]))

    with pytest.raises(ValueError):
        map_notice({"day": "2020-01-01", "xml": document["notices"][name]})


def test_a_file_in_another_encoding_is_a_changed_flux(document):
    """Every recorded file declares UTF-8; decoding by guess is how mojibake reaches
    a French lexicon match."""
    assert ENCODING == "utf-8"
    assert all(xml.lstrip().startswith("<?xml") for xml in document["notices"].values())


# --- ceilings ----------------------------------------------------------------


def test_the_request_ceiling_sits_above_the_busiest_pair_of_days_seen(connector, source):
    """816 files across the two busiest consecutive days on 2026-09-12."""
    assert MAX_NOTICE_REQUESTS >= 816
    assert connector.at_ceiling(MAX_NOTICE_REQUESTS, 0, RECORDED_DAY) is True
    assert connector.at_ceiling(0, 0, RECORDED_DAY) is False


def test_the_yield_ceiling_is_the_registrys_and_not_a_constant(connector, source):
    assert connector.at_ceiling(0, source.expected_max, RECORDED_DAY) is True
    assert connector.at_ceiling(0, source.expected_max - 1, RECORDED_DAY) is False


# --- the nature the first live fetch found ------------------------------------


def test_every_format_maps_its_document_element_by_nature(document):
    """`document` is a mapping and not a single name, and the reason is measured.

    The recorded day held no PRE-INFORMATION at all. The first live fetch read
    2026-09-10 as well and hit two, whose eForms document element is
    `PriorInformationNotice` rather than `ContractNotice`, and the run stopped
    rather than mapping one through the wrong path. Resolving that by trying one
    element and then another would have been the fallback rule 1 forbids.
    """
    assert FORMATS["EFORMS"].document == {
        "APPEL_OFFRE": "ContractNotice",
        "PRE-INFORMATION": "PriorInformationNotice",
    }
    for national in ("FNSimple", "MAPA", "DSP"):
        assert FORMATS[national].document == {"APPEL_OFFRE": "initial"}


def test_a_nature_with_no_mapped_element_raises_and_names_it(document, roots):
    """Rule 4. The message has to name the nature, because the fix is a one-line
    addition to FORMATS and the reader needs to know which line."""
    name = next(
        n
        for n, root in roots.items()
        if notice_nature(root, reference=n) == "APPEL_OFFRE" and "EFORMS" in document["notices"][n][:2000]
    )
    tampered = (
        document["notices"][name]
        .replace("<APPEL_OFFRE", "<AVIS_INCONNU", 1)
        .replace("</APPEL_OFFRE", "</AVIS_INCONNU", 1)
    )

    with pytest.raises(ValueError, match="no document element is mapped for nature|unknown"):
        map_notice({"day": document["day"], "xml": tampered})
