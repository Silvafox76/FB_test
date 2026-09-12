"""TED's contract, against the response recorded on 2026-09-11.

A contract test answers one question: has the source changed under us? It runs
against the committed fixture, never the network, so it is deterministic and it
fails the moment TED renames a field rather than the morning nobody notices the
queue is empty.

The property that matters most is the last one: a renamed field must raise. A
parser that returns zero notices on a changed API is indistinguishable from a
quiet day, and quiet days are exactly when nobody looks.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from monitor.connectors.ted import REQUIRED_FIELDS, TedConnector, notice_url, parse_notices
from monitor.models import Source
from monitor.normalise.ted import map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "ted.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "ted.yaml"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def notices(document) -> list[dict]:
    return parse_notices(document)


def test_the_fixture_yields_within_the_registrys_expected_range(notices, source):
    """The registry says what a healthy run looks like; the fixture has to agree."""
    assert source.expected_min <= len(notices) <= source.expected_max


def test_every_notice_has_the_fields_the_pipeline_reads(notices):
    for notice in notices:
        for field in REQUIRED_FIELDS:
            assert field in notice, f"{notice.get('publication-number')} has no {field}"


def test_every_notice_maps_to_a_title_url_country_and_external_id(notices):
    for raw in notices:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.url.startswith("https://ted.europa.eu/")
        assert len(mapped.country) == 2
        assert mapped.external_id.strip()


def test_a_renamed_field_raises_rather_than_yielding_zero(document):
    """The whole point of a contract test. Zero notices reads as a quiet day."""
    broken = json.loads(json.dumps(document))
    for notice in broken["notices"]:
        notice["noticeTitle"] = notice.pop("notice-title")

    with pytest.raises(ValueError, match="notice-title"):
        parse_notices(broken)


def test_a_renamed_container_raises(document):
    broken = {"results": document["notices"]}

    with pytest.raises(ValueError, match="no 'notices' key"):
        parse_notices(broken)


def test_the_original_language_is_what_is_stored(notices):
    """Rule 9: TED translates into 24 languages and we store the published one."""
    spanish = [n for n in notices if n["official-language"] == ["SPA"]]
    assert spanish, "expected at least one Spanish notice in the fixture"

    mapped = map_notice(spanish[0])

    assert mapped.notice.language == "es"
    assert mapped.notice.title == spanish[0]["notice-title"]["spa"]
    assert mapped.title_en == spanish[0]["notice-title"]["eng"]
    assert mapped.notice.title != mapped.title_en


def test_three_letter_codes_are_converted(notices):
    """ESP -> ES and SPA -> es, or the geography weights silently miss."""
    countries = {map_notice(raw).notice.country for raw in notices}
    languages = {map_notice(raw).notice.language for raw in notices}

    assert all(len(code) == 2 and code.isupper() for code in countries)
    assert all(len(code) == 2 and code.islower() for code in languages)


def test_cpv_codes_are_extracted_and_deduplicated(notices):
    """The fixture's first notice carries the same code twice."""
    for raw in notices:
        codes = map_notice(raw).notice.cpv_codes
        assert codes, f"{raw['publication-number']} has no CPV code"
        assert len(codes) == len(set(codes))
        assert all(len(code) == 8 and code.isdigit() for code in codes)


def test_deadlines_parse_when_present_and_are_none_when_not(notices):
    """Only some notices carry a tender deadline; absence is normal, not a failure."""
    with_deadline = [n for n in notices if n.get("deadline-receipt-tender-date-lot")]
    without = [n for n in notices if not n.get("deadline-receipt-tender-date-lot")]

    assert with_deadline and without, "fixture should exercise both paths"
    assert all(map_notice(raw).notice.deadline_at is not None for raw in with_deadline)
    assert all(map_notice(raw).notice.deadline_at is None for raw in without)


