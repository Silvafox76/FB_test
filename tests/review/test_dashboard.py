"""Decision 71: the review app's main page becomes a dashboard hub.

The queue moved to its own tab at `/queue` (covered in `test_app.py`, updated to the
new path); this file covers what replaced it at `/`: the figure strip, the region
selector, the countries and sources tables, and the nav that now carries seven links.

Region names are never hand-written here. `chip_pending` reads them straight off the
rendered page instead of a literal list, so this file cannot drift from
`config/thresholds.yaml`'s own `regions:` block the way rule 23 forbids a template
from doing.
"""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote_plus

import pytest
from starlette.testclient import TestClient

from monitor.stage.stager import FIXTURE_ID_FLOOR, regions_config
from review.app import app

pytestmark = pytest.mark.roles


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_pending_candidate(owner, review):
    """A minimal pending candidate in a chosen country and region, and its own teardown.

    A factory rather than an eager fixture (like `test_app.py`'s `staged_with_value`)
    so a test can read the dashboard's counts *before* calling it and compare against
    the same counts after - the only way to prove a specific candidate moved a figure
    without asserting an absolute value against a database this suite does not own.
    """
    created: list[tuple[str, str, str]] = []

    def make(*, country: str, region: str) -> str:
        marker = uuid.uuid4().hex[:8]
        candidate_id = f"C{FIXTURE_ID_FLOOR + int(marker, 16) % (1_000_000 - FIXTURE_ID_FLOOR):06d}"
        source_id = f"test-dash-{marker}"
        content_hash = f"sha256:dash{marker}"

        owner.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, 'Test dashboard source', %s, 'national', 'en', 'feed', 'api', 'FeedConnector',
                    1, 'cleared', false, 1, 50, 3)
            """,
            (source_id, country),
        )
        owner.execute(
            """
            insert into notices_raw (content_hash, source_id, url, storage_path, mime)
            values (%s, %s, 'https://example.invalid/notice', 'raw/test.json', 'application/json')
            """,
            (content_hash, source_id),
        )
        notice_id = owner.execute(
            """
            insert into notices (content_hash, source_id, url, title, country, admin_level,
                                 language, status, filter_result)
            values (%s, %s, 'https://example.invalid/notice', 'Dashboard test notice', %s,
                    'national', 'en', 'scored', 'cpv 72, lexicon en: test')
            returning id
            """,
            (content_hash, source_id, country),
        ).fetchone()[0]
        owner.execute(
            """
            insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                    buyer, country, admin_level, summary_en, matched_functions,
                                    system_names, procurement_type, eligibility_flags, deadline_at)
            values (%s, %s, 78, 'pending_review', %s, 'en', 'Dashboard test candidate',
                    'Ministry of Test', %s, 'national', 'Test summary', '[]'::jsonb, %s,
                    'system', %s, '2026-11-30T17:00:00Z')
            """,
            (candidate_id, notice_id, region, country, [], []),
        )
        created.append((candidate_id, notice_id, source_id))
        return candidate_id

    yield make

    review.rollback()
    for candidate_id, notice_id, source_id in created:
        owner.execute("delete from events where entity_id = %s", (candidate_id,))
        owner.execute("delete from candidate_notices where candidate_id = %s", (candidate_id,))
        owner.execute("delete from candidates where id = %s", (candidate_id,))
        owner.execute("delete from notices where id = %s", (notice_id,))
        owner.execute("delete from notices_raw where source_id = %s", (source_id,))
        owner.execute("delete from sources where id = %s", (source_id,))


def chip_pending(page: str, region_name: str) -> int:
    """The pending count shown on one region's chip, parsed off the rendered page."""
    match = re.search(rf'{re.escape(region_name)} <span class="muted">\((\d+)\)</span>', page)
    assert match, f"no chip for {region_name!r} on the dashboard"
    return int(match.group(1))


def nav_hrefs(page: str) -> list[str]:
    nav = re.search(r"<nav>(.*?)</nav>", page, re.S)
    assert nav, "no <nav> on the page"
    return re.findall(r'href="([^"]+)"', nav.group(1))


# --- the dashboard renders, for all and for one region -------------------------


def test_the_dashboard_renders_for_all_regions(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Dashboard" in response.text
    assert "Notices held" in response.text
    assert "Top countries" in response.text
    assert "Sources covering this selection" in response.text


def test_the_dashboard_renders_for_one_configured_region(client):
    response = client.get("/?region=West+Africa")

    assert response.status_code == 200
    assert "West Africa" in response.text


def test_an_unknown_region_is_a_404_not_a_silent_all(client):
    response = client.get("/?region=Nowhereland")

    assert response.status_code == 404
    assert "Nowhereland" in response.text


# --- a candidate belongs to its own region's counts and not another's ----------


def test_a_fixture_candidate_appears_in_its_regions_counts_and_not_anothers(client, make_pending_candidate):
    before_west = chip_pending(client.get("/").text, "West Africa")
    before_europe = chip_pending(client.get("/").text, "Europe")

    make_pending_candidate(country="GH", region="West Africa")

    after_west = chip_pending(client.get("/").text, "West Africa")
    after_europe = chip_pending(client.get("/").text, "Europe")

    assert after_west == before_west + 1
    assert after_europe == before_europe


# --- a `covers` source is scoped by its covered countries, not its sentinel ----


def test_a_multi_country_source_is_scoped_by_covers_not_the_multi_sentinel(client):
    """`country: multi` (World Bank, EBRD, EU Funding and Tenders, BOAD, OPEC Fund,
    MCC, UNDP, UNGM) is a routing sentinel, not a place. The source is described
    entirely by its own `covers` list and must never surface under the default
    region, which is where mapping the literal string `multi` through
    `regions_config()` used to put it (`regions_config()` names no country
    `multi`, so it fell through to the `default` key).
    """
    default_region = quote_plus(regions_config()["default"])

    assert "World Bank procurement notices" not in client.get(f"/?region={default_region}").text
    assert "World Bank procurement notices" in client.get("/?region=West+Africa").text


def test_ted_is_scoped_by_covers_not_the_eu_sentinel(client):
    """TED's own `country: EU` is the same case as `multi`: `EU` is not an ISO code
    `regions_config()` lists, so it must not fall through to the default region
    either - TED is described by its `covers` list of EU/EEA countries.
    """
    default_region = quote_plus(regions_config()["default"])

    assert "Tenders Electronic Daily" not in client.get(f"/?region={default_region}").text
    assert "Tenders Electronic Daily" in client.get("/?region=Europe").text


# --- the queue moved, and the nav says where everything is ---------------------


def test_the_queue_is_served_at_its_own_path(client, staged):
    page = client.get("/queue").text

    assert staged in page
    assert "minutes of review" in page


def test_the_nav_carries_the_seven_links_in_order(client):
    expected = ["/", "/queue", "/decided", "/sources", "/audit", "/export", "/metrics"]

    for path in ("/", "/queue", "/decided", "/sources", "/audit", "/export", "/metrics"):
        assert nav_hrefs(client.get(path).text) == expected
