"""The browser connector, for a portal whose listing is not in the served HTML.

This is the second and last connector base, and BUILD_ORDER step 17 names it. It
subclasses `FeedConnector` rather than standing beside it, so there is still one
base class in this codebase and one specialisation of it that swaps the transport:
everything about failure policy, the source id on the error and `raw_notice` is
inherited unchanged. A portal connector subclasses this; nothing subclasses it
further (CLAUDE.md, "No abstract base beyond FeedConnector").

**Why a browser at all.** `monitor/connectors/ebrd.py` reads an HTML listing with
httpx and selectolax and needs no browser, and that is the cheaper path every
source should take. A browser is justified by one measured fact only: the notice
rows are absent from the server-rendered HTML and injected by JavaScript. The
source's registry entry records that measurement. A portal that renders its rows
server-side is a `FeedConnector` even if it looks modern.

**It runs in its own container.** `docker-compose.yml` builds a `browser` service
from the same repository with the `browser` extra and a Chromium binary, and it
runs the browser sources and nothing else. The `pipeline` image installs neither.
That is what step 17 means by the cheap fetchers never carrying a browser
dependency, and it is why `playwright` is imported inside `render` rather than at
the top of this module: with the extra uninstalled, importing this module, parsing
a fixture and running every test in `tests/contract/` all still work. Only actually
driving a browser needs the package.

There is no browser server and nothing listens. An earlier shape of this had the
pipeline connect to `playwright run-server` over a websocket, which would have put
a listener inside the deployment for the first time; running the connector where
the browser already is removes the question rather than answering it (rule 17).

**One selector, asserted twice.** The source declares `row_selector`, the CSS
selector for a notice row in the listing. It is passed to `page.wait_for_selector`
so the render waits for the rows themselves rather than for a timer, and the same
string is re-asserted against the returned HTML by `require_rows`. That is not a
second selector (rule 1) and neither is a fallback for the other: they are two
different failures. The first is "the page never produced rows"; the second is "the
page produced something and the selector no longer names it", which is what a
portal redesign does. Both raise.

**Zero rows is never returned quietly**, which is step 17's acceptance test. A
stale selector on a page that loaded fine is the failure this whole module is
shaped around: `parse` calls `require_rows` before it maps anything, so a selector
that matches nothing raises `ConnectorError` naming the selector and the source.
Returning `[]` would make a redesigned portal look like a quiet week, and
`monitor/health/source_health.py` would be left to infer from a count what the
connector already knew (rule 4).
"""

from __future__ import annotations

import os

import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.base import ConnectorError, FeedConnector, user_agent
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# How long a page is given to produce its rows. Longer than the 30 seconds httpx is
# given in base.py because this waits for scripts to run and not only for bytes, and
# several West African portals are served from hosts with a slow first byte. It is
# not a retry and nothing is attempted twice: one pass, then the source is unhealthy.
RENDER_TIMEOUT_MS = 45_000

# The environment variable the browser container sets to say a Chromium is present.
# Checked before a render is attempted so "the browser extra is not installed" is a
# clear message rather than a ModuleNotFoundError from inside a connector.
BROWSER_ENV = "MONITOR_BROWSER"


class StaleSelector(Exception):
    """A selector that named rows when the connector was written names none now.

    Its own type rather than a bare ValueError because it is the one failure this
    module exists to make loud, and a test asserting the behaviour should be able to
    name it. It is wrapped into `ConnectorError` by `fetch`, like every other cause.
    """

    def __init__(self, source_id: str, selector: str, url: str) -> None:
        super().__init__(
            f"{source_id}: no element matches {selector!r} on {url}. The listing rendered and the "
            "selector matched nothing in it, which is what a portal redesign looks like. Re-record "
            "the fixture, read what the rows are called now, and change row_selector in the source's "
            "registry entry. Nothing is fetched from this source until that is done."
        )
        self.source_id = source_id
        self.selector = selector
        self.url = url


def browser_available() -> bool:
    """Whether this process is the one with a Chromium behind it."""
    return os.environ.get(BROWSER_ENV, "").strip().lower() in {"1", "true", "yes"}


def require_rows(markup: str, selector: str, source_id: str, url: str) -> list:
    """The rows the selector names, or `StaleSelector`. Never an empty list.

    Pure: it takes markup and returns nodes, so the contract test runs it against the
    recorded fixture with no browser anywhere (CLAUDE.md, contract tests).
    """
    rows = HTMLParser(markup).css(selector)
    if not rows:
        raise StaleSelector(source_id, selector, url)
    return rows


def render(url: str, selector: str, source_id: str) -> str:
    """Load a page in Chromium, wait for the rows, return the rendered HTML.

    One attempt. `wait_for_selector` waits for the same `row_selector` the parser
    will use, so a page that loads its shell and never its rows fails here with a
    Playwright `TimeoutError` rather than returning a shell for the parser to find
    empty. The import is inside the function so this module is importable, and every
    test in the suite runs, without the browser extra installed.
    """
    if not browser_available():
        raise RuntimeError(
            f"{BROWSER_ENV} is not set: this process has no Chromium. Browser sources run in the "
            "`browser` service in docker-compose.yml, which is built with the `browser` extra and a "
            "Chromium binary; the pipeline image carries neither (BUILD_ORDER step 17)."
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            # The user agent is set on the context, so it is on the document request and
            # on every subresource the page fetches. A browser that announced itself as
            # a stock Chrome would be a disguised client, which rule 21 forbids as
            # plainly for a rendered page as for an httpx call.
            context = browser.new_context(user_agent=user_agent())
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=RENDER_TIMEOUT_MS)
            if response is None:
                raise RuntimeError(f"{url} returned no response")
            if not response.ok:
                raise RuntimeError(f"{url} returned HTTP {response.status}")
            page.wait_for_selector(selector, timeout=RENDER_TIMEOUT_MS)
            return page.content()
        finally:
            browser.close()


class BrowserConnector(FeedConnector):
    """Base for a portal whose rows arrive by JavaScript. Subclasses implement `parse`.

    `fetch` is overridden rather than `fetch_raw` because the inherited one opens an
    `httpx.Client` this transport has no use for. The error contract is the same and
    is written out here rather than inherited by accident: `ConnectorError` passes
    through, everything else is wrapped with the source id, and nothing is swallowed
    (rule 3).
    """

    def fetch(self) -> list[RawNotice]:
        """One render, then one parse. Any failure becomes a ConnectorError."""
        url = self.source.list_url
        selector = self.row_selector()
        try:
            markup = render(url, selector, self.source_id)
            notices = self.parse(markup, url, selector)
        except ConnectorError:
            raise
        except Exception as cause:  # noqa: BLE001 - re-raised with the source id, never swallowed
            raise ConnectorError(self.source_id, cause) from cause

        log.info("browser_fetch", source_id=self.source_id, url=url, selector=selector, fetched=len(notices))
        return notices

    def row_selector(self) -> str:
        """The CSS selector for one notice row, from the source's registry entry.

        In config and not in this file (rule 6): a selector is the single thing about a
        portal connector most likely to change without warning, and changing it should
        be a registry edit with a version hash rather than a deployment.
        """
        selector = (self.source.row_selector or "").strip()
        if not selector:
            raise ValueError(f"{self.source_id}: row_selector is not set in the source's registry entry")
        return selector

    def parse(self, markup: str, url: str, selector: str) -> list[RawNotice]:
        raise NotImplementedError(f"{type(self).__name__} must implement parse")