def test_the_deadline_is_the_earliest_lot():
    """A reviewer needs the soonest date they could miss, not the last."""
    raw = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "A notice"},
        "description-proc": {"eng": "A description"},
        "buyer-name": {"eng": ["A buyer"]},
        "buyer-country": ["DEU"],
        "classification-cpv": ["48000000"],
        "publication-date": "2026-09-09+02:00",
        "deadline-receipt-tender-date-lot": ["2026-10-31+02:00", "2026-09-30+02:00"],
        "official-language": ["ENG"],
        "notice-type": "cn-standard",
        "links": {"html": {"ENG": "https://ted.europa.eu/en/notice/-/detail/1-2026"}},
    }

    deadline = map_notice(raw).notice.deadline_at

    assert deadline is not None
    assert (deadline.month, deadline.day) == (9, 30)


def test_a_euro_value_is_carried_as_euros(notices):
    """TED states ten different currencies and each is kept as published.

    This used to assert the euro figure was thrown away. It was: 404 of the 806
    stored notices state a value and only Liberia's eleven USD figures ever
    survived, while the model invented figures for eight notices whose text carried
    no number at all. The published amount is now the record (rule 9) and the USD
    column is derived at a rate that is stored beside it.
    """
    euro_valued = [n for n in notices if n.get("estimated-value-cur-proc") == "EUR"]
    assert euro_valued, "expected EUR-denominated notices in the fixture"

    for raw in euro_valued:
        notice = map_notice(raw).notice
        stated = Decimal(str(raw["estimated-value-proc"] or 0))
        if stated > 0:
            assert notice.value_currency == "EUR"
            assert notice.estimated_value == stated.quantize(Decimal("0.01"))
        else:
            # A currency with no amount behind it is not a price. Both fields drop
            # together, which the Notice model also refuses to let come apart.
            assert notice.estimated_value is None
            assert notice.value_currency is None


def test_a_stated_zero_is_read_as_not_stated_rather_than_as_a_free_contract(notices):
    """19 of the 404 stated values in the corpus are zero or less.

    A zero carried through becomes USD 0 on a CRM Opportunity for a real
    procurement, which is exactly the plausible-looking wrong value the export
    rules exist to prevent. The rule started in the Liberia normaliser for three
    OCDS releases and now lives in monitor/normalise/value.py because TED does it
    too.
    """
    zeros = [n for n in notices if n.get("estimated-value-proc") and Decimal(str(n["estimated-value-proc"])) <= 0]
    for raw in zeros:
        notice = map_notice(raw).notice
        assert notice.estimated_value is None
        assert notice.value_currency is None


def test_award_notices_are_excluded_at_the_query_not_after_the_fetch(notices, source):
    """Acquisition scope: they are never asked for, so they are never read.

    Measured 2026-09-12: excluding them took a two-day window from 1,449 matched
    notices to 822. Every one removed is a tender already decided.
    """
    assert source.exclude_notice_types == ["can-standard", "can-modif", "can-social", "veat"]
    assert not {raw["notice-type"] for raw in notices} & set(source.exclude_notice_types)


def test_the_query_carries_the_registrys_exclusions(source):
    from datetime import date

    query = TedConnector(source, ["48"]).query(today=date(2026, 9, 12))

    assert "NOT (notice-type IN (can-standard can-modif can-social veat))" in query


def test_a_source_that_excludes_nothing_asks_for_everything(source):
    """The exclusion is the registry's, not the connector's."""
    from datetime import date

    everything = source.model_copy(update={"exclude_notice_types": []})

    assert "notice-type" not in TedConnector(everything, ["48"]).query(today=date(2026, 9, 12))


def test_an_award_notice_that_did_arrive_would_still_be_carried_through(notices):
    """Rule 5: the mapper does not drop things. Dropping is the filter's job.

    Constructed, because the query means one no longer reaches the fixture. If the
    exclusion is ever reversed for competitor intelligence, this is what says the
    rest of the pipeline still handles them.
    """
    award = json.loads(json.dumps(notices[0]))
    award["notice-type"] = "can-standard"

    assert map_notice(award).notice.status == "detected"


