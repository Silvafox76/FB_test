"""Burkina Faso's contract, against the listing recorded on 2026-09-12.

Two things this file exists to prove, and they are different questions. First,
that the listing parser reads the real Drupal markup correctly and fails loudly
on the hazards this session found in it: a second, unpaginated rendering of the
same view lower on the page: a row with two files instead of one; a combined
two-day issue whose title states one month for two day numbers; and a page that
stops being sorted. Second, that what this connector actually stores — base64-
encoded PDF bytes, untouched — is real French text once put through
`monitor.normalise.ocr.extract`, and not a scan, which is the premise this
connector's own brief got wrong and `sources/burkina_faso.yaml` corrected.

There is no per-notice item count to assert against `sources/burkina_faso.yaml`'s
`expected_items_per_run: [10, 160]`, and this is deliberate rather than an
oversight: that band is dossier references *inside* one issue, and this connector
yields one `RawNotice` per issue PDF (occasionally two). See the "consequence"
section of `monitor/connectors/burkina_faso.py`'s module docstring. What is
asserted instead is the exact, hand-verified count of issues a five-day lookback
selects from the recorded page.

A third fixture, recorded 2026-09-13: `burkina_faso_avis.pdf` is pages 20-47 of the
same real issue (Quotidien n°4478) that `burkina_faso_sample.pdf` truncates to its
first four pages. Those first four pages are RESULTATS PROVISOIRES — award results
only, per the issue's own table of contents — and carry no tender notice, no
deadline and no financing line. Pages 20-47 are the AVIS section the table of
contents names, so the normaliser this connector is blocked on (see
`sources/burkina_faso.yaml`'s "BLOCKED ON THE NORMALISER" note) has an actual
notice to be built against. This file does not parse a single notice out of it —
that is the normaliser's job — it only proves the fixture is what
`tests/contract/fixtures/burkina_faso_avis.json` says it is: real, unscanned
French prose containing at least the recorded number of each tender-notice marker.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from monitor.connectors.burkina_faso import (
    LISTING_CONTAINER,
    LOOKBACK_DAYS,
    MAX_ISSUE_REQUESTS,
    BurkinaFasoConnector,
    in_scope,
    issue_date,
    parse_listing,
)
from monitor.models import Source
from monitor.normalise import ocr

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "burkina_faso.html"
MANIFEST_FIXTURE = FIXTURES / "burkina_faso.json"
SAMPLE_PDF_FIXTURE = FIXTURES / "burkina_faso_sample.pdf"
AVIS_PDF_FIXTURE = FIXTURES / "burkina_faso_avis.pdf"
AVIS_MANIFEST_FIXTURE = FIXTURES / "burkina_faso_avis.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "burkina_faso.yaml"

# The listing was fetched on this day, so this is that run's own window.
RECORDED_ON = date(2026, 9, 12)
CUTOFF = RECORDED_ON - timedelta(days=LOOKBACK_DAYS)
assert CUTOFF == date(2026, 9, 7)

# Hand-verified against the fixture (see this file's smoke run in the build
# report): 15 rows on the page, newest n°4486 (2026-09-11) to oldest n°4471
# (2026-08-21), one row per business day plus one combined pair (n°4473-4474).
ROWS_ON_PAGE = 15
ISSUES_IN_SCOPE = 5  # n4486, n4485, n4484, n4483, n4482 — 2026-09-07 through -11
COMBINED_ISSUE_TITLE = "Quotidien n°4473 - 4474 - Mardi 25 & Mercredi 26 août 2026"
BIS_ISSUE_TITLE = "Quotidien n°4480 - Jeudi 03 septembre 2026"


@pytest.fixture(scope="module")
def listing_html() -> str:
    return LISTING_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def issues(listing_html) -> list[dict]:
    return parse_listing(listing_html)


@pytest.fixture(scope="module")
def wanted(issues) -> list[dict]:
    kept, _closed = in_scope(issues, cutoff=CUTOFF)
    return kept


@pytest.fixture(scope="module")
def sample_pdf_bytes() -> bytes:
    return SAMPLE_PDF_FIXTURE.read_bytes()


# --- the listing's real shape ---------------------------------------------------


def test_the_page_renders_the_same_view_twice_and_only_the_first_is_read(listing_html):
    """The hazard this parser is scoped around: a second, unpaginated block lower
    on the page repeats the newest rows in an identical `table.cols-2`."""
    from selectolax.parser import HTMLParser

    tree = HTMLParser(listing_html)
    all_cols_2_tables = tree.css("table.cols-2")
    scoped = tree.css_first(LISTING_CONTAINER)

    assert len(all_cols_2_tables) == 2, "the duplicate block is expected; a third would be a bigger page change"
    assert scoped is not None
    assert scoped.css("tbody tr")[0].text().strip().startswith("Quotidien n°4486")


def test_every_row_on_the_page_parses(issues):
    assert len(issues) == ROWS_ON_PAGE
    assert issues[0]["issue_date"] == date(2026, 9, 11)
    assert issues[-1]["issue_date"] == date(2026, 8, 21)


def test_the_page_is_sorted_newest_first(issues):
    dates = [issue["issue_date"] for issue in issues]
    assert dates == sorted(dates, reverse=True)


def test_a_renamed_container_raises(listing_html):
    """Without this, a renamed block would read as an archive of zero issues."""
    tampered = listing_html.replace('id="block-gavias-monte-content"', 'id="block-main-content"')

    with pytest.raises(ValueError, match="block-gavias-monte-content"):
        parse_listing(tampered)


def test_a_row_that_lost_a_cell_raises(listing_html):
    file_cell = (
        '<td headers="view-field-fichier-table-column" class="views-field views-field-field-fichier">'
        '<span class="file file--mime-application-pdf file--application-pdf">'
        '<a href="http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20n%C2%B04486_0.pdf" '
        'type="application/pdf">Quotidien n°4486_0.pdf</a></span>\n  <span>(3.2 Mo)</span>\n          </td>'
    )
    assert file_cell in listing_html, "fixture markup changed; update this test's expected substring"
    tampered = listing_html.replace(file_cell, "<span>lost</span>", 1)

    with pytest.raises(ValueError, match="cells, not 2"):
        parse_listing(tampered)


def test_a_row_with_no_file_link_raises(listing_html):
    tampered = listing_html.replace(
        '<a href="http://dgcmef.gov.bf/sites/default/files/2026-09/Quotidien%20n%C2%B04486_0.pdf" '
        'type="application/pdf">Quotidien n°4486_0.pdf</a>',
        "",
        1,
    )

    with pytest.raises(ValueError, match="no file link"):
        parse_listing(tampered)


def test_a_row_linking_to_a_non_pdf_file_raises(listing_html):
    tampered = listing_html.replace("Quotidien%20n%C2%B04486_0.pdf", "Quotidien%20n%C2%B04486_0.docx")

    with pytest.raises(ValueError, match="non-PDF file"):
        parse_listing(tampered)


def test_a_row_that_is_not_titled_quotidien_raises(listing_html):
    tampered = listing_html.replace(
        'hreflang="fr">Quotidien n°4486 – Vendredi 11 septembre 2026 </a>',
        'hreflang="fr">Avis special – Vendredi 11 septembre 2026 </a>',
        1,
    )

    with pytest.raises(ValueError, match="not a Quotidien issue"):
        parse_listing(tampered)


def test_an_unsorted_page_raises(listing_html):
    tampered = listing_html.replace("11 septembre 2026", "01 janvier 2020", 1)

    with pytest.raises(ValueError, match="not sorted newest first"):
        parse_listing(tampered)


# --- the two title shapes this session found beyond the brief -------------------


def test_a_row_can_carry_more_than_one_file(issues):
    """The brief said one PDF href per issue; n°4480 carries a same-day 'bis' too."""
    bis_row = next(issue for issue in issues if issue["title"] == BIS_ISSUE_TITLE)

    assert len(bis_row["hrefs"]) == 2
    assert any("bis" in href.lower() for href in bis_row["hrefs"])


def test_a_combined_two_day_issue_is_dated_by_its_later_day(issues):
    """ "Mardi 25 & Mercredi 26 août 2026" states one month for two day numbers;
    the earlier "25" has no month of its own and must not be misread as one."""
    combined = next(issue for issue in issues if issue["title"] == COMBINED_ISSUE_TITLE)

    assert combined["issue_date"] == date(2026, 8, 26)
    assert len(combined["hrefs"]) == 1


def test_a_title_with_no_date_raises():
    with pytest.raises(ValueError, match="no dd month yyyy date"):
        issue_date("Quotidien n°9999 - un jour sans date")


def test_an_impossible_date_raises():
    with pytest.raises(ValueError, match="invalid date"):
        issue_date("Quotidien n°9999 - 31 février 2026")


# --- the window -------------------------------------------------------------


def test_the_window_is_five_business_days(source):
    connector = BurkinaFasoConnector(source, [])

    assert connector.cutoff(today=RECORDED_ON) == CUTOFF
    assert LOOKBACK_DAYS == 5


def test_the_window_cut_keeps_exactly_the_issues_a_person_can_verify_by_hand(wanted):
    assert len(wanted) == ISSUES_IN_SCOPE
    assert [issue["issue_date"] for issue in wanted] == [
        date(2026, 9, 11),
        date(2026, 9, 10),
        date(2026, 9, 9),
        date(2026, 9, 8),
        date(2026, 9, 7),
    ]


def test_the_window_closes_on_this_single_page(issues):
    """So no second listing page is needed for this fixture's own window."""
    _kept, closed = in_scope(issues, cutoff=CUTOFF)

    assert closed is True


