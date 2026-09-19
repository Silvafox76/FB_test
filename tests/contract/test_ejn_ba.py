"""EJN e-Nabavke BiH's contract, against the pass recorded live on 2026-09-19.

The fixture (`tests/contract/fixtures/ejn_ba.json.gz`, gzip because the 62-request
transcript is 5 MB uncompressed and the repository refuses files over 4 MB) is not one response but a
transcript: every one of the 62 HTTP requests `EjnBaConnector.fetch_raw` made for
one real seven-day window, in order, each as `{"url": ..., "status": ...,
"body": ...}`. That shape exists because this connector is not a single page the
way `tests/contract/test_fts.py`'s is: it pages the notice listing, then joins
three more entity sets (`Lots`, `LotCpvCodeLinks`, `CpvCodes`), each batched and
each itself paged. `test_one_whole_pass_replays_the_measured_counts` below
replays the whole thing through a mock transport keyed on the exact recorded URL,
the same shape `tests/contract/test_liberia.py` uses for its own two-endpoint
replay, extended to this connector's four endpoints.

**Personal data was found in the live response and is redacted here, not in the
connector**, the same split `tests/contract/test_liberia.py` documents for its
own fixture. Every one of the 672 recorded notices names at least one contact
person in one of four fields
(`AdditionalInformationContactPersonName`, `DocumentationTakeOverContactPerson`,
`OfferDeliveryContactPersonName`, `ForeignContactPerson`), each paired with an
email and a phone/fax number - 505 distinct named individuals across the window,
some behind an office-style address, some behind a personal-looking one
(`marina.toroman@gmail.com`, `srdjan.cegar@gmail.com`) on a free mail provider.
Rule 19 and rule 20 forbid a contact detail landing in a fixture committed to
git, so every one of those twelve fields that was non-empty in the live response
has been replaced with a fixed placeholder (`Redacted Contact` /
`redacted@example.invalid` / `000-000-0000`) in `tests/contract/fixtures/
ejn_ba.json`; a field that was already empty in the live response was left empty.
7,943 values were redacted this way.
`test_the_committed_fixture_carries_no_real_contact_details` guards against a
re-recording quietly undoing this. The connector itself does not touch these
fields - rule 5 leaves what becomes `Notice.body` to the normaliser that does not
exist yet, and rule 9 makes the record what was published.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.ejn_ba import (
    ADMIN_UNIT_TYPES,
    REQUIRED_LINK_FIELDS,
    REQUIRED_LOT_FIELDS,
    EjnBaConnector,
    announced_at,
    batched,
    build_payload,
    collect_pages,
    notice_url,
    odata_datetime,
    validate_notices,
    validate_rows,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "ejn_ba.json.gz"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "ejn_ba.yaml"

# The exact window this fixture was recorded with. Pinned here rather than left to
# `EjnBaConnector.window()`'s own default so the replay test issues the identical
# requests the fixture was keyed on - see the module docstring.
RECORDED_SINCE = datetime(2026, 9, 12, 17, 40, 12, tzinfo=UTC)
RECORDED_UNTIL = datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)

# What the recorded pass actually held. Asserted rather than described, so a
# re-recorded fixture that quietly holds a different count fails here, not
# somewhere subtler.
RECORDED_NOTICE_COUNT = 672
RECORDED_LOT_COUNT = 1101
RECORDED_LINK_COUNT = 1489
RECORDED_CPV_CODE_COUNT = 459
RECORDED_REQUEST_COUNT = 62

# The placeholders test_the_committed_fixture_carries_no_real_contact_details
# checks every non-empty contact field against. See the module docstring.
REDACTED_NAME = "Redacted Contact"
REDACTED_EMAIL = "redacted@example.invalid"
REDACTED_PHONE = "000-000-0000"

NAME_FIELDS = (
    "AdditionalInformationContactPersonName",
    "DocumentationTakeOverContactPerson",
    "OfferDeliveryContactPersonName",
    "ForeignContactPerson",
)
EMAIL_FIELDS = (
    "AdditionalInformationEmailAddress",
    "DocumentationTakeOverEmailAddress",
    "OfferDeliveryEmailAddress",
)
PHONE_FIELDS = (
    "AdditionalInformationPhoneNumber",
    "DocumentationTakeOverPhoneNumber",
    "OfferDeliveryPhoneNumber",
    "AdditionalInformationFaxNumber",
    "DocumentationTakeOverFaxNumber",
    "OfferDeliveryFaxNumber",
)


@pytest.fixture(scope="module")
def fixture() -> dict:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> EjnBaConnector:
    return EjnBaConnector(source, ["48", "72", "79"])


@pytest.fixture(scope="module")
def by_url(fixture) -> dict[str, dict]:
    """Every recorded request, keyed on its exact URL string."""
    return {record["url"]: record["body"] for record in fixture["requests"]}


def _rows_from(fixture: dict, path_fragment: str) -> list[dict]:
    """Every `value` row from every recorded request whose URL names this entity."""
    rows: list[dict] = []
    for record in fixture["requests"]:
        if path_fragment in record["url"]:
            rows.extend(record["body"]["value"])
    return rows


@pytest.fixture(scope="module")
def notices(fixture) -> list[dict]:
    return _rows_from(fixture, "open.ejn.gov.ba/ProcurementNotices")


@pytest.fixture(scope="module")
def lots(fixture) -> list[dict]:
    return _rows_from(fixture, "/Lots?")


@pytest.fixture(scope="module")
def links(fixture) -> list[dict]:
    return _rows_from(fixture, "/LotCpvCodeLinks?")


@pytest.fixture(scope="module")
def codes(fixture) -> list[dict]:
    return _rows_from(fixture, "/CpvCodes?")


def replaying_client(by_url: dict[str, dict]) -> httpx.Client:
    """A client that serves the recorded documents by exact URL and refuses
    anything else - the shape `tests/contract/test_liberia.py` uses for its own
    two-endpoint replay, here keyed on all 62 recorded requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in by_url:
            raise AssertionError(f"the connector asked for {url}, which is not part of the recorded pass")
        return httpx.Response(200, json=by_url[url])

    return httpx.Client(transport=httpx.MockTransport(handler))


