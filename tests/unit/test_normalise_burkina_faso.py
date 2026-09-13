"""The Burkina Faso normaliser: one bulletin's text to its AVIS notices.

Measured against `tests/contract/fixtures/burkina_faso_avis.pdf` (pages 20-47 of
Quotidien n°4478), read the way `monitor/fetch.py` reads it - through
`monitor.normalise.ocr.extract` - so the text under test is the text the mapper
will receive. The manifest beside the fixture supplies the verbatim value lines
and buyer headings pinned below; everything else is pinned to what the module
docstring says it measured.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from monitor.normalise import ocr
from monitor.normalise.burkina_faso import (
    CENTRED_FROM_COLUMN,
    CONTACT,
    issue_number,
    map_notices,
    stated_value,
    strip_contact_details,
)

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures"
AVIS_PDF = FIXTURES / "burkina_faso_avis.pdf"
AVIS_MANIFEST = FIXTURES / "burkina_faso_avis.json"
MASTHEAD_PDF = FIXTURES / "burkina_faso_4486_masthead.pdf"
MASTHEAD_MANIFEST = FIXTURES / "burkina_faso_4486_masthead.json"
WRAPPED_PDF = FIXTURES / "burkina_faso_4485_wrapped_marker.pdf"
WRAPPED_MANIFEST = FIXTURES / "burkina_faso_4485_wrapped_marker.json"
WRAPPED_URL = "http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20n%C2%B04485_0.pdf"

# The issue's own URL, as the manifest records it and as the connector fetched it.
ISSUE_URL = "http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04478.pdf"
ISSUE_PUBLISHED = datetime(2026, 9, 1, tzinfo=UTC)

# The 4486 URL as the listing fixture links it: lowercase n°, a "_0" suffix.
MASTHEAD_URL = "http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20n%C2%B04486_0.pdf"
MASTHEAD_PUBLISHED = datetime(2026, 9, 11, tzinfo=UTC)

# What the segmentation produces on the fixture: one notice per page, 28 pages.
NOTICE_COUNT = 28
DEMANDE_DE_PRIX = 25
APPEL_D_OFFRES = 2
MANIFESTATION_D_INTERET = 1

WITH_DEADLINE = 26
WITH_VALUE = 28
WITH_REFERENCE = 28
NATIONAL = 5
LOCAL = 23

# The submission date 27 of the 28 notices share, as printed: 10/09/2026.
COMMON_DEADLINE = date(2026, 9, 10)

# The ten lines the manifest quotes verbatim, and the figure each yields: the HTVA
# figure where one is tagged, else the TTC figure, else the only figure.
MANIFEST_LINE_VALUES = [
    ("Montant prévisionnel : 9 322 034 HTVA et 11 000 000 TTC.", Decimal("9322034.00")),
    ("Montant prévisionnel : 13 347 457 FCFA HTVA ; 15 750 000 FCFA TTC", Decimal("13347457.00")),
    (
        "Montant prévisionnel HTVA et TTC : Quarante-deux million six cent cinquante-sept mille sept cent dix "
        "(42 657 710) francs cfa HTVA et Cinquante",
        Decimal("42657710.00"),
    ),
    (
        "Montant prévisionnel HTVA et TTC : Seize millions quatre cent cinquante-neuf mille trois cent soixante-dix "
        "(16 459 370) francs cfa HTVA et",
        Decimal("16459370.00"),
    ),
    (
        "Montant prévisionnel : dix millions quatre cent vingt-cinq mille neuf cent dix-huit (10 425 918) FCFA HTVA "
        "et douze millions sept cent quatorze mille",
        Decimal("10425918.00"),
    ),
    (
        "Montant prévisionnel : quatorze millions deux cent soixante-dix-neuf mille six cent soixante un "
        "(14 279 661) FCFA HTVA soit seize millions huit cent",
        Decimal("14279661.00"),
    ),
    (
        "Montant prévisionnel : vingt-cinq millions cent soixante-huit mille cent dix (25 168 110) francs CFA HTVA "
        "soit vingt-neuf millions six cent quatre-",
        Decimal("25168110.00"),
    ),
    (
        "Montant prévisionnel : dix millions trente-trois mille neuf cent cinquante-trois (10 033 953) francs CFA "
        "HTVA et onze millions huit cent quarante mille",
        Decimal("10033953.00"),
    ),
    (
        "Montant prévisionnel : Montant prévisionnel HTVA et TTC : trente-trois millions soixante-treize mille cinq "
        "cent quatorze (33 073 514) francs CFA",
        Decimal("33073514.00"),
    ),
    ("Montant prévisionnel : 264 943 177 F CFA TTC.", Decimal("264943177.00")),
]


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(AVIS_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def text(tmp_path_factory) -> str:
    """The one extraction this file makes; pdftotext is a subprocess."""
    pdf_path = tmp_path_factory.mktemp("avis") / "burkina_faso_avis.pdf"
    pdf_path.write_bytes(AVIS_PDF.read_bytes())
    return ocr.extract(pdf_path).text


def _document(text: str) -> dict:
    """The shape fetch.py's PDF decoder hands the mapper (decision 51)."""
    return {"url": ISSUE_URL, "text": text}