def test_a_window_wider_than_the_page_is_reported_not_closed(issues):
    _kept, closed = in_scope(issues, cutoff=date(2020, 1, 1))

    assert closed is False


# --- what a run actually requests -----------------------------------------------


class _FixtureClient:
    """A stand-in httpx client: the real listing, and one real PDF body for every
    file request regardless of which href was asked for.

    Only one real issue PDF was fetched and committed (see
    `tests/contract/fixtures/burkina_faso.json`); fetching and committing five
    more just to give each in-window issue its own bytes would not test anything
    this stand-in cannot already prove — the connector's request count, its
    ceiling, and its base64 encoding of whatever bytes the server returns. What
    those bytes decode to as real French prose is tested separately, against the
    one real PDF that was recorded, in the ocr.extract tests below.
    """

    def __init__(self, listing_html: str, pdf_bytes: bytes, *, listing_url: str):
        self._listing_html = listing_html
        self._pdf_bytes = pdf_bytes
        self._listing_url = listing_url
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        assert not kwargs, f"the connector sent {kwargs} with a request; this source takes no parameters"
        self.requested.append(url)
        if url == self._listing_url:
            return _StubResponse(text=self._listing_html)
        return _StubResponse(content=self._pdf_bytes)


class _StubResponse:
    def __init__(self, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        return None


def test_a_run_requests_the_listing_once_and_one_pdf_per_in_scope_file(listing_html, sample_pdf_bytes, source):
    connector = BurkinaFasoConnector(source, [])
    connector.cutoff = lambda today=None: CUTOFF
    client = _FixtureClient(listing_html, sample_pdf_bytes, listing_url=source.list_url)

    raw_notices = connector.fetch_raw(client)

    assert client.requested[0] == source.list_url
    assert len(client.requested) == 1 + ISSUES_IN_SCOPE
    assert len(raw_notices) == ISSUES_IN_SCOPE
    assert all(raw.mime == "application/pdf" for raw in raw_notices)
    assert all(raw.source_id == "burkina_faso" for raw in raw_notices)
    assert all(base64.b64decode(raw.payload) == sample_pdf_bytes for raw in raw_notices)


def test_a_bis_reissue_is_fetched_as_its_own_raw_notice(listing_html, sample_pdf_bytes, source):
    """A wider window that reaches n°4480 fetches both of its files."""
    connector = BurkinaFasoConnector(source, [])
    connector.cutoff = lambda today=None: date(2026, 9, 3)
    client = _FixtureClient(listing_html, sample_pdf_bytes, listing_url=source.list_url)

    raw_notices = connector.fetch_raw(client)

    bis_urls = [raw.url for raw in raw_notices if "bis" in raw.url.lower()]
    assert len(bis_urls) == 1


def test_the_ceiling_truncates_a_run(listing_html, sample_pdf_bytes, source, monkeypatch):
    """A lowered ceiling caps the files fetched; `fetch_raw` logs a warning when it
    does (`burkina_faso_ceiling_reached`), matching the house pattern in
    `monitor/connectors/ebrd.py` and `monitor/connectors/worldbank.py`, neither of
    which asserts on the log call itself — only the truncation it causes."""
    import monitor.connectors.burkina_faso as module

    monkeypatch.setattr(module, "MAX_ISSUE_REQUESTS", 2)
    connector = module.BurkinaFasoConnector(source, [])
    connector.cutoff = lambda today=None: CUTOFF
    client = _FixtureClient(listing_html, sample_pdf_bytes, listing_url=source.list_url)

    raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == 2


def test_the_max_issue_requests_constant_has_headroom_over_the_window(source):
    """5 business days is normally 5 requests and rarely 6 (one bis); the ceiling
    is not sized to the flagged mismatch with expected_items_per_run."""
    assert MAX_ISSUE_REQUESTS > LOOKBACK_DAYS


# --- what the connector stores really is a PDF, and really is French -----------


def test_the_sample_pdf_is_not_a_scan(sample_pdf_bytes, tmp_path):
    """The premise this connector's brief got wrong, checked against the one real
    issue this session fetched independently of the brief."""
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(sample_pdf_bytes)

    extraction = ocr.extract(pdf_path)

    assert extraction.route == ocr.TEXT_LAYER
    assert extraction.text_layer_chars >= ocr.text_layer_minimum()


def test_the_extracted_text_is_real_french_prose_matching_the_registrys_language(sample_pdf_bytes, source, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(sample_pdf_bytes)

    extraction = ocr.extract(pdf_path)

    assert source.language == "fr"
    # Vocabulary a scanned-image fallback or a placeholder file could not produce:
    # the masthead's own French institutional language, present on every issue.
    for phrase in ("DGCMEF", "Marchés", "Ouagadougou"):
        assert phrase in extraction.text


def test_a_connectors_raw_notice_payload_round_trips_through_ocr_extract(
    listing_html, sample_pdf_bytes, source, tmp_path
):
    """The exact path this connector's payload is meant to be read by: base64
    decode, write to a file, extract — nothing the connector itself does."""
    connector = BurkinaFasoConnector(source, [])
    connector.cutoff = lambda today=None: CUTOFF
    client = _FixtureClient(listing_html, sample_pdf_bytes, listing_url=source.list_url)

    raw_notice = connector.fetch_raw(client)[0]
    pdf_path = tmp_path / "from_payload.pdf"
    pdf_path.write_bytes(base64.b64decode(raw_notice.payload))

    extraction = ocr.extract(pdf_path)

    assert extraction.route == ocr.TEXT_LAYER
    assert "DGCMEF" in extraction.text


def test_a_scan_with_no_client_raises_rather_than_being_silently_skipped(tmp_path):
    """ocr.extract's own contract, exercised here because this connector's payload
    is exactly what it is meant to guard: a future scanned issue with no Textract
    credential configured must fail loudly, not disappear as a healthy empty run."""
    below_threshold = tmp_path / "blank.pdf"
    # A syntactically minimal PDF with no text objects: pdftotext reads it as zero
    # characters, which is what a scanned image with no text layer also produces.
    below_threshold.write_bytes(
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )

    with pytest.raises(RuntimeError, match="Textract"):
        ocr.extract(below_threshold)


# --- provenance: the fixture is what it says it is -------------------------------


def test_the_manifest_matches_the_committed_files(manifest, listing_html, sample_pdf_bytes):
    import hashlib

    assert manifest["recorded_on"] == RECORDED_ON.isoformat()
    assert hashlib.sha256(listing_html.encode("utf-8")).hexdigest() == manifest["listing_sha256"]
    assert hashlib.sha256(sample_pdf_bytes).hexdigest() == manifest["sample_pdf"]["truncated_sha256"]
    assert len(sample_pdf_bytes) == manifest["sample_pdf"]["truncated_bytes"]


def test_the_sample_pdf_is_not_the_full_issue(manifest, sample_pdf_bytes):
    """Documented rather than silent: this fixture is 4 of 49 pages."""
    assert manifest["sample_pdf"]["truncated_pages"] < manifest["sample_pdf"]["full_issue_pages"]
    assert len(sample_pdf_bytes) < manifest["sample_pdf"]["full_issue_bytes"]


# --- the AVIS fixture: pages 20-47, tender notices rather than award results ----


@pytest.fixture(scope="module")
def avis_manifest() -> dict:
    return json.loads(AVIS_MANIFEST_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def avis_pdf_bytes() -> bytes:
    return AVIS_PDF_FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def avis_extraction(tmp_path_factory):
    """The one extraction call this file makes, shared by every marker-count test
    below rather than re-run per test, since `pdftotext` is a subprocess call."""
    pdf_path = tmp_path_factory.mktemp("avis") / "burkina_faso_avis.pdf"
    pdf_path.write_bytes(AVIS_PDF_FIXTURE.read_bytes())
    return ocr.extract(pdf_path)


def test_the_avis_fixture_opens_and_matches_its_recorded_size_and_hash(avis_manifest, avis_pdf_bytes):
    import hashlib

    assert len(avis_pdf_bytes) == avis_manifest["extraction"]["bytes"]
    assert hashlib.sha256(avis_pdf_bytes).hexdigest() == avis_manifest["extraction"]["sha256"]


def test_the_avis_fixture_has_the_recorded_page_count(avis_manifest, tmp_path):
    """pdfinfo's own page count, not a guess from the 20-47 range's arithmetic,
    though they agree (28 pages either way)."""
    import subprocess

    pdf_path = tmp_path / "avis.pdf"
    pdf_path.write_bytes(AVIS_PDF_FIXTURE.read_bytes())

    result = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, check=True, timeout=30)
    output = result.stdout.decode("utf-8")
    pages_line = next(line for line in output.splitlines() if line.startswith("Pages:"))
    pages = int(pages_line.split(":")[1].strip())

    assert pages == avis_manifest["extraction"]["pages_extracted"] == 28


def test_the_avis_fixture_is_not_the_full_issue_and_not_the_award_results_sample(avis_manifest, avis_pdf_bytes):
    """Documented rather than silent: this is a different 28 of 49 pages from
    `burkina_faso_sample.pdf`'s first 4, and both are smaller than the full issue."""
    assert avis_manifest["extraction"]["pages_extracted"] < avis_manifest["source_issue"]["full_issue_pages"]
    assert len(avis_pdf_bytes) < avis_manifest["source_issue"]["full_issue_bytes"]
    full_sha256 = "881e554493dd3e5a3bf6ca97e75d2177fe1ce5e5a160b6406416b4878f006b7d"
    assert avis_manifest["source_issue"]["full_issue_sha256"] == full_sha256


def test_the_avis_fixture_has_an_embedded_font_text_layer(avis_extraction, avis_manifest):
    """The same premise `test_the_sample_pdf_is_not_a_scan` checks for the award-
    results fixture, checked again here because a page-range extraction is a
    different file and could in principle have lost its text layer in the cut."""
    assert avis_extraction.route == ocr.TEXT_LAYER
    assert avis_extraction.text_layer_chars >= ocr.text_layer_minimum()
    assert avis_extraction.text_layer_chars == avis_manifest["extraction"]["text_layer_chars"]


def test_the_avis_fixture_is_real_french_prose_not_the_award_results_section(avis_extraction):
    """Vocabulary that only appears in the AVIS section, not RESULTATS PROVISOIRES:
    a call for offers names a submission deadline and a financing source; an award
    result does not use this phrasing for either. "dgcmef" (lowercase) rather than
    the masthead's uppercase "DGCMEF" - that acronym is only spelled in capitals on
    page 1's masthead sidebar, outside this fixture's 20-47 range; every occurrence
    inside pages 20-47 is the lowercase "www.dgcmef.gov.bf" footer URL repeated on
    every page, which is exactly as good a not-a-scan, not-page-1 signal."""
    for phrase in ("Avis de demande de prix", "Financement", "dgcmef.gov.bf"):
        assert phrase in avis_extraction.text


@pytest.mark.parametrize(
    ("manifest_key", "pattern"),
    [
        ("avis_de_demande_de_prix", r"avis de demande de prix"),
        ("avis_d_appel_d_offres", r"avis d[’']appel d[’']offres"),
        ("demande_de_propositions", r"demande de propositions"),
        ("manifestation_d_interet", r"manifestation d[’'](?:int[eé]r[eê]t)"),
        ("avis_de_sollicitation", r"avis de sollicitation"),
        ("date_limite", r"date limite"),
        ("depot_des_offres", r"d[eé]p[oô]t des offres"),
        ("financement_colon", r"financement\s*:"),
        ("montant_previsionnel", r"montant pr[eé]visionnel"),
    ],
)
def test_a_tender_marker_meets_its_recorded_count(avis_extraction, avis_manifest, manifest_key, pattern):
    """The recorded counts are a floor, not an exact match a future re-extraction
    must hit precisely, since `pdftotext`'s line-wrapping can shift by a character
    between poppler versions; a *lower* count than recorded is the failure this
    guards, because that is what a changed layout or a bad extraction would produce
    — a real regression, not noise. See `burkina_faso_avis.json`'s
    `measured_inventory` for how each of these was counted (re.findall against this
    same `ocr.extract` text, not a shell pipeline, so there is no locale mismatch
    between what was recorded and what this test measures)."""
    expected = avis_manifest["measured_inventory"][manifest_key]
    found = len(re.findall(pattern, avis_extraction.text, re.IGNORECASE))

    assert found >= expected, f"{manifest_key}: expected at least {expected}, found {found}"


def test_a_renamed_or_reflowed_avis_section_fails_loudly_rather_than_yielding_zero(avis_extraction):
    """The deliberate zero-yield guard this task asked for: if the fixture ever
    stopped containing tender notices (e.g. a future re-extraction accidentally
    pointed back at the RESULTATS PROVISOIRES pages, or the source changed its
    section heading text and pdftotext read nothing recognisable), asserting only
    `>= 0` would pass on a broken fixture forever. A named marker with zero hits
    must raise the test, not silently pass, per rule 4 (zero-yield is a failure
    state, not an empty success)."""
    demande_de_prix = len(re.findall(r"avis de demande de prix", avis_extraction.text, re.IGNORECASE))

    assert demande_de_prix > 0, (
        "zero 'Avis de demande de prix' markers found in the AVIS fixture — this is "
        "the failure this test exists to catch, not a passing empty result"
    )


def test_the_ten_recorded_montant_lines_are_present_verbatim(avis_extraction, avis_manifest):
    """Every one of the ten sample lines the manifest quotes for the normaliser's
    XOF parser must actually be findable in the extracted text, unmodified — this
    is the guard against transcribing a line into the manifest that does not match
    what `pdftotext` really produces."""
    for line in avis_manifest["montant_previsionnel_sample_lines_verbatim"]:
        assert line in avis_extraction.text


def test_the_three_recorded_buyer_heading_lines_are_present_verbatim(avis_extraction, avis_manifest):
    for line in avis_manifest["buyer_heading_lines_verbatim"]:
        assert line in avis_extraction.text


def test_the_avis_manifest_page_range_matches_the_extraction_command(avis_manifest):
    expected_range = (
        "20-47 (1-indexed, inclusive, matching the task's instruction and the "
        "table of contents' own AVIS start/last-entry pages)"
    )
    assert avis_manifest["extraction"]["page_range"] == expected_range
    assert avis_manifest["table_of_contents_measured"]["avis_starts_page"] == 20
    assert avis_manifest["table_of_contents_measured"]["avis_last_toc_entry_page"] == 47


# --- the registry entry ----------------------------------------------------------


def test_the_registry_entry_is_disabled_until_a_live_fetch(source):
    assert source.enabled is False
    assert source.tos_status == "reviewed_ok"
    assert source.id == "burkina_faso"
    assert source.country == "BF"
    assert source.language == "fr"
    assert source.connector_class == "FeedConnector"
    assert source.schedule == "0 11 * * 1-5"


def test_the_registry_health_band_is_measured_at_the_issue_level_not_the_run_level(source):
    """Documented so the mismatch this connector's docstring flags is provable
    from the registry entry itself rather than only asserted in prose: five real,
    hand-verified in-window issues sit far below the registry's per-issue band,
    which is not the number `fetch()` produces (see the module docstring)."""
    assert ISSUES_IN_SCOPE < source.expected_min
