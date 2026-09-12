"""The DÖE contract, against the day recorded on 2026-09-12.

The fixture is one completed publication day: `pubDay=2026-09-11`, its two listing
files verbatim, and the 90 notices the listing selected, each fetched from
`?format=domain`.

Most of this file is about two hazards probing found, and both are hazards of
looking healthy while under-reading:

  - Every query parameter on the export endpoint except the date is ignored in
    silence, so nothing about the response can be assumed to match what was asked
    for. The day is checked per row and the notice id and version are checked per
    detail.
  - The representation that carries both the deadline and the buyer legal type,
    `ocds2`, omits 484 of the day's 975 notices behind a 200. The tests below pin
    the two fields that choice was made for - the deadline and `admin_level` - to
    the representation that is actually complete.

The rest checks that the parser and the mapper read what is really in the fixture,
including the awkward parts: 57 of the 90 notices come from the legacy feed with
no declared language, no buyer address and no classification block, one notice is
a joint procurement with nine buyers, six pairs are the same procurement published
twice under different ids, and every `publicationDate` carries microseconds that
the shared date parser cannot read.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from monitor.connectors.doe import (
    CLASSIFICATION_COLUMNS,
    CLASSIFICATION_FILE,
    LOOKBACK_DAYS,
    MAX_DETAIL_REQUESTS,
    NOTICE_FILE,
    DoeConnector,
    Listing,
    check_detail,
    classification_codes,
    notice_url,
    parse_classifications,
    parse_listing,
    passes_cpv,
    read_archive,
    select,
)
from monitor.models import Source
from monitor.normalise.doe import (
    COUNTRY,
    DEFAULT_ADMIN_LEVEL,
    LANGUAGE,
    admin_level,
    buyer_organisation,
    cpv_codes,
    deadline,
    level_for,
    map_notice,
    published,
    without_fraction,
)

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "doe.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "doe.yaml"
THRESHOLDS = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"

# The day the fixture's listing was asked for.
PUB_DAY = date(2026, 9, 11)


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def files(fixture) -> dict[str, str]:
    return fixture["listing"]


@pytest.fixture(scope="module")
def rows(files) -> list[Listing]:
    return parse_listing(files, PUB_DAY)


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def prefixes() -> list[str]:
    return yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))["cpv_pass_prefixes"]


@pytest.fixture(scope="module")
def selected(rows, source, prefixes) -> list[Listing]:
    return select(rows, exclude_form_types=source.exclude_notice_types, cpv_prefixes=prefixes)


@pytest.fixture(scope="module")
def details(fixture) -> list[dict]:
    return fixture["details"]


# --- the registry and the modules agree -------------------------------------


def test_the_source_yaml_and_the_mapper_agree(source):
    """The mapper's constants are sources/doe.yaml's values, not a second opinion."""
    assert source.id == "doe"
    assert source.country == COUNTRY
    assert source.language == LANGUAGE
    assert source.admin_level == DEFAULT_ADMIN_LEVEL
    assert source.connector_class == "FeedConnector"


def test_the_registry_owns_the_form_types_that_are_never_read(source):
    """Post-decision forms, excluded before a notice is fetched rather than after."""
    assert source.exclude_notice_types == ["result", "cont-modif", "dir-awa-pre"]


def test_the_window_is_completed_days_only(source, prefixes):
    """Today is never asked for: its publication batches are still running."""
    connector = DoeConnector(source, prefixes)
    days = connector.days(today=date(2026, 9, 12))

    assert days == [date(2026, 9, 11), date(2026, 9, 10)]
    assert len(days) == LOOKBACK_DAYS
    assert connector.listing_params(days[0]) == {"pubDay": "2026-09-11", "format": "csv.zip"}


def test_the_connector_bounds_its_detail_requests(source):
    """A run stops at the registry's ceiling, and never above the absolute one."""
    assert source.expected_max <= MAX_DETAIL_REQUESTS