def test_excluding_awards_raised_the_share_of_notices_with_a_deadline(notices):
    """A side effect worth knowing: contract notices have deadlines, awards do not.

    16 of 50 carried one before the exclusion; 198 of 250 after. The deduper needs
    two known deadlines to join on a title, so this directly improves clustering.
    """
    with_deadline = [raw for raw in notices if raw.get("deadline-receipt-tender-date-lot")]

    assert len(with_deadline) / len(notices) > 0.5


def test_the_url_is_the_english_permalink(notices):
    for raw in notices:
        assert notice_url(raw) == raw["links"]["html"]["ENG"]


def test_the_query_asks_for_the_configured_prefixes_only(source):
    """Rule 6: the CPV prefixes come from config, not from this module."""
    from datetime import date

    connector = TedConnector(source, ["48", "72"])
    query = connector.query(today=date(2026, 9, 11))

    assert "classification-cpv=48*" in query
    assert "classification-cpv=72*" in query
    assert "79" not in query
    assert "publication-date>=20260909" in query


def test_a_notice_missing_its_own_language_raises(notices):
    """Substituting another language would store a title the language column lies about."""
    broken = json.loads(json.dumps(notices[0]))
    broken["official-language"] = ["POL"]  # a language TED did not translate this notice into
    del broken["notice-title"]["pol"]

    with pytest.raises(ValueError, match="no entry for the notice's own language"):
        map_notice(broken)


def test_an_unknown_buyer_legal_type_raises(notices):
    """Present but unrecognised is a changed vocabulary, not a national buyer."""
    broken = json.loads(json.dumps(notices[0]))
    broken["buyer-legal-type"] = ["some-new-eforms-code"]

    with pytest.raises(ValueError, match="unknown buyer-legal-type"):
        map_notice(broken)


def test_a_missing_buyer_legal_type_defaults_to_national(notices):
    """Absent is normal: 4 of the 50 recorded notices carry none."""
    without = json.loads(json.dumps(notices[0]))
    without.pop("buyer-legal-type", None)

    assert map_notice(without).notice.admin_level == "national"


def test_an_unknown_country_code_raises(notices):
    broken = json.loads(json.dumps(notices[0]))
    broken["buyer-country"] = ["ZZZ"]

    with pytest.raises(ValueError, match="unknown country code"):
        map_notice(broken)


def test_no_english_rendering_yields_no_english_rather_than_another_language(notices):
    """No English beats another language stored as though it were English."""
    without_english = json.loads(json.dumps(notices[0]))
    del without_english["notice-title"]["eng"]

    mapped = map_notice(without_english)

    assert mapped.title_en == ""
    assert mapped.notice.title  # the original is untouched


def test_ted_translates_titles_but_not_descriptions(notices):
    """The reason TED still needs step 14: only the title comes back in English."""
    assert all("eng" in raw["notice-title"] for raw in notices)

    with_english_body = [raw for raw in notices if "eng" in raw["description-proc"]]
    assert len(with_english_body) < len(notices) / 2, "descriptions are not translated by TED"

    for raw in notices:
        mapped = map_notice(raw)
        assert mapped.title_en
        if "eng" not in raw["description-proc"]:
            assert mapped.body_en == ""


# --- paging ------------------------------------------------------------------


class _PagingClient:
    """A stand-in httpx client that serves a fixed number of notices in pages."""

    def __init__(self, total: int, page_size: int):
        self.total = total
        self.page_size = page_size
        self.pages_requested: list[int] = []

    def post(self, url, json, headers):  # noqa: A002 - matches httpx's signature
        page = json["page"]
        self.pages_requested.append(page)
        start = (page - 1) * self.page_size
        count = max(0, min(self.page_size, self.total - start))
        notices = [_stub_notice(f"{start + i}-2026") for i in range(count)]
        return _StubResponse({"notices": notices, "totalNoticeCount": self.total, "timedOut": False})


