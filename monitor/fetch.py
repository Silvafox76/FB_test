"""The acquire stage: run one connector, store what is new, record what happened.

Sits between the connectors (which only fetch) and the normalisers (which only
map), because neither of them should know about the database and something has to
(rule 5). One job: for each raw notice, map it, and insert it if its content hash
is not already stored.

Change detection is the content hash and nothing else. A second run over an
unchanged source inserts nothing and reports zero new, which is what makes a daily
pass cheap and what `make fetch S=ted` twice proves.

Two deliberate exceptions to "failures raise" (rule 3) live here, both narrow:

  - `NormaliseError` wraps whatever a mapper raises with the source and the notice
    it was reading, the same shape and for the same reason as `ConnectorError`.
    The mappers are built to raise: an unknown country code or an unrecognised
    buyer type is a real change in the source, and it has to become that source's
    failure rather than a traceback.
  - `fetch_source` turns both into a `FetchResult(failed=True)` instead of
    re-raising, because one broken source must mark itself unhealthy without
    taking the others down with it. That isolation is what step 11's second drill
    tests. Nothing is swallowed: the run is recorded failed with its error, health
    is updated, the failure is logged, and the CLI exits non-zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import structlog
import yaml

from monitor.connectors.base import ConnectorError
from monitor.connectors.ted import TedConnector
from monitor.health import source_health
from monitor.models import Source, Translation
from monitor.normalise import ted as ted_normalise
from monitor.registry.load import CONFIG_DIR, load_sources

log = structlog.get_logger(__name__)

REPO = Path(__file__).resolve().parent.parent
STORAGE = REPO / "storage"

# Source id -> the connector that reads it and the mapper that normalises it.
# One entry per source, added by the step that records that source's fixture.
# A source with no entry raises: there is no generic fallback connector (rule 1).
CONNECTORS = {
    "ted": (TedConnector, ted_normalise.map_notice),
}

# The provenance stamped on an English rendering the source itself supplied, as
# opposed to one a model produced. TED translates into all 24 EU languages, so its
# English arrives with the notice and costs nothing.
SOURCE_NATIVE_MODEL = "ted-eforms"
SOURCE_NATIVE_PROMPT_VERSION = "source-native"


class NormaliseError(Exception):
    """A mapper could not read a notice. Carries the source and the notice."""

    def __init__(self, source_id: str, reference: str, cause: BaseException) -> None:
        super().__init__(f"{source_id}: notice {reference}: {type(cause).__name__}: {cause}")
        self.source_id = source_id
        self.reference = reference
        self.cause = cause


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    seen: int
    new: int
    failed: bool
    error: str = ""


def cpv_prefixes() -> list[str]:
    thresholds = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    return thresholds["cpv_pass_prefixes"]


def build_connector(source: Source):
    if source.id not in CONNECTORS:
        raise KeyError(f"no connector for source {source.id!r}; it is added by the step that records its fixture")
    connector_class, mapper = CONNECTORS[source.id]
    return connector_class(source, cpv_prefixes()), mapper


def store_payload(source_id: str, content_hash: str, payload: str) -> str:
    """Write the raw payload and return its repository-relative path."""
    directory = STORAGE / source_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{content_hash}.json"
    path.write_text(payload, encoding="utf-8")
    return str(path.relative_to(REPO))


def fetch_source(conn: psycopg.Connection, source: Source) -> FetchResult:
    """One pass over one source. Always writes a fetch_runs row and health."""
    connector, mapper = build_connector(source)
    started = datetime.now(UTC)
    run_id = conn.execute(
        "insert into fetch_runs (source_id, started_at, status) values (%s, %s, 'running') returning id",
        (source.id, started),
    ).fetchone()[0]
    conn.commit()
    bound = log.bind(source_id=source.id, run_id=str(run_id))

    seen = new = 0
    try:
        raw_notices = connector.fetch()
        for raw in raw_notices:
            seen += 1
            if _store_notice(conn, source, raw.payload, mapper, raw.url, raw.mime):
                new += 1
    except (ConnectorError, NormaliseError) as error:
        # The run is recorded failed and the source goes unhealthy. Rolled back
        # first so a half-stored page does not survive as though it were complete.
        conn.rollback()
        conn.execute(
            "update fetch_runs set finished_at = now(), status = 'failed', items_seen = %s, error = %s where id = %s",
            (seen, str(error), run_id),
        )
        _update_health(conn, source, started, failed=True, seen=seen, new=0)
        conn.commit()
        bound.error("fetch_failed", error=str(error))
        return FetchResult(source.id, seen=seen, new=0, failed=True, error=str(error))

    conn.execute(
        """
        update fetch_runs set finished_at = now(), status = 'ok', items_seen = %s, items_new = %s
        where id = %s
        """,
        (seen, new, run_id),
    )
    _update_health(conn, source, started, failed=False, seen=seen, new=new)
    conn.commit()
    bound.info("fetch_ok", seen=seen, new=new)
    return FetchResult(source.id, seen=seen, new=new, failed=False)


def _store_notice(conn, source: Source, payload: str, mapper, url: str, mime: str) -> bool:
    """Insert one notice if its content hash is new. Returns whether it was new."""
    document = json.loads(payload)
    try:
        mapped = mapper(document)
    except Exception as cause:  # noqa: BLE001 - re-raised with the source and notice attached
        raise NormaliseError(source.id, str(document.get("publication-number", url)), cause) from cause
    notice = mapped.notice

    # Scoped to the source (migration 007). The hash is still the change-detection
    # key, but two sources publishing the same tender each keep their own notice so
    # the deduper can join them into one candidate and the export can name both.
    existing = conn.execute(
        "select 1 from notices_raw where source_id = %s and content_hash = %s",
        (source.id, notice.content_hash),
    ).fetchone()
    if existing:
        return False

    storage_path = store_payload(source.id, notice.content_hash, payload)
    conn.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, %s, %s, %s)
        """,
        (notice.content_hash, source.id, url, storage_path, mime),
    )
    notice_id = conn.execute(
        """
        insert into notices (content_hash, source_id, external_id, url, title, buyer, country,
                             admin_level, published_at, deadline_at, language, language_confidence,
                             cpv_codes, estimated_value_usd, body, status)
        values (%(content_hash)s, %(source_id)s, %(external_id)s, %(url)s, %(title)s, %(buyer)s,
                %(country)s, %(admin_level)s, %(published_at)s, %(deadline_at)s, %(language)s,
                %(language_confidence)s, %(cpv_codes)s, %(estimated_value_usd)s, %(body)s, %(status)s)
        returning id
        """,
        notice.model_dump(exclude={"filter_result"}),
    ).fetchone()[0]

    # TED translates every notice into all 24 EU languages itself, so the English
    # rendering arrives with the notice and costs nothing. It is stored as what it
    # is: a derived field with its provenance stamped (rule 9), which is why the
    # step 14 translation stage never has to run for this source. The original
    # title and body are untouched.
    if mapped.title_en and mapped.title_en != notice.title:
        translation = Translation(
            notice_id=str(notice_id),
            title_en=mapped.title_en,
            body_en=mapped.body_en,
            model=SOURCE_NATIVE_MODEL,
            prompt_version=SOURCE_NATIVE_PROMPT_VERSION,
            latency_ms=0,
            cost_usd=0.0,
        )
        conn.execute(
            """
            insert into translations (notice_id, title_en, body_en, model, prompt_version,
                                      latency_ms, cost_usd)
            values (%(notice_id)s, %(title_en)s, %(body_en)s, %(model)s, %(prompt_version)s,
                    %(latency_ms)s, %(cost_usd)s)
            on conflict (notice_id, prompt_version) do nothing
            """,
            translation.model_dump(),
        )
    return True


def _update_health(conn, source: Source, at: datetime, *, failed: bool, seen: int, new: int) -> None:
    outcome = source_health.RunOutcome(at=at, failed=failed, items_seen=seen, items_new=new)
    current = source_health.read(conn, source.id)
    source_health.write(
        conn,
        source_health.next_health(current, outcome, source.expected_min, source.max_consecutive_failures),
    )


def fetch(conn: psycopg.Connection, source_id: str) -> list[FetchResult]:
    """`monitor fetch <id>` or `monitor fetch all`."""
    sources = {source.id: source for source in load_sources()}

    if source_id == "all":
        chosen = [source for source in sources.values() if source.enabled]
        if not chosen:
            raise RuntimeError("no source is enabled; a source is enabled by the step that proves it parses")
    else:
        if source_id not in sources:
            raise KeyError(f"no source {source_id!r} in the registry")
        chosen = [sources[source_id]]

    return [fetch_source(conn, source) for source in chosen]