def test_the_day_yields_within_the_registrys_expected_range(selected, source):
    """One day of the two-day window, against the band sources/doe.yaml declares."""
    assert source.expected_min <= len(selected) <= source.expected_max


# --- the listing: the archive and its two files ------------------------------


def test_the_archive_gives_up_the_two_listing_files(files):
    """Read out of the day's csv.zip, byte-order mark stripped."""
    archive = _zip(files, with_bom=True)

    read = read_archive(archive)

    assert set(read) == {NOTICE_FILE, CLASSIFICATION_FILE}
    assert read[NOTICE_FILE].startswith("noticeIdentifier,")
    assert read == files


def test_an_archive_without_the_notice_table_raises(files):
    """A changed export, not a quiet day."""
    archive = _zip({CLASSIFICATION_FILE: files[CLASSIFICATION_FILE]}, with_bom=False)

    with pytest.raises(ValueError, match=NOTICE_FILE):
        read_archive(archive)


def test_the_listing_carries_the_whole_day(rows):
    """975 notices published on 2026-09-11, every one of them listed."""
    assert len(rows) == 975
    assert all(row.notice_id and row.version for row in rows)


def test_a_renamed_listing_column_raises(files):
    """A missing column raises rather than yielding rows with an empty field."""
    broken = dict(files)
    broken[NOTICE_FILE] = files[NOTICE_FILE].replace("formType", "form_type", 1)

    with pytest.raises(ValueError, match="formType"):
        parse_listing(broken, PUB_DAY)


def test_a_row_from_another_day_raises(files):
    """The date is the one parameter that filters, so it is checked per row."""
    broken = dict(files)
    broken[NOTICE_FILE] = files[NOTICE_FILE].replace("2026-09-11T02:04:04", "2026-07-04T02:04:04", 1)

    with pytest.raises(ValueError, match="pubDay parameter was ignored"):
        parse_listing(broken, PUB_DAY)


def test_a_day_of_slack_is_allowed_because_the_export_groups_by_batch(files):
    """2 of the 1,094 rows on pubDay=2026-09-09 carried the day before."""
    shifted = dict(files)
    shifted[NOTICE_FILE] = files[NOTICE_FILE].replace("2026-09-11T02:04:04", "2026-09-10T23:59:04", 1)

    assert len(parse_listing(shifted, PUB_DAY)) == 975


def test_the_same_notice_appears_twice_as_two_versions(rows):
    """One notice was corrected 16 minutes after publication, so the key is a pair."""
    versions = {}
    for row in rows:
        versions.setdefault(row.notice_id, []).append(row.version)
    repeated = {notice_id: found for notice_id, found in versions.items() if len(found) > 1}

    assert repeated == {"7a825fa9-532e-4d59-8fa9-74f0ad75496a": ["1", "2"]}
    assert len({(row.notice_id, row.version) for row in rows}) == len(rows)


def test_the_same_notice_and_version_twice_raises(files):
    """That would mean the listing is not keyed the way the detail endpoint is."""
    lines = files[NOTICE_FILE].splitlines()
    broken = dict(files)
    broken[NOTICE_FILE] = "\n".join([*lines, lines[1]])

    with pytest.raises(ValueError, match="twice"):
        parse_listing(broken, PUB_DAY)


# --- the listing: classification, as four different spellings ----------------


def test_cpv_is_read_as_written_including_bare_divisions():
    """The four forms measured across the day's 2,231 classification rows."""
    assert classification_codes("48000000") == ["48000000"]
    assert classification_codes("45112000-5") == ["45112000"]
    assert classification_codes("45") == ["45"]
    assert classification_codes("90713000-8 80590000-6") == ["90713000", "80590000"]
    assert classification_codes("31422000,45311000,65310000") == ["31422000", "45311000", "65310000"]
    assert classification_codes("", None) == []


def test_an_empty_classification_block_is_not_a_code(files):
    """59 of 2,231 rows say nothing under no scheme, one per unclassified notice."""
    codes = parse_classifications(files[CLASSIFICATION_FILE])

    assert codes
    assert all(values for values in codes.values())