@pytest.fixture(scope="module")
def document(text) -> dict:
    return _document(text)


@pytest.fixture(scope="module")
def notices(document):
    return [mapped.notice for mapped in map_notices(document)]


def _by_buyer_and_title(notices, buyer: str, title_start: str):
    found = [n for n in notices if n.buyer == buyer and n.title.startswith(title_start)]
    assert len(found) == 1, f"{len(found)} notices for {buyer!r} / {title_start!r}"
    return found[0]


# --- segmentation -------------------------------------------------------------


def test_the_fixture_segments_into_28_notices_one_per_marker_line(notices):
    assert len(notices) == NOTICE_COUNT


def test_the_marker_breakdown_is_25_demande_de_prix_2_appel_d_offres_1_manifestation(notices):
    """The body starts at the marker line, so its first line names the procedure."""
    first_lines = [n.body.splitlines()[0].lower() for n in notices]

    assert sum(line.startswith("avis de demande de prix") for line in first_lines) == DEMANDE_DE_PRIX
    assert sum(line.startswith("avis d’appel d’offres") for line in first_lines) == APPEL_D_OFFRES
    assert sum(line.startswith("avis de manifestation d’intérêt") for line in first_lines) == MANIFESTATION_D_INTERET


def test_recital_prose_that_repeats_the_phrase_is_not_a_marker(manifest, text):
    """The manifest counts the phrase 50 times; only 25 are notices. The other 25
    are recital sentences ('Cet Avis de demande de prix fait suite...') and sit at
    the margin, which is what keeps them out of the marker rule."""
    assert manifest["measured_inventory"]["avis_de_demande_de_prix"] == 50
    phrase = "avis de demande de prix"
    lines = text.replace("\f", "").splitlines()
    markers = [
        line
        for line in lines
        if len(line) - len(line.lstrip()) >= CENTRED_FROM_COLUMN and line.strip().lower().startswith(phrase)
    ]
    others = [line for line in lines if phrase in line.lower() and line not in markers]

    assert len(markers) == DEMANDE_DE_PRIX
    assert sum(line.lower().count(phrase) for line in others) == 50 - DEMANDE_DE_PRIX
    assert all(len(line) - len(line.lstrip()) < CENTRED_FROM_COLUMN for line in others)


def test_a_wrapped_prose_line_beginning_with_a_marker_phrase_is_not_a_marker(text, notices):
    """Page 20 wraps '...dont l’avis à / manifestation d’intérêt a été publié...'
    onto a line that starts with the phrase at column 0. It is inside the first
    notice's body, not a notice of its own."""
    assert any(line.startswith("manifestation d’intérêt a été publié") for line in text.splitlines())

    assert "manifestation d’intérêt a été publié" in notices[0].body
    assert notices[0].buyer == "INSTITUT SUPERIEUR DE LOGISTIQUE DE OUAGADOUGOU"


# --- buyer, title, admin level ---------------------------------------------------


def test_the_three_recorded_buyer_headings_are_the_first_three_buyers(manifest, notices):
    assert [n.buyer for n in notices[:3]] == manifest["buyer_heading_lines_verbatim"]


def test_an_all_caps_title_does_not_displace_the_buyer(notices):
    """The last notice's three-line title is in capitals; the buyer is still the
    line beneath the section line, and the title is the three lines joined."""
    last = notices[-1]

    assert last.buyer == "COMMUNE DE TENKODOGO"
    assert last.title == (
        "SUIVI-CONTRÔLE DE DIVERS TRAVAUX ET UNE MISSION D’ETUDES POUR LES TRAVAUX DE "
        "CONSTRUCTION D’UNE AUBERGE COMMUNALE AU PROFIT DE LA COMMUNE DE TENKODOGO"
    )


def test_a_blank_line_between_title_and_marker_is_skipped(notices):
    adeu = _by_buyer_and_title(notices, "AGENCE DU DEVELOPPEMENT ECONOMIQUE URBAIN (ADEU)", "Travaux d’aménagement")

    assert adeu.title == "Travaux d’aménagement de vingt (20) étals au marché de Paspanga"