# --- volume and shape -----------------------------------------------------------


def test_the_recorded_window_yields_within_the_registrys_expected_range(notices, source):
    assert len(notices) == RECORDED_NOTICE_COUNT
    assert source.expected_min <= len(notices) <= source.expected_max


def test_every_notice_id_is_distinct(notices):
    ids = [notice["Id"] for notice in notices]
    assert len(ids) == len(set(ids)) == RECORDED_NOTICE_COUNT


def test_the_join_hops_hold_the_measured_counts(lots, links, codes):
    assert len(lots) == RECORDED_LOT_COUNT
    assert len(links) == RECORDED_LINK_COUNT
    assert len(codes) == RECORDED_CPV_CODE_COUNT


def test_the_whole_pass_took_the_measured_number_of_requests(fixture):
    """62, not roughly 2,016 - the number the join would cost done per notice
    rather than batched with `in (...)`. See the module docstring on
    `monitor/connectors/ejn_ba.py`."""
    assert len(fixture["requests"]) == RECORDED_REQUEST_COUNT


# --- every item is tender stage --------------------------------------------------


def test_every_item_is_tender_stage(notices, source):
    """`ProcurementNotices` is a call-for-tenders entity set by construction, not
    by a field on the record - award, termination, prior-information and annual
    notices each live in a separate entity set (`sources/ejn_ba.yaml`). The
    connector is built against that one entity set, and every recorded notice
    still carries an open application deadline, which an award or termination
    record would not."""
    assert source.api_url.endswith("/ProcurementNotices")
    for notice in notices:
        assert notice["ApplicationDeadlineDateTime"], f"{notice['Id']} has no open deadline"
        assert announced_at(notice) < datetime.fromisoformat(
            notice["ApplicationDeadlineDateTime"].replace("Z", "+00:00")
        )


# --- required fields and the sort ------------------------------------------------


def test_every_notice_has_the_required_fields(notices):
    validate_notices(notices)  # does not raise


def test_a_renamed_notice_field_raises(notices):
    broken = json.loads(json.dumps(notices))
    for notice in broken:
        notice["ProcedureTitle"] = notice.pop("ProcedureName")

    with pytest.raises(ValueError, match="ProcedureName"):
        validate_notices(broken)


def test_an_unrecognised_administrative_unit_type_raises(notices):
    broken = json.loads(json.dumps(notices))
    broken[0]["ContractingAuthorityAdministrativeUnitType"] = "Province"

    with pytest.raises(ValueError, match="Province"):
        validate_notices(broken)