def test_a_classification_in_another_scheme_raises():
    """A scheme this connector has never seen, rather than one it quietly skips."""
    header = ",".join(CLASSIFICATION_COLUMNS)

    with pytest.raises(ValueError, match="nuts"):
        parse_classifications(f"{header}\nabc,01,nuts,DEG02,\n")


def test_a_code_under_no_scheme_raises():
    """An empty scheme with a code in it would be a code silently dropped."""
    header = ",".join(CLASSIFICATION_COLUMNS)

    with pytest.raises(ValueError, match="states a code under no scheme"):
        parse_classifications(f"{header}\nabc,01,,48000000,\n")


# --- what the run decides to read -------------------------------------------


def test_two_thirds_of_the_day_is_a_procurement_already_decided(rows, source):
    """Awards, modifications and ex-ante notices: 314 of 975, never fetched."""
    published = _counts(row.form_type for row in rows)

    assert published["competition"] == 656
    assert sum(published[form] for form in source.exclude_notice_types) == 314


def test_the_cpv_prefixes_cut_the_day_to_ninety_notices(rows, selected):
    """661 forms are still open; 90 of those are classified in the pass prefixes."""
    assert len(selected) == 90
    assert {row.form_type for row in selected} == {"competition", "change"}
    assert all(row.form_type not in {"result", "cont-modif", "dir-awa-pre"} for row in selected)
    assert len(selected) < len(rows)


def test_an_unclassified_notice_is_read_rather_than_dropped(selected, prefixes):
    """Same asymmetry as monitor/filter/cpv.py: absence is not a failed match."""
    assert passes_cpv((), prefixes) is True
    assert sum(1 for row in selected if not row.cpv_codes) == 21


def test_a_notice_classified_outside_the_prefixes_is_never_read(prefixes):
    """The buyer classified it and said it is not software, IT or consultancy."""
    assert passes_cpv(("45000000",), prefixes) is False
    assert passes_cpv(("45",), prefixes) is False
    assert passes_cpv(("45000000", "72000000"), prefixes) is True


def test_a_bare_division_is_enough_to_be_read(prefixes):
    """298 of the day's rows carry one; 23 of the 90 selected matched this way."""
    assert passes_cpv(("79",), prefixes) is True


# --- the detail response -----------------------------------------------------


def test_every_detail_carries_the_fields_the_mapper_reads(details):
    """90 notices, and the identity check passes on all of them."""
    assert len(details) == 90
    for detail in details:
        row = _listing_for(detail)
        assert check_detail(detail, row) is detail


def test_a_missing_detail_field_raises(details):
    """A renamed field raises rather than mapping a notice with no buyer."""
    broken = {key: value for key, value in details[0].items() if key != "buyers"}

    with pytest.raises(ValueError, match="buyers"):
        check_detail(broken, _listing_for(details[0]))


def test_a_detail_for_another_version_raises(details):
    """The corrected version under the old version's key would read as new daily."""
    detail = details[0]
    asked = _listing_for(detail)
    wrong = Listing(
        notice_id=asked.notice_id,
        version="99",
        form_type=asked.form_type,
        notice_type=asked.notice_type,
        published_at=asked.published_at,
        cpv_codes=asked.cpv_codes,
    )

    with pytest.raises(ValueError, match="version"):
        check_detail(detail, wrong)


def test_the_fixture_is_two_populations(details):
    """57 legacy notices and 33 eForms-DE, which is why the rules below are split."""
    versions = _counts(detail["eformsVersion"] for detail in details)

    assert versions["eforms-sdk-0.1"] == 57
    assert sum(count for name, count in versions.items() if name.startswith("eforms-de")) == 33
    assert sum(1 for detail in details if "noticeOfficialLanguages" not in detail) == 53
    assert sum(1 for detail in details if not detail.get("classification")) == 21