def test_a_two_line_title_is_joined_with_one_space(notices):
    guiriko = _by_buyer_and_title(notices, "REGION DU GUIRIKO", "Travaux de construction de trois")

    assert guiriko.title == (
        "Travaux de construction de trois (03) boutiques au marché du village de Karangasso-Sambla, "
        "commune de Karangasso-Sambla"
    )


def test_admin_level_is_local_under_commune_and_region_headings_and_national_otherwise(notices):
    levels = {n.buyer: n.admin_level for n in notices}

    assert levels["INSTITUT SUPERIEUR DE LOGISTIQUE DE OUAGADOUGOU"] == "national"
    assert levels["CENTRE HOSPITALIER REGIONAL DE KAYA"] == "national"
    assert levels["COMMUNE DE FOUTOURI"] == "local"
    assert levels["REGION DE BANKUI"] == "local"
    assert sum(n.admin_level == "national" for n in notices) == NATIONAL
    assert sum(n.admin_level == "local" for n in notices) == LOCAL


def test_country_language_and_confidence_are_uniform_and_nothing_is_english(document):
    mapped = map_notices(document)

    assert {m.notice.country for m in mapped} == {"BF"}
    assert {m.notice.language for m in mapped} == {"fr"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}
    assert all(m.title_en == "" and m.body_en == "" for m in mapped)
    assert all(m.notice.cpv_codes == [] for m in mapped)


def test_every_notice_carries_the_issues_own_url(notices, manifest):
    assert ISSUE_URL == manifest["source_issue"]["source_href"]
    assert {n.url for n in notices} == {ISSUE_URL}


@pytest.mark.parametrize(
    ("url", "number"),
    [
        (ISSUE_URL, "4478"),
        (MASTHEAD_URL, "4486"),
        ("http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04480%20%28bis%29.pdf", "4480"),
        ("http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20N%C2%B04481%20.pdf", "4481"),
    ],
)
def test_the_issue_number_is_the_digits_after_the_decoded_degree_sign(url, number):
    assert issue_number(url) == number


def test_a_url_without_an_issue_number_raises():
    with pytest.raises(ValueError, match="no issue number"):
        issue_number("http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien.pdf")


def test_published_at_is_the_date_printed_beside_the_issues_own_number(text, notices, manifest):
    """On this fixture the line is the page footer's right-hand stamp, 'N° 4478 -
    Mardi 01 septembre 2026'; the stale 'N°3827 – lundi 04 Mars 2024' beside it
    carries a different number and is never read. Midnight UTC."""
    assert "N° 4478 - Mardi 01 septembre 2026" in text
    assert manifest["source_issue"]["issue_title"].endswith("01 septembre 2026")

    assert {n.published_at for n in notices} == {ISSUE_PUBLISHED}
    assert all(n.published_at.date() < n.deadline_at.date() for n in notices if n.deadline_at is not None)


def test_published_at_on_issue_4486_comes_from_its_masthead_not_its_stale_footers(tmp_path):
    """The issue the first live run failed on. Its footers print the template
    stamp 'N° 4106(bis) – Vendredi 28 mars 2025'; the masthead prints
    'N° 4486 – Vendredi 11 septembre 2026' on the IFU line, and that is what the
    URL's number selects."""
    import hashlib

    masthead_manifest = json.loads(MASTHEAD_MANIFEST.read_text(encoding="utf-8"))
    pdf_bytes = MASTHEAD_PDF.read_bytes()
    assert hashlib.sha256(pdf_bytes).hexdigest() == masthead_manifest["extraction"]["sha256"]
    assert len(pdf_bytes) == masthead_manifest["extraction"]["bytes"]
    pdf_path = tmp_path / "masthead.pdf"
    pdf_path.write_bytes(pdf_bytes)
    masthead_text = ocr.extract(pdf_path).text
    assert masthead_manifest["masthead_line_verbatim"] in masthead_text
    assert "N° 4106(bis) – Vendredi 28 mars 2025" in masthead_text

    mapped = [m.notice for m in map_notices({"url": MASTHEAD_URL, "text": masthead_text})]

    assert len(mapped) == masthead_manifest["expected"]["notices"] == 2
    assert {n.published_at for n in mapped} == {MASTHEAD_PUBLISHED}
    assert {n.buyer for n in mapped} == {"PRIMATURE"}
    assert {n.url for n in mapped} == {MASTHEAD_URL}


def test_text_that_never_prints_the_urls_issue_number_raises_naming_it(text):
    """The 4478 text under a 4486 URL: no line prints 'N° 4486' with a date."""
    with pytest.raises(ValueError, match=r"no line prints issue number 4486"):
        map_notices({"url": MASTHEAD_URL, "text": text})