def test_an_unsorted_page_raises(notices):
    broken = json.loads(json.dumps(notices))
    broken[0], broken[-1] = broken[-1], broken[0]

    with pytest.raises(ValueError, match="not sorted"):
        validate_notices(broken)


def test_the_recorded_notices_are_genuinely_sorted_newest_first(notices):
    """What test_a_renamed_notice_field_raises and friends rely on: the fixture
    itself must hold this property or the raise tests above would prove nothing."""
    validate_notices(notices)


# --- administrative unit values seen ---------------------------------------------


def test_administrative_unit_values_seen(notices):
    """`sources/ejn_ba.yaml` names a 6-value enum; the recorded window hit all six."""
    seen = {notice["ContractingAuthorityAdministrativeUnitType"] for notice in notices}
    assert seen == ADMIN_UNIT_TYPES


# --- CPV present after the join --------------------------------------------------


def test_cpv_present_after_the_join_on_every_recorded_notice(notices, lots, links, codes):
    """Every one of the 672 recorded notices resolves to at least one CPV code
    through the Lots -> LotCpvCodeLinks -> CpvCodes join - measured, not assumed
    (see the module docstring's note that this is a property of one window, not a
    documented guarantee, and is carried as an empty list rather than raised on
    when it does not hold)."""
    for notice in notices:
        payload = build_payload(notice, lots, links, codes)
        assert payload["cpv_codes"], f"notice {notice['Id']} resolved no CPV code"
        for code in payload["cpv_codes"]:
            assert len(code["Code"]) == 10 and code["Code"][:8].isdigit() and code["Code"][8] == "-"


def test_a_procedure_with_no_lots_raises(notices, lots, links, codes):
    sample = notices[0]
    lots_without_sample = [lot for lot in lots if lot["ProcedureId"] != sample["ProcedureId"]]

    with pytest.raises(ValueError, match="no Lots row"):
        build_payload(sample, lots_without_sample, links, codes)


def test_a_link_naming_an_unresolved_cpv_code_id_raises(notices, lots, links, codes):
    sample = notices[0]
    sample_lot_ids = {lot["Id"] for lot in lots if lot["ProcedureId"] == sample["ProcedureId"]}
    sample_links = [link for link in links if link["LotId"] in sample_lot_ids]
    assert sample_links, "the fixture's first notice should have at least one lot_cpv_link"

    missing_code_id = sample_links[0]["CpvCodeId"]
    codes_missing_one = [code for code in codes if code["Id"] != missing_code_id]

    with pytest.raises(ValueError, match="unresolved CpvCodeId"):
        build_payload(sample, lots, links, codes_missing_one)


# --- required fields on the join hops --------------------------------------------


def test_every_lot_has_the_required_fields(lots):
    validate_rows("Lots", lots, REQUIRED_LOT_FIELDS)  # does not raise


def test_every_link_has_the_required_fields(links):
    validate_rows("LotCpvCodeLinks", links, REQUIRED_LINK_FIELDS)  # does not raise


def test_a_missing_join_field_raises(lots):
    broken = json.loads(json.dumps(lots))
    del broken[0]["ProcedureId"]

    with pytest.raises(ValueError, match="ProcedureId"):
        validate_rows("Lots", broken, REQUIRED_LOT_FIELDS)


# --- pagination and batching helpers ----------------------------------------------


def test_batched_splits_into_the_requested_size():
    chunks = list(batched(list(range(250)), 100))
    assert [len(chunk) for chunk in chunks] == [100, 100, 50]
    assert chunks[0][0] == 0 and chunks[-1][-1] == 249


def test_odata_datetime_is_seconds_precision_with_a_z_suffix():
    assert odata_datetime(datetime(2026, 9, 12, 10, 30, 5, 123456, tzinfo=UTC)) == "2026-09-12T10:30:05Z"


def test_collect_pages_follows_odata_nextlink():
    """A two-page response, replayed: page one's `@odata.nextLink` is followed,
    not assumed, and paging stops the moment a response omits it."""
    page_one = {"value": [{"Id": 1}], "@odata.nextLink": "https://example.invalid/Thing?$skip=1"}
    page_two = {"value": [{"Id": 2}]}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.invalid/Thing":
            return httpx.Response(200, json=page_one)
        if str(request.url) == "https://example.invalid/Thing?$skip=1":
            return httpx.Response(200, json=page_two)
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        rows = collect_pages(client, "https://example.invalid/Thing", None)

    assert rows == [{"Id": 1}, {"Id": 2}]