# --- mapping ----------------------------------------------------------------


def test_every_notice_maps(details):
    """All 90, with a title, a buyer, a url and an external id that carries the version."""
    for detail in details:
        notice = map_notice(detail).notice
        assert notice.title
        assert notice.buyer
        assert notice.source_id == "doe"
        assert notice.external_id == f"{detail['noticeIdentifier']}-{detail['noticeVersion']}"
        assert notice.url == notice_url(detail["noticeIdentifier"])
        assert notice.status == "detected"


def test_titles_stay_in_german_and_carry_no_english_rendering(details):
    """Nothing here translates (rule 9); every notice reaches step 14."""
    for detail in details:
        mapped = map_notice(detail)
        assert mapped.notice.language == "de"
        assert mapped.notice.language_confidence == 1.0
        assert mapped.title_en == ""
        assert mapped.body_en == ""


def test_a_notice_declaring_another_language_raises(details):
    """Stored under a column the lexicons would believe."""
    broken = dict(details[0]) | {"noticeOfficialLanguages": [{"value": "FRA", "listName": "eu-official-language"}]}

    with pytest.raises(ValueError, match="declares language"):
        map_notice(broken)


def test_a_bilingual_notice_raises_rather_than_one_language_being_picked(details):
    """Picking one would be picking which language title and body are stored under."""
    broken = dict(details[0]) | {
        "noticeOfficialLanguages": [{"value": "DEU"}, {"value": "ENG"}],
    }

    with pytest.raises(ValueError, match="official languages"):
        map_notice(broken)


def test_a_notice_declaring_another_country_raises(details):
    """Only 37 of 90 declare one; every declared one across the day is DEU."""
    detail = next(d for d in details if (buyer_organisation(d).get("address") or {}).get("countryCode"))
    broken = json.loads(json.dumps(detail))
    buyer_organisation(broken)["address"]["countryCode"]["value"] = "AUT"

    with pytest.raises(ValueError, match="declares country"):
        map_notice(broken)


def test_every_notice_is_german_even_where_it_says_nothing(details):
    assert {map_notice(detail).notice.country for detail in details} == {"DE"}


# --- admin_level: the reason this is one connector and not sixteen -----------


def test_admin_level_comes_from_the_tier_segment_of_the_legal_type():
    """The eForms-DE list names bund, Land and Kommune in the code itself."""
    assert level_for("koerp-oer-bund") == "national"
    assert level_for("omu-bbeh") == "national"
    assert level_for("omu-bbeh-niedrig") == "national"
    assert level_for("oberst-bbeh") == "national"
    assert level_for("koerp-oer-land") == "regional"
    assert level_for("omu-lbeh") == "regional"
    assert level_for("oberst-lbeh") == "regional"
    assert level_for("stift-oer-land") == "regional"
    assert level_for("kommun-beh") == "local"
    assert level_for("koerp-oer-kommun") == "local"
    assert level_for("anst-oer-kommun") == "local"


def test_the_eu_style_codes_are_resolved_by_the_module_that_owns_them():
    """Delegated to monitor/normalise/ted.py rather than copied into a second table."""
    assert level_for("pub-undert-ra") == "regional"
    assert level_for("pub-undert-la") == "local"
    assert level_for("pub-undert-cga") == "national"
    assert level_for("pub-undert") == "national"
    assert level_for("org-sub") == "national"
    assert level_for("grp-p-aut") == "national"
    assert level_for("def-cont") == "national"


def test_all_three_levels_are_carried_through_from_one_source(details):
    """One connector, sub-national coverage, no source per Land (BUILD_ORDER 13)."""
    levels = _counts(map_notice(detail).notice.admin_level for detail in details)

    assert levels == {"national": 72, "regional": 10, "local": 8}


def test_a_notice_with_no_legal_type_takes_the_registrys_level(details):
    """452 of the day's 975 declare none, all of them from the legacy feed."""
    detail = next(d for d in details if not (d["buyers"][0].get("buyerLegalType") or {}).get("value"))

    assert admin_level(detail) == DEFAULT_ADMIN_LEVEL