# --- reference ---------------------------------------------------------------


def test_every_notice_carries_its_dossier_number(notices):
    assert sum(bool(n.external_id) for n in notices) == WITH_REFERENCE
    assert len({n.external_id for n in notices}) == WITH_REFERENCE


@pytest.mark.parametrize(
    ("buyer", "title_start", "external_id"),
    [
        # On the line after the marker, "N° :2026-096/...".
        (
            "INSTITUT SUPERIEUR DE LOGISTIQUE DE OUAGADOUGOU",
            "Acquisition de consommables",
            "2026-096/MGDP/SG/ISLO/DG/PRCP",
        ),
        # On an "Identifiant de la DPX :" line two lines below the marker.
        ("INSTITUT NATIONAL DE FORMATION DES PERSONNEL DE L’EDUCATION", "Acquisition de 200", "2026-25/INFPE/DG/PRCP"),
        # Printed with spaces inside: "MS/SG/ CHR-KAYA /DG/PRCP".
        ("CENTRE HOSPITALIER REGIONAL DE KAYA", "Travaux de curage", "2026-032/MS/SG/CHR-KAYA/DG/PRCP"),
        # A stray degree sign: "N° :°2026-002/...".
        ("REGION DE OUBRI", "Réalisation de travaux diverses", "2026-002/ROBR/PKWG/CLYE"),
        # Doubled prefix: "N° : N°2026-06/...".
        (
            "REGION DES TANNOUNYAN",
            "Travaux de construction d’infrastructures scolaires",
            "2026-06/RTNY/PCMO/CBFRT/PRCP",
        ),
        # The AAOA prints a second number on the next line; the first is the reference.
        ("COMMUNE DE OUAGADOUGOU", "Travaux de construction de boutiques", "2026-02/CO/ADEU/DG/SCP"),
        # Four lines below the marker, ending in a full stop, on the AAOO.
        ("REGION DES TANNOUYNAN", "Aménagement d’une aire", "2026-001/RTNY/CNGLK/M/SG/PRMP"),
    ],
)
def test_the_reference_is_read_from_the_head_in_each_shape_the_fixture_prints(notices, buyer, title_start, external_id):
    matches = [n for n in notices if n.buyer == buyer and n.title.startswith(title_start)]

    assert external_id in {n.external_id for n in matches}


# --- value -------------------------------------------------------------------


@pytest.mark.parametrize(("line", "expected"), MANIFEST_LINE_VALUES)
def test_each_manifest_value_line_yields_its_figure_in_xof(manifest, line, expected):
    assert line in manifest["montant_previsionnel_sample_lines_verbatim"]

    assert stated_value(line) == (expected, "XOF")


def test_the_manifest_quotes_exactly_the_ten_lines_pinned_above(manifest):
    assert [line for line, _ in MANIFEST_LINE_VALUES] == manifest["montant_previsionnel_sample_lines_verbatim"]


def test_every_notice_carries_a_value_in_xof(notices):
    assert sum(n.estimated_value is not None for n in notices) == WITH_VALUE
    assert {n.value_currency for n in notices} == {"XOF"}


@pytest.mark.parametrize(
    ("buyer", "title_start", "expected"),
    [
        # Header prints TTC first, HTVA second: the HTVA figure is still the one carried.
        ("REGION DU SOUROU", "Travaux de construction d’un laboratoire", Decimal("31780000.00")),
        # Tagged "HTV", the typo two notices print, and again TTC before HTVA.
        ("REGION DE OUBRI", "Réalisation de travaux diverses", Decimal("19041990.00")),
        # Only a TTC figure is printed, so it is carried.
        ("REGION DES TANNOUYNAN", "Aménagement d’une aire", Decimal("264943177.00")),
        # "Enveloppe financière prévisionnelle", TTC only.
        ("REGION DU LIPTAKO", "Travaux de construction d’un hall", Decimal("25000000.00")),
        # "Budget prévisionnel", HTVA and TTC.
        ("CENTRE HOSPITALIER REGIONAL DE KAYA", "Travaux de curage", Decimal("94915254.00")),
        # No header line at all: the recital sentence is the first occurrence.
        ("ECOLE POLYTECHNIQUE DE OUAGADOUGOU", "Acquisition et installation", Decimal("23600000.00")),
        ("COMMUNE DE FOUTOURI", "Acquisition de fournitures scolaires", Decimal("14498792.00")),
        # A multi-lot notice that prints its total first carries the total.
        ("REGION DU SOUROU", "Travaux de construction d’infrastructures économiques", Decimal("19065720.00")),
        # A multi-lot notice with no total carries lot 1, never a sum.
        ("REGION DE BANKUI", "Travaux de réhabilitation d’infrastructures", Decimal("14527845.00")),
        # The truncated last notice: ten TTC-only lots, lot 1 carried.
        ("COMMUNE DE TENKODOGO", "SUIVI-CONTRÔLE", Decimal("2250000.00")),
    ],
)
def test_the_value_rule_on_the_shapes_the_fixture_prints(notices, buyer, title_start, expected):
    matches = [n for n in notices if n.buyer == buyer and n.title.startswith(title_start)]

    assert expected in {n.estimated_value for n in matches}