def test_collect_pages_raises_on_a_renamed_container():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError, match="'value'"):
        collect_pages(client, "https://example.invalid/Thing", None)


def test_a_non_200_raises():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        collect_pages(client, "https://example.invalid/Thing", None)


# --- the notice URL ---------------------------------------------------------------


def test_the_url_is_built_from_the_notice_id(notices, source):
    for notice in notices[:10]:
        url = notice_url(source.api_url, notice)
        assert url == f"{source.api_url}?$filter=Id eq {notice['Id']}"


# --- the window default -----------------------------------------------------------


def test_the_window_defaults_to_seven_days_before_now(connector):
    now = datetime(2026, 9, 19, 14, 0, tzinfo=UTC)
    assert connector.window(now=now) == datetime(2026, 9, 12, 14, 0, tzinfo=UTC)


# --- one whole pass, replayed ------------------------------------------------------


def test_one_whole_pass_replays_the_measured_counts(connector, by_url):
    with replaying_client(by_url) as client:
        raw_notices = connector.fetch_raw(client, since=RECORDED_SINCE, until=RECORDED_UNTIL)

    assert len(raw_notices) == RECORDED_NOTICE_COUNT
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "ejn_ba"
        assert raw.mime == "application/json"
        assert raw.url.startswith("https://open.ejn.gov.ba/ProcurementNotices?$filter=Id eq ")
        seen_urls.add(raw.url)

        payload = json.loads(raw.payload)
        assert sorted(payload) == ["cpv_codes", "lot_cpv_links", "lots", "notice"]
        assert payload["notice"]["Id"] is not None
        assert payload["lots"]
        assert payload["cpv_codes"]

    assert len(seen_urls) == RECORDED_NOTICE_COUNT, "each notice must have its own URL"


def test_an_unrecorded_request_fails_loudly_rather_than_hanging_or_stubbing(connector, by_url):
    """The replaying client itself: a request outside the recorded pass must raise,
    not silently answer with something plausible."""
    trimmed = dict(by_url)
    first_listing_url = next(url for url in trimmed if "ProcurementNotices" in url and "skip" not in url.lower())
    del trimmed[first_listing_url]

    with replaying_client(trimmed) as client, pytest.raises(AssertionError, match="not part of the recorded pass"):
        connector.fetch_raw(client, since=RECORDED_SINCE, until=RECORDED_UNTIL)


# --- personal data, redacted in the fixture ---------------------------------------


def test_the_committed_fixture_carries_no_real_contact_details(notices):
    """Guards the redaction described in this file's own docstring: a re-recorded
    fixture must be redacted again before it is committed, not committed as-is."""
    checked = 0
    for notice in notices:
        for field, placeholder in [(f, REDACTED_NAME) for f in NAME_FIELDS]:
            value = notice.get(field) or ""
            assert value in ("", placeholder), f"notice {notice['Id']} {field} is not redacted: {value!r}"
            checked += 1
        for field, placeholder in [(f, REDACTED_EMAIL) for f in EMAIL_FIELDS]:
            value = notice.get(field) or ""
            assert value in ("", placeholder), f"notice {notice['Id']} {field} is not redacted: {value!r}"
            checked += 1
        for field, placeholder in [(f, REDACTED_PHONE) for f in PHONE_FIELDS]:
            value = notice.get(field) or ""
            assert value in ("", placeholder), f"notice {notice['Id']} {field} is not redacted: {value!r}"
            checked += 1
    assert checked == RECORDED_NOTICE_COUNT * (len(NAME_FIELDS) + len(EMAIL_FIELDS) + len(PHONE_FIELDS))


def test_every_notice_names_at_least_one_contact_in_the_live_response(notices):
    """Documents the scale of the redaction above: this is not an occasional
    field, it is on every recorded notice (module docstring)."""
    with_contact = [n for n in notices if any(n.get(f) == REDACTED_NAME for f in NAME_FIELDS)]
    assert len(with_contact) == RECORDED_NOTICE_COUNT