def test_an_unknown_legal_type_raises(details):
    """A level quietly read as federal is what the design point exists to avoid."""
    broken = json.loads(json.dumps(details[0]))
    broken["buyers"][0]["buyerLegalType"] = {"value": "wholly-new-code", "listName": "buyer-legal-type"}

    with pytest.raises(ValueError, match="unknown buyer-legal-type"):
        map_notice(broken)


# --- the buyer --------------------------------------------------------------


def test_the_buyer_is_resolved_through_the_organisation_reference(details):
    """Resolved on all 90, and the resolved organisation carries the buyer role."""
    for detail in details:
        reference = detail["buyers"][0]["organisationReference"]
        organisation = buyer_organisation(detail)

        assert organisation["partyIdentification"] == reference
        roles = [role["value"] for role in detail["organisationRoles"][reference]["roles"]]
        assert "buyer" in roles


def test_a_joint_procurement_names_the_body_running_it(details):
    """Nine buyers on one notice; the first is the group acting for the other eight."""
    joint = next(detail for detail in details if len(detail["buyers"]) > 1)

    assert len(joint["buyers"]) == 9
    assert joint["buyers"][0]["buyerLegalType"]["value"] == "grp-p-aut"
    assert map_notice(joint).notice.buyer.startswith("Die AOK-Bundesverband")


def test_a_buyer_reference_matching_no_organisation_raises(details):
    broken = json.loads(json.dumps(details[0]))
    broken["buyers"][0]["organisationReference"] = "ORG-9999"

    with pytest.raises(ValueError, match="matches no organisation"):
        map_notice(broken)


# --- dates ------------------------------------------------------------------


def test_the_publication_date_carries_microseconds_the_shared_parser_cannot_read(details):
    """All 90. Dropped here, because dates.py has no fractional-second format."""
    assert all("." in detail["publicationDate"].split("T")[-1] for detail in details)
    assert without_fraction("2026-09-11T02:04:04.556507Z") == "2026-09-11T02:04:04Z"
    assert without_fraction("2026-09-11T00:00:00+02:00") == "2026-09-11T00:00:00+02:00"

    for detail in details:
        published = map_notice(detail).notice.published_at
        assert published is not None
        assert published.date() == PUB_DAY


def test_the_preferred_publication_date_is_not_the_published_one(details):
    """It parses cleanly and is wrong on 72 of 90: the date the buyer asked for."""
    disagreeing = sum(
        1 for detail in details if detail["publicationDate"][:10] != detail["noticePreferredPublicationDate"][:10]
    )

    assert disagreeing == 72


def test_every_selected_notice_has_a_closing_date(details):
    """The field the chosen representation exists to carry. 80 close on tenders,
    10 on requests to participate, and no notice states both."""
    tenders = requests = 0
    for detail in details:
        stated = [lot.get("submissionTerms") or {} for lot in detail["lots"]]
        has_tenders = any(terms.get("deadlineReceiptTenders") for terms in stated)
        has_requests = any(terms.get("deadlineReceiptRequests") for terms in stated)

        assert has_tenders != has_requests
        tenders += has_tenders
        requests += has_requests
        assert map_notice(detail).notice.deadline_at is not None

    assert (tenders, requests) == (80, 10)


def test_the_deadline_is_the_earliest_lot_deadline(details):
    """A reviewer needs the soonest date they could miss, not the last."""
    detail = json.loads(json.dumps(next(d for d in details if len(d["lots"]) > 2)))
    later = "2027-01-31T10:00:00+01:00"
    for lot in detail["lots"]:
        lot["submissionTerms"] = {"deadlineReceiptTenders": later}
    detail["lots"][-1]["submissionTerms"] = {"deadlineReceiptTenders": "2026-11-30T10:00:00+01:00"}

    assert deadline(detail).month == 11