def test_a_passage_with_no_figure_states_no_value():
    assert stated_value("Montant prévisionnel : sans objet") == (None, None)
    assert stated_value("") == (None, None)


# --- deadline ----------------------------------------------------------------


def test_26_of_28_notices_carry_a_deadline_parsed_from_the_original(notices):
    with_deadline = [n for n in notices if n.deadline_at is not None]

    assert len(with_deadline) == WITH_DEADLINE
    assert all(n.deadline_at.tzinfo is UTC for n in with_deadline)
    assert sum(n.deadline_at.date() == COMMON_DEADLINE for n in with_deadline) == 24


def test_a_deadline_is_the_end_of_the_printed_day(notices):
    first = notices[0]

    assert first.deadline_at.date() == COMMON_DEADLINE
    assert (first.deadline_at.hour, first.deadline_at.minute) == (23, 59)


def test_a_deadline_wrapped_across_two_lines_is_read(notices):
    """Page 25 prints 'au plus tard le' at the end of one line and '10/09/2026' at
    the start of the next."""
    boromo = _by_buyer_and_title(notices, "REGION DE BANKUI", "Travaux de clôture partielle")

    assert boromo.deadline_at.date() == COMMON_DEADLINE


def test_the_accelerated_open_tender_closes_on_15_september(notices):
    aaoa = _by_buyer_and_title(notices, "COMMUNE DE OUAGADOUGOU", "Travaux de construction de boutiques")

    assert aaoa.deadline_at.date() == date(2026, 9, 15)


def test_a_date_printed_with_a_stray_space_is_read(notices):
    """'au plus tard le 01/10/ 2026' on the AAOO, page 44."""
    aaoo = _by_buyer_and_title(notices, "REGION DES TANNOUYNAN", "Aménagement d’une aire")

    assert aaoo.deadline_at.date() == date(2026, 10, 1)


def test_a_deadline_written_in_words_without_a_year_is_not_guessed(notices):
    """'au plus tard le jeudi 10 septembre à 9H00' names no year; None, not 2026."""
    kaya = _by_buyer_and_title(notices, "CENTRE HOSPITALIER REGIONAL DE KAYA", "Travaux de curage")

    assert kaya.deadline_at is None
    assert "jeudi 10 septembre" in " ".join(kaya.body.split())


# --- the truncated last notice -------------------------------------------------


def test_the_truncated_last_notice_is_carried_without_a_deadline(notices):
    """Page 47 ends inside the Tenkodogo notice's clause 3; its deadline is on page
    48, outside the fixture. `Notice.deadline_at` is optional and the notice is
    still a notice."""
    last = notices[-1]

    assert last.buyer == "COMMUNE DE TENKODOGO"
    assert last.external_id == "2026-01/RNKB/PBLG/CTNK/M/SG/P-CCAM"
    assert last.deadline_at is None
    assert last.estimated_value == Decimal("2250000.00")
    assert last.body.rstrip().endswith("tous les jours ouvrables de 7h30 à 16h00.")


# --- body ----------------------------------------------------------------------


def test_the_body_starts_at_the_marker_and_keeps_the_financing_line(notices):
    first = notices[0]
    lines = first.body.splitlines()

    assert lines[0] == "Avis de demande de prix"
    assert lines[1] == "N° :2026-096/MGDP/SG/ISLO/DG/PRCP du 24/08/2026"
    assert lines[2] == "Financement : Budget de l’ISLO, Exercice 2026"


def test_page_furniture_and_the_next_head_are_not_in_the_body(notices):
    for notice in notices:
        assert "www.dgcmef.gov.bf" not in notice.body
        assert "N° 4478 - Mardi 01 septembre 2026" not in notice.body
        assert "N°3827" not in notice.body
        assert "\f" not in notice.body
    # Each notice's buyer heading appears in no body at all: the head belongs to
    # the notice, and the previous notice's body stops before it.
    for notice in notices:
        assert not any(notice.buyer in other.body for other in notices)
    # The section line above the next buyer is in neither body.
    section_lines = ("Travaux", "Fournitures et Services courants", "Prestations intellectuelles")
    assert not any(line in section_lines for n in notices for line in n.body.splitlines())