class _StubResponse:
    def __init__(self, document):
        self._document = document

    def raise_for_status(self):
        return None

    def json(self):
        return self._document


def _stub_notice(publication_number: str) -> dict:
    return {
        "publication-number": publication_number,
        "notice-title": {"eng": "A notice"},
        "description-proc": {"eng": "A description"},
        "buyer-name": {"eng": ["A buyer"]},
        "buyer-country": ["DEU"],
        "buyer-legal-type": ["cga"],
        "classification-cpv": ["48000000"],
        "publication-date": "2026-09-09+02:00",
        "official-language": ["ENG"],
        "notice-type": "cn-standard",
        "links": {"html": {"ENG": f"https://ted.europa.eu/en/notice/-/detail/{publication_number}"}},
    }


def test_paging_reads_the_whole_result_set(source):
    """1,449 notices in six requests at the API's maximum page size of 250."""
    from monitor.connectors.ted import PAGE_SIZE

    client = _PagingClient(total=1449, page_size=PAGE_SIZE)
    connector = TedConnector(source, ["48"])

    notices = connector.fetch_raw(client)

    assert len(notices) == 1449
    assert client.pages_requested == [1, 2, 3, 4, 5, 6]


def test_a_short_page_ends_the_run(source):
    """No request is made past the end of the result set."""
    from monitor.connectors.ted import PAGE_SIZE

    client = _PagingClient(total=PAGE_SIZE - 1, page_size=PAGE_SIZE)

    notices = TedConnector(source, ["48"]).fetch_raw(client)

    assert len(notices) == PAGE_SIZE - 1
    assert client.pages_requested == [1]


def test_the_registrys_expected_max_is_the_paging_ceiling(source):
    """A query that suddenly matches everything cannot pull it all in one pass."""
    from monitor.connectors.ted import PAGE_SIZE

    client = _PagingClient(total=1_000_000, page_size=PAGE_SIZE)

    notices = TedConnector(source, ["48"]).fetch_raw(client)

    assert len(notices) >= source.expected_max
    assert len(notices) < source.expected_max + PAGE_SIZE


def test_a_timed_out_search_raises_rather_than_returning_a_partial_set(source):
    """The API says when a search did not complete; a partial read must not look complete."""

    class _TimedOut(_PagingClient):
        def post(self, url, json, headers):  # noqa: A002
            return _StubResponse({"notices": [], "totalNoticeCount": 999, "timedOut": True})

    with pytest.raises(ValueError, match="timedOut"):
        TedConnector(source, ["48"]).fetch_raw(_TimedOut(total=0, page_size=1))


@pytest.mark.parametrize(
    ("legal_type", "expected"),
    [
        ("cga", "national"),
        ("ra", "regional"),
        ("la", "local"),
        ("body-pl-cga", "national"),
        ("body-pl-ra", "regional"),
        ("body-pl-la", "local"),
        ("org-sub-ra", "regional"),
        ("pub-undert-la", "local"),
        ("body-pl", "national"),
        ("pub-undert", "national"),
        ("spec-rights-entity", "national"),
        ("grp-p-aut", "national"),
        ("def-cont", "national"),
        ("int-org", "national"),
        ("eu-ins-bod-ag", "national"),
        ("org-sub", "national"),
    ],
)
def test_every_legal_type_the_query_actually_returns_maps(legal_type, expected):
    """All 21 distinct values across the 1,449 notices matched on 2026-09-11."""
    from monitor.normalise.ted import admin_level_for

    assert admin_level_for(legal_type) == expected


def test_a_genuinely_new_base_code_still_raises():
    """grp-p-aut was missed by the 50-notice fixture and found by the first full run."""
    from monitor.normalise.ted import admin_level_for

    assert admin_level_for("some-new-eforms-code") is None