def test_a_notice_stating_no_closing_date_has_none(details):
    """20 of the day's 975. A missing deadline is visible; an invented one is not."""
    detail = json.loads(json.dumps(details[0]))
    for lot in detail["lots"]:
        lot["submissionTerms"] = {}

    assert deadline(detail) is None


def test_deadlines_keep_the_offset_the_notice_published(details):
    assert isinstance(map_notice(details[0]).notice.deadline_at, datetime)
    assert map_notice(details[0]).notice.deadline_at.utcoffset() is not None


# --- classification, value, change detection --------------------------------


def test_cpv_is_stored_as_the_eight_digit_form(details):
    """46 of 90 store codes; the other 44 published divisions or nothing at all."""
    stored = [map_notice(detail).notice.cpv_codes for detail in details]

    assert sum(1 for codes in stored if codes) == 46
    assert all(len(code) == 8 and code.isdigit() for codes in stored for code in codes)


def test_a_bare_division_cannot_be_stored_as_a_cpv_code(details):
    """23 of the 90 were selected on a division and reach the filter unclassified."""
    detail = json.loads(json.dumps(details[0]))
    detail["classification"] = {"mainClassificationCode": {"value": "79", "listName": "cpv"}}
    for lot in detail["lots"]:
        lot["classification"] = {}

    assert cpv_codes(detail) == []


def test_lot_and_notice_classification_are_the_same_codes(details):
    """69 carry both, 21 carry neither; never one without the other."""
    for detail in details:
        top = detail.get("classification") or {}
        lots = [lot.get("classification") or {} for lot in detail["lots"]]

        assert bool(top) == any(bool(block) for block in lots)


def test_a_euro_value_is_carried_as_euros_rather_than_dropped(details):
    """Every value in the day is EUR, and EUR is what is stored.

    This used to assert the opposite: `value_usd` returned None for all 90, so the
    eight stated figures were thrown away because decision 6 had no rate to convert
    them with. Migration 012 gives the pipeline a rate it can stamp, so the euro
    amount is carried as a euro amount and the conversion happens at staging.
    """
    stated = [detail for detail in details if detail["purpose"].get("estimatedValue")]

    assert len(stated) == 8
    assert {detail["purpose"]["estimatedValue"]["currencyID"] for detail in stated} == {"EUR"}

    carried = [published(detail) for detail in stated]
    assert all(currency == "EUR" for _, currency in carried)
    assert all(amount is not None and amount > 0 for amount, _ in carried)

    # And the 82 that state nothing carry nothing, rather than a zero.
    silent = [detail for detail in details if not detail["purpose"].get("estimatedValue")]
    assert len(silent) == len(details) - 8
    assert all(published(detail) == (None, None) for detail in silent)


def test_the_legacy_feed_republishes_the_same_procurement_under_new_ids(details):
    """Six pairs in one day. The content hash is what stops the second insert."""
    hashes: dict[str, list[str]] = {}
    for detail in details:
        notice = map_notice(detail).notice
        hashes.setdefault(notice.content_hash, []).append(notice.external_id)
    repeated = {digest: ids for digest, ids in hashes.items() if len(ids) > 1}

    assert len(repeated) == 6
    assert all(len(ids) == 2 for ids in repeated.values())
    assert len(hashes) == 84


# --- helpers ----------------------------------------------------------------


def _zip(files: dict[str, str], *, with_bom: bool) -> bytes:
    """The listing files back into an archive, with the byte-order mark the service writes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, text in files.items():
            bundle.writestr(name, ("﻿" if with_bom else "") + text)
    return buffer.getvalue()


def _listing_for(detail: dict) -> Listing:
    return Listing(
        notice_id=detail["noticeIdentifier"],
        version=detail["noticeVersion"],
        form_type=detail["noticeType"]["listName"],
        notice_type=detail["noticeType"]["value"],
        published_at=detail["publicationDate"],
        cpv_codes=(),
    )


def _counts(values) -> dict[str, int]:
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return counted