def test_a_two_line_footer_is_stripped_from_the_body_too(text):
    """4471 and 4486 print the page number after the first stamp and the issue
    stamp on the next line; both lines are furniture."""
    split_footer = (
        "    N°3827 – lundi 04 Mars 2024                     98\n          N° 4478 - Mardi 01 septembre 2026\n"
    )
    anchor = "Financement : Budget de l’ISLO, Exercice 2026\n"
    assert text.count(anchor) == 1
    tampered = text.replace(anchor, anchor + split_footer, 1)

    first = map_notices(_document(tampered))[0].notice

    assert "N°3827" not in first.body
    assert "Mardi 01 septembre 2026" not in first.body


# --- the results part of a full issue ------------------------------------------


def test_centred_results_table_text_before_the_first_head_is_skipped_not_raised(text):
    """4477 prints 'demande de prix' as a table cell at column 110 and 4471
    'manifestation d’intérêt (...)' at column 61, both in RESULTATS PROVISOIRES,
    which precedes AVIS in every issue. Neither is a notice and neither aborts
    the issue."""
    results = (
        " " * 110 + "demande de prix\n" + " " * 61 + "manifestation d’intérêt (Au minimum 600 000 000 FCFA)\n"
        "   N°3827 – lundi 04 Mars 2024   N° 4106(bis) – Vendredi 28 mars 2025   3\n\n"
    )

    notices = [m.notice for m in map_notices(_document(results + text))]

    assert len(notices) == NOTICE_COUNT
    assert notices[0].buyer == "INSTITUT SUPERIEUR DE LOGISTIQUE DE OUAGADOUGOU"


def test_a_bare_demande_de_prix_heading_inside_avis_is_a_marker(text):
    """The head form 4471, 4477 and 4486 print without 'Avis de'."""
    centred = " " * 40
    extra = (
        f"\n{centred}Travaux\n{centred}COMMUNE DE NULLEPART\n\n{centred}Réfection du marché central\n"
        f"{centred}Demande de prix\n{centred}N° 2026-009/TEST/M/PRCP\n{centred}Financement : budget communal\n\n"
        "Montant prévisionnel : 4 000 000 FCFA HTVA et 4 720 000 FCFA TTC.\n\n"
        "1. Les offres devront être remises au plus tard le 30/09/2026 à 09 heures.\n"
    )

    notices = [m.notice for m in map_notices(_document(text + extra))]

    assert len(notices) == NOTICE_COUNT + 1
    last = notices[-1]
    assert last.buyer == "COMMUNE DE NULLEPART"
    assert last.title == "Réfection du marché central"
    assert last.external_id == "2026-009/TEST/M/PRCP"
    assert last.estimated_value == Decimal("4000000.00")
    assert last.deadline_at.date() == date(2026, 9, 30)


def test_a_heading_wrapped_over_two_phrase_lines_is_one_notice(tmp_path):
    """Issue 4485, page 43: 'AVIS A MANIFESTATION D’INTERET EN VUE D’UNE' over
    'DEMANDE DE PROPOSITIONS ALLEGEE' under one section line, one buyer and one
    title. The second live run raised on the second line."""
    import hashlib

    wrapped_manifest = json.loads(WRAPPED_MANIFEST.read_text(encoding="utf-8"))
    pdf_bytes = WRAPPED_PDF.read_bytes()
    assert hashlib.sha256(pdf_bytes).hexdigest() == wrapped_manifest["extraction"]["sha256"]
    pdf_path = tmp_path / "wrapped.pdf"
    pdf_path.write_bytes(pdf_bytes)
    wrapped_text = ocr.extract(pdf_path).text
    for line in wrapped_manifest["heading_lines_verbatim"]:
        assert line in wrapped_text

    mapped = [m.notice for m in map_notices({"url": WRAPPED_URL, "text": wrapped_text})]

    assert len(mapped) == 1
    notice = mapped[0]
    assert notice.buyer == "MINISTERE DE LA CONSTRUCTION DE LA PATRIE"
    assert notice.title == "Recrutement d’un cabinet pour la mise en œuvre des emprunts du PRéBBO"
    assert notice.body.splitlines()[:2] == wrapped_manifest["heading_lines_verbatim"]
    assert notice.external_id == "2026-034/MCP/SG/DMP/SMT-PI"
    assert notice.published_at == datetime(2026, 9, 10, tzinfo=UTC)
    assert notice.estimated_value == Decimal("33898305.00")


