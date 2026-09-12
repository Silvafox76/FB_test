"""The browser connector's failure policy, which is most of what it is for.

Step 17's second acceptance test is that *a deliberately stale Playwright selector
fails loudly rather than returning zero silently*, and that failure has two halves in
two places, so it is asserted twice here:

  - the render waits for the row selector, so a page that loads its shell and never
    its rows times out in Playwright rather than handing a shell to the parser;
  - `require_rows` re-asserts the same selector against the returned HTML, so a page
    that rendered fine under a selector that no longer names anything raises
    `StaleSelector` before a single row is mapped.

Neither half needs a browser to test. `require_rows` is pure, and the render's wiring
is checked against a fake Playwright that records what it was asked to wait for — so
this whole file runs with the `browser` extra uninstalled, which is the arrangement
that keeps the pipeline image free of a Chromium in the first place.
"""

from __future__ import annotations

import sys
import types

import pytest

from monitor.connectors.base import ConnectorError
from monitor.connectors.browser_base import (
    BROWSER_ENV,
    BrowserConnector,
    StaleSelector,
    browser_available,
    render,
    require_rows,
)
from monitor.models import Source

# A listing as a browser hands it back: a wrapper, rows, and the text a parser wants.
# Deliberately small. The per-source contract tests hold the real recorded pages; what
# is being tested here is the guard, not anyone's markup.
RENDERED = """
<html><body>
  <div id="results">
    <div class="tender-row"><a href="/notice/1">Supply of an IFMIS</a><span class="close">2026-10-01</span></div>
    <div class="tender-row"><a href="/notice/2">Payroll system audit</a><span class="close">2026-10-14</span></div>
  </div>
</body></html>
"""

# The same page after a redesign renamed the row class. Nothing about it is broken:
# it is a valid page with content on it, which is exactly why a connector that only
# counted rows would call this a quiet day.
REDESIGNED = RENDERED.replace("tender-row", "notice-card")

SELECTOR = "div.tender-row"


def source(**overrides) -> Source:
    fields = {
        "id": "testportal",
        "name": "A portal that renders its rows",
        "country": "GH",
        "admin_level": "national",
        "language": "en",
        "stream": "portal",
        "access": "public_listing",
        "connector": "BrowserConnector",
        "wave": 1,
        "schedule": "0 6 * * *",
        "list_url": "https://example.invalid/tenders",
        "row_selector": SELECTOR,
        "tos_status": "reviewed_ok",
        "enabled": False,
        "owner": "ryan.dear",
        "health": {"expected_items_per_run": [1, 50], "max_consecutive_failures": 3},
    }
    fields.update(overrides)
    return Source.model_validate(fields)


class Portal(BrowserConnector):
    """The smallest real subclass: it maps rows and counts how often it was asked to."""

    def __init__(self, src: Source) -> None:
        super().__init__(src)
        self.parsed = 0

    def parse(self, markup, url, selector):
        self.parsed += 1
        return [
            self.raw_notice(url=url, payload=row.html, mime="text/html")
            for row in require_rows(markup, selector, self.source_id, url)
        ]


# --- the guard itself ------------------------------------------------------------


def test_rows_that_match_come_back():
    assert len(require_rows(RENDERED, SELECTOR, "testportal", "https://example.invalid/tenders")) == 2


def test_a_stale_selector_raises_rather_than_returning_nothing():
    """The acceptance test, in one line: a page that rendered, a selector that is dead."""
    with pytest.raises(StaleSelector) as raised:
        require_rows(REDESIGNED, SELECTOR, "testportal", "https://example.invalid/tenders")

    # The message has to be enough to act on without opening the code: which source,
    # which selector, which page, and what to do next.
    message = str(raised.value)
    assert "testportal" in message
    assert SELECTOR in message
    assert "https://example.invalid/tenders" in message
    assert "row_selector" in message


def test_an_empty_page_is_the_same_failure_and_not_an_empty_success():
    """Rule 4: zero yield on a source that yields is a failure state, not a quiet day."""
    with pytest.raises(StaleSelector):
        require_rows("<html><body></body></html>", SELECTOR, "testportal", "https://example.invalid/tenders")


# --- the selector comes from config, and there is only one of it -------------------


def test_the_selector_is_read_from_the_registry_entry():
    assert Portal(source()).row_selector() == SELECTOR


def test_an_onboarded_browser_source_may_not_know_its_selector_yet():
    """source-onboarder decides a portal needs a browser before anyone has rendered it.

    It reaches that verdict by fetching the HTML and finding the rows absent from it;
    what the rows are *called* needs the page rendered, which is the connector's job.
    A disabled entry without a selector is onboarding having happened, so it loads.
    """
    assert source(row_selector="", enabled=False).row_selector == ""


def test_an_enabled_browser_source_without_a_selector_is_refused_on_load():
    """`enabled` is what decides whether anything fetches, so it is where this binds."""
    with pytest.raises(ValueError, match="row_selector"):
        source(row_selector="", enabled=True)