def test_text_whose_only_phrase_lines_have_no_head_raises():
    """A results-only extraction: markers by the letter, no notice head anywhere."""
    text = "   N° 4478 - Mardi 01 septembre 2026\n" + " " * 60 + "Avis de demande de prix\n"

    with pytest.raises(ValueError, match="none with a notice head"):
        map_notices({"url": ISSUE_URL, "text": text})


def test_the_body_ends_at_the_signature_block_not_the_next_notice(notices):
    first = notices[0]

    assert first.body.rstrip().endswith("Officier de l’Ordre du Mérite Burkinabè")
    assert "ECOLE POLYTECHNIQUE" not in first.body


# --- contact details (rule 19) ---------------------------------------------------


def test_no_phone_number_or_email_survives_in_any_body(text, notices):
    """The fixture prints 22 bodies' worth of them before stripping."""
    assert "armelnombre@gmail.com" in text
    assert "24_71_00_19/73_70_47_14" in text
    phone = re.compile(r"(?<!\d)\d{2}(?:([ \-_.])\d{2}\1\d{2}\1\d{2}|\d{6})(?!\d)")

    for notice in notices:
        assert "@" not in notice.body
        assert phone.search(notice.body) is None, notice.external_id
        assert CONTACT.search(notice.body) is None


def test_the_label_goes_with_the_number_and_the_rest_of_the_line_stays():
    line = "Publique de la Commune de Douroula, Tel : 74 67 06 82 et prendre connaissance des documents"

    assert strip_contact_details(line) == "Publique de la Commune de Douroula, et prendre connaissance des documents"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("tel (+226)72 71 72 72, les jours ouvrables", ", les jours ouvrables"),
        ("suivant :70-73-64-92, les jours", "suivant :, les jours"),
        ("Tél : 24_71_00_19/73_70_47_14; email : mairie_tenkodogo@yahoo.fr tous", "; tous"),
        ("Téléphone : 25 31 77 95 / 70 81 84 25 tous les jours", "tous les jours"),
        ("Boromo (contact : 70 93 51 22) et prendre", "Boromo et prendre"),
        ("Pierre. Tel 76475706 ; et prendre", "Pierre. ; et prendre"),
        (
            "Monsieur SOULAMA Brama, soulama brama39@gmail.com, TEL :70 45 48 80 personne",
            "Monsieur SOULAMA Brama, soulama , personne",
        ),
    ],
)
def test_each_contact_shape_the_fixture_prints_is_removed(printed, expected):
    assert strip_contact_details(printed) == expected


@pytest.mark.parametrize(
    "amount",
    [
        "(25 168 110) francs CFA HTVA",
        "11 000 000 TTC",
        "(16 101695) francs CFA HTV",
        "01 BP 5373 Ouagadougou 01",
        "Lot 10 : 5 377 272 francs CFA TTC",
    ],
)
def test_amounts_and_postal_boxes_are_not_phone_numbers(amount):
    assert strip_contact_details(amount) == amount


def test_signing_officials_stay_in_the_body(notices):
    assert notices[0].body.rstrip().endswith("ACM Oula Etienne BARRO\nOfficier de l’Ordre du Mérite Burkinabè")


# --- change detection ----------------------------------------------------------


def test_a_second_run_over_the_same_text_produces_the_same_hashes(document):
    first = [m.notice.content_hash for m in map_notices(document)]
    second = [m.notice.content_hash for m in map_notices(document)]

    assert first == second
    assert len(set(first)) == NOTICE_COUNT


def test_two_notices_with_the_same_title_still_hash_apart(notices):
    """Laye publishes two 'Réalisation de travaux diverses dans la commune de LAYE'
    notices with different references and lots; the body keeps them distinct."""
    laye = [n for n in notices if n.title == "Réalisation de travaux diverses dans la commune de LAYE"]

    assert len(laye) == 2
    assert laye[0].content_hash != laye[1].content_hash


# --- raise cases ---------------------------------------------------------------


def test_a_document_without_a_url_key_raises(text):
    with pytest.raises(ValueError, match="no 'url'"):
        map_notices({"text": text})


def test_a_document_without_a_text_key_raises():
    with pytest.raises(ValueError, match="no 'text'"):
        map_notices({"url": ISSUE_URL})


def test_text_with_no_marker_line_raises():
    with pytest.raises(ValueError, match="no centred notice marker"):
        map_notices(_document("RESULTATS PROVISOIRES\n\n   Demande de prix n°2026-01 attribuée à ...\n"))