def test_a_blank_selector_still_raises_at_fetch_time():
    """The guard that actually stands between a blank selector and a silent zero.

    The load-time check is scoped to enabled sources, so this is the one that holds
    for every path into a fetch, including a source enabled by an environment the
    registry check never saw.
    """
    portal = Portal(source(row_selector="", enabled=False))

    with pytest.raises(ValueError, match="row_selector"):
        portal.row_selector()


def test_a_selector_on_a_non_browser_source_is_refused():
    """A FeedConnector reading a row_selector would be a second, unused code path."""
    with pytest.raises(ValueError, match="row_selector"):
        source(connector="FeedConnector")


# --- the render waits for that same selector ---------------------------------------


class FakePage:
    """A page that loads, and records what it was asked to wait for."""

    def __init__(self, markup: str, missing: str | None = None) -> None:
        self.markup = markup
        self.missing = missing
        self.waited_for: list[str] = []

    def goto(self, url, wait_until, timeout):
        return types.SimpleNamespace(ok=True, status=200)

    def wait_for_selector(self, selector, timeout):
        self.waited_for.append(selector)
        if self.missing is not None and selector == self.missing:
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {selector}")

    def content(self):
        return self.markup


def fake_playwright(page: FakePage, seen: dict):
    """A stand-in for `playwright.sync_api`, installed into sys.modules for one test."""

    class Context:
        def new_page(self):
            return page

    class Browser:
        def new_context(self, user_agent):
            seen["user_agent"] = user_agent
            return Context()

        def close(self):
            seen["closed"] = True

    class Chromium:
        def launch(self):
            return Browser()

    class Driver:
        chromium = Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: Driver()
    package = types.ModuleType("playwright")
    package.sync_api = module
    return package, module


@pytest.fixture
def browser_env(monkeypatch):
    monkeypatch.setenv(BROWSER_ENV, "1")
    monkeypatch.setenv("MONITOR_USER_AGENT", "FreeBalance-OpportunityMonitor/0.1 (+pfm@example.org)")


@pytest.fixture
def playwright_double(monkeypatch):
    def install(page: FakePage) -> dict:
        seen: dict = {}
        package, module = fake_playwright(page, seen)
        monkeypatch.setitem(sys.modules, "playwright", package)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
        return seen

    return install


def test_the_render_waits_for_the_selector_the_parser_will_use(browser_env, playwright_double):
    """One selector, not two. The wait and the parse cannot drift apart."""
    page = FakePage(RENDERED)
    playwright_double(page)

    markup = render("https://example.invalid/tenders", SELECTOR, "testportal")

    assert page.waited_for == [SELECTOR]
    assert len(require_rows(markup, SELECTOR, "testportal", "https://example.invalid/tenders")) == 2


def test_a_selector_that_never_appears_times_out_in_the_browser(browser_env, playwright_double):
    """The other half of the acceptance test: the page loaded and the rows never came."""
    page = FakePage(REDESIGNED, missing=SELECTOR)
    playwright_double(page)

    with pytest.raises(TimeoutError):
        render("https://example.invalid/tenders", SELECTOR, "testportal")


def test_the_browser_announces_itself(browser_env, playwright_double):
    """Rule 21 applies to a rendered page exactly as it does to an httpx call."""
    seen = playwright_double(FakePage(RENDERED))

    render("https://example.invalid/tenders", SELECTOR, "testportal")

    assert seen["user_agent"].startswith("FreeBalance-OpportunityMonitor/")


def test_the_browser_is_closed_even_when_the_selector_is_stale(browser_env, playwright_double):
    """A leaked Chromium per failed run would outlive the schedule that started it."""
    seen = playwright_double(FakePage(REDESIGNED, missing=SELECTOR))

    with pytest.raises(TimeoutError):
        render("https://example.invalid/tenders", SELECTOR, "testportal")

    assert seen["closed"] is True


# --- the connector's error contract -------------------------------------------------


def test_a_stale_selector_reaches_the_caller_as_a_connector_error(browser_env, playwright_double, monkeypatch):
    """`fetch` wraps every cause with the source id so health can be updated (rule 3)."""
    playwright_double(FakePage(REDESIGNED))  # renders, but the rows are named something else now
    portal = Portal(source())

    with pytest.raises(ConnectorError) as raised:
        portal.fetch()

    assert raised.value.source_id == "testportal"
    assert isinstance(raised.value.cause, StaleSelector)
    assert portal.parsed == 1  # it got as far as parsing, and refused there


def test_a_good_page_yields_raw_notices(browser_env, playwright_double):
    playwright_double(FakePage(RENDERED))

    notices = Portal(source()).fetch()

    assert len(notices) == 2
    assert {notice.source_id for notice in notices} == {"testportal"}
    assert all(notice.mime == "text/html" for notice in notices)


def test_a_process_without_a_browser_says_so_rather_than_failing_on_import(monkeypatch):
    """The pipeline image has no Chromium by design; the message should say that."""
    monkeypatch.delenv(BROWSER_ENV, raising=False)
    assert browser_available() is False

    with pytest.raises(RuntimeError, match="browser"):
        render("https://example.invalid/tenders", SELECTOR, "testportal")