def test_a_marker_phrase_at_the_margin_is_not_a_marker():
    """The results section prints 'Manifestation d’intérêt N°2026-03 pour ...' as a
    table row at column 3 (burkina_faso_sample.pdf); a bulletin holding only that
    has no notice."""
    with pytest.raises(ValueError, match="no centred notice marker"):
        map_notices(_document("   Manifestation d’intérêt N°2026-03 pour la constitution d’une base de données\n"))


def test_a_stale_template_stamp_with_another_number_is_never_the_issue_date(text):
    """Every issue prints 'N° 4106(bis) – Vendredi 28 mars 2025' somewhere; it
    carries a different number from the URL's and is ignored wherever it sits."""
    stale = "   N°3827 – lundi 04 Mars 2024   N° 4106(bis) – Vendredi 28 mars 2025   1\n\n"

    notices = [m.notice for m in map_notices(_document(stale + text))]

    assert {n.published_at for n in notices} == {ISSUE_PUBLISHED}


def test_a_marker_inside_avis_with_no_section_line_above_it_raises_naming_the_marker(text):
    tampered, count = re.subn(r"Fournitures et Services courants\n(\s+COMMUNE DE FOUTOURI)", r"\1", text, count=1)
    assert count == 1

    with pytest.raises(ValueError, match=r"no section line above marker line \d+ 'Avis de demande de prix'"):
        map_notices(_document(tampered))


def test_a_section_line_with_a_blank_beneath_it_raises_naming_the_marker(text):
    tampered, count = re.subn(r"\n\s+COMMUNE DE FOUTOURI\n", "\n\n", text, count=1)
    assert count == 1

    with pytest.raises(ValueError, match=r"no buyer line beneath the section line above marker line \d+"):
        map_notices(_document(tampered))


def test_the_three_head_shapes_the_other_issues_print_are_read(text):
    """4471: an all-caps title with a blank on either side. 4477: a second caps line
    (the commune) beneath the caps title. 4483: a buyer that is not all caps."""
    c = " " * 40
    extra = (
        f"\n{c}Fournitures et Services courants\n{c}REGION DE LA BOUCLE DU MOUHOUN\n\n"
        f"{c}ACQUISITION ET LIVRAISON SUR SITE DE MATERIEL ET OUTILLAGE SCOLAIRE\n\n{c}Avis de demande de prix\n"
        f"{c}N° : 2026-03/RBMH/CR/SG/PRCP\n\nCorps.\n"
        f"\n{c}Travaux\n{c}REGION DE BANKUI\n\n{c}TRAVAUX DE CONSTRUCTION D’UN JARDIN MUNICIPAL A BAGASSI (Lot2)\n\n\n"
        f"{c}COMMUNE DE BAGASSI\n\n{c}Avis d’Appel d’Offres Ouvert Accéléré\n\n"
        f"{c}N° 2026-03/RBNK/PBL/C-BGS/M/PRCP\n\nCorps.\n"
        f"\n{c}Travaux\n{c}ECOLE NATIONALE DE SANTE PUBLIQUE « Dr. COMLAN ALFRED A. QUENUM »\n\n"
        f"{c}Travaux d’aménagement au profit de l’ECOLE NATIONALE DE SANTE PUBLIQUE\n{c}Avis de demande de prix\n"
        f"{c}N°2026-08/MS/SG/ENSP/DG/DMP\n\nCorps.\n"
    )

    notices = [m.notice for m in map_notices(_document(text + extra))][-3:]

    assert [n.buyer for n in notices] == [
        "REGION DE LA BOUCLE DU MOUHOUN",
        "REGION DE BANKUI",
        "ECOLE NATIONALE DE SANTE PUBLIQUE « Dr. COMLAN ALFRED A. QUENUM »",
    ]
    assert notices[0].title == "ACQUISITION ET LIVRAISON SUR SITE DE MATERIEL ET OUTILLAGE SCOLAIRE"
    assert notices[1].title == "TRAVAUX DE CONSTRUCTION D’UN JARDIN MUNICIPAL A BAGASSI (Lot2) COMMUNE DE BAGASSI"
    assert [n.external_id for n in notices] == [
        "2026-03/RBMH/CR/SG/PRCP",
        "2026-03/RBNK/PBL/C-BGS/M/PRCP",
        "2026-08/MS/SG/ENSP/DG/DMP",
    ]
    assert [n.admin_level for n in notices] == ["local", "local", "national"]


def test_a_marker_with_no_title_between_buyer_and_marker_raises_naming_the_marker(text):
    title = "Acquisition de fournitures scolaires au profit des écoles de la CEB de la commune"
    assert text.count(title) == 1
    tampered = text.replace(title, "", 1)

    with pytest.raises(ValueError, match=r"no title between buyer 'COMMUNE DE FOUTOURI' and marker line \d+"):
        map_notices(_document(tampered))
