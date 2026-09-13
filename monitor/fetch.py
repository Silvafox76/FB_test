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

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import structlog
import yaml

from monitor.connectors.base import ConnectorError
from monitor.connectors.boamp import BoampConnector
from monitor.connectors.burkina_faso import BurkinaFasoConnector
from monitor.connectors.doe import DoeConnector
from monitor.connectors.ebrd import EbrdConnector
from monitor.connectors.euft import EuftConnector
from monitor.connectors.fts import FtsConnector
from monitor.connectors.ghana import GhanaConnector
from monitor.connectors.liberia import LiberiaConnector
from monitor.connectors.mali import MaliConnector
from monitor.connectors.prozorro import ProzorroConnector
from monitor.connectors.senegal import SenegalConnector
from monitor.connectors.sierra_leone import SierraLeoneConnector
from monitor.connectors.ted import TedConnector
from monitor.connectors.worldbank import WorldBankConnector
from monitor.connectors.worldbank_pipeline import WorldBankPipelineConnector
from monitor.health import source_health
from monitor.models import RawNotice, Source, Translation
from monitor.normalise import boamp as boamp_normalise
from monitor.normalise import burkina_faso as burkina_faso_normalise
from monitor.normalise import doe as doe_normalise
from monitor.normalise import ebrd as ebrd_normalise
from monitor.normalise import euft as euft_normalise
from monitor.normalise import fts as fts_normalise
from monitor.normalise import ghana as ghana_normalise
from monitor.normalise import liberia as liberia_normalise
from monitor.normalise import mali as mali_normalise
from monitor.normalise import ocr
from monitor.normalise import prozorro as prozorro_normalise
from monitor.normalise import senegal as senegal_normalise
from monitor.normalise import sierra_leone as sierra_leone_normalise
from monitor.normalise import ted as ted_normalise
from monitor.normalise import worldbank as worldbank_normalise
from monitor.normalise import worldbank_pipeline as worldbank_pipeline_normalise
from monitor.registry.load import CONFIG_DIR, load_sources
from monitor.schedule import is_due

log = structlog.get_logger(__name__)

REPO = Path(__file__).resolve().parent.parent
STORAGE = REPO / "storage"

# Source id -> the connector that reads it and the mapper that normalises it.
# One entry per source, added by the step that records that source's fixture.
# A source with no entry raises: there is no generic fallback connector (rule 1).
CONNECTORS = {
    "ted": (TedConnector, ted_normalise.map_notice),
    "prozorro": (ProzorroConnector, prozorro_normalise.map_notice),
    "fts": (FtsConnector, fts_normalise.map_notice),
    "worldbank": (WorldBankConnector, worldbank_normalise.map_notice),
    "doe": (DoeConnector, doe_normalise.map_notice),
    "worldbank_pipeline": (WorldBankPipelineConnector, worldbank_pipeline_normalise.map_notice),
    "euft": (EuftConnector, euft_normalise.map_notice),
    "boamp": (BoampConnector, boamp_normalise.map_notice),
    "ebrd": (EbrdConnector, ebrd_normalise.map_notice),
    "sierra_leone": (SierraLeoneConnector, sierra_leone_normalise.map_notice),
    "liberia": (LiberiaConnector, liberia_normalise.map_notice),
    "mali": (MaliConnector, mali_normalise.map_notice),
    "senegal": (SenegalConnector, senegal_normalise.map_notice),
    "ghana": (GhanaConnector, ghana_normalise.map_notice),
    # One RawNotice per bulletin issue, many notices out (decision 51): the one
    # mapper in this table that returns a list.
    "burkina_faso": (BurkinaFasoConnector, burkina_faso_normalise.map_notices),
}

# Built, fixture-tested, and deliberately absent from the table above. Listed here
# because "why is this connector not wired" is a question the table cannot answer,
# and because an absence with no note reads as an oversight:
#
#   simap   tos_status reviewed_restricted. AGB clause 5 needs a named person's
#           signature and no file in this repository is one. Wiring it would let
#           `monitor run` fetch it, so the permission to run is what is withheld;
#           the connector and its 43 contract tests stay.
#   burkina_faso was in this list until 2026-09-13: see decision 51 and the
#           table entry above. The acquire stage decodes by declared mime and a
#           mapper may return a list, which is what a bulletin needed.
#
#
# LIBERIA AND SIERRA LEONE WERE IN THIS LIST UNTIL 2026-09-12, both for the same
# reason and both now wired. Neither was un-built: each had a connector, a recorded
# fixture and a passing contract test, and what was missing was the other half of
# the pair this table wants - a `monitor/normalise/<source>.py` to map the
# `RawNotice` they yield onto a `Notice`. Both mappers were written that day.
#
# Liberia's live fetch failed once on the way in, at 17:53, with a connection reset,
# and was deliberately not retried then: one failure is no more conclusive than the
# one success that had misled the EBRD enable a few hours earlier, and the lesson
# was worth applying in both directions. A single clean attempt at 18:07 returned 14
# notices from 60 page rows, inside its expected band of 3 to 60, so the reset was
# transient. The failure stays on the record here because a host that reset once may
# reset again, and whoever reads the next one should know it has happened before.
#
# These two are the pilot's first West African national sources. Before them the
# 1.0-weight priority geography had no national feed at all: every notice in the
# database came from Europe or a multi-country donor feed.
#
# EBRD WAS IN THIS LIST TWICE ON 2026-09-12 AND IS NOW WIRED, which is worth the
# space because the second entry was wrong for a better reason than the first.
#
# The original note said "no recorded fixture: ecepp.ebrd.com is resetting
# connections". Half had gone stale - the fixtures are recorded and the 56-case
# contract test passes - and a live `fetch_raw` returned 4,050 archive rows and 11
# notices in scope, inside the entry's own expected range of 1 to 30. That was read
# as proof the host was fine, and the source was enabled. The next fetch, four
# minutes later, timed out. One success does not disprove "serves the first pass,
# then resets"; it is what that sentence predicts.
#
# The real blocker was ours. `sources/ebrd.yaml` asks for `schedule: '30 10 * * *'`,
# once a day, and `fetch()` below ignored `schedule` entirely while the scheduler
# wakes hourly - so wiring this source asked a host that refuses a second pass for
# twenty-four passes a day. `monitor/schedule.py` fixed that, and this entry went in
# on the strength of it rather than on the strength of the probe: EBRD is fetched at
# 10:30 UTC and not again until the next day. Checked rather than assumed before
# wiring - its last attempt is the 17:06 timeout, so `is_due` was False for the rest
# of that day and the enable touched the host no further times.
#
# What is still unproven is whether a daily cadence suits this host at all. The
# evidence for it is a successful recording at 08:48, a successful fetch at 17:02 and
# a timeout at 17:06 - consistent with tolerating requests hours apart and refusing
# them minutes apart, which is a reasoned expectation and not a measurement.
# `max_consecutive_failures: 3` is what will say otherwise.
#
# Every other source here is `enabled: false` in its registry entry as well. The test
# `test_every_wired_source_is_enabled_and_every_enabled_source_is_wired` asserts
# those two facts stay in step.

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
    # A source the schedule said was not due. It is a result rather than an absence
    # from the list so that "nothing happened" and "nothing was due" cannot look the
    # same to a caller, which is how an over-eager schedule filter would hide itself.
    skipped: bool = False


def cpv_prefixes() -> list[str]:
    thresholds = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    return thresholds["cpv_pass_prefixes"]


def build_connector(source: Source):
    if source.id not in CONNECTORS:
        raise KeyError(f"no connector for source {source.id!r}; it is added by the step that records its fixture")
    connector_class, mapper = CONNECTORS[source.id]
    return connector_class(source, cpv_prefixes()), mapper


def store_payload(source_id: str, key: str, payload: str | bytes, suffix: str = "json") -> str:
    """Write a raw payload under `key` and return its repository-relative path.

    `key` is the notice's content hash when a payload is one notice, which is every
    JSON source and was the only case until 2026-09-13; it is the payload's own
    hash when one payload holds many notices, a PDF bulletin, so the file is
    written once and every notice it yields points at it. `suffix` follows the
    mime, so a stored bulletin is a readable `.pdf` and not JSON-named bytes.
    """
    directory = STORAGE / source_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.{suffix}"
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    else:
        path.write_text(payload, encoding="utf-8")
    return str(path.relative_to(REPO))


# What a connector's payload is, by the mime it declared, and how it becomes the
# document a mapper reads. Dispatch on a declared field, the way monitor/fx/convert.py
# dispatches on a currency's kind: one correct path per mime and no second attempt
# (rule 1). A mime with no decoder raises, because a connector declaring one is a
# change in the source and not something to read around (rule 4).
#
# Each decoder returns the document and, for a payload that is filed once for many
# notices, the path it was filed at; None means "file it per notice at insert".


def _decode_json(source: Source, raw: RawNotice) -> tuple[object, str | None]:
    return json.loads(raw.payload), None


def _decode_pdf(source: Source, raw: RawNotice) -> tuple[object, str | None]:
    # A bulletin is one payload holding thirty-odd dossier notices (decision 51). It
    # is filed once under its own hash, before it is read, because `ocr.extract`
    # runs pdftotext against a path; the text-layer-or-Textract choice is that
    # module's and is not repeated here.
    # The mapper gets the issue's own URL beside its text: a bulletin notice has
    # no page of its own, so every notice from an issue opens the issue, the way
    # every Mali notice opens the shared listing.
    data = base64.b64decode(raw.payload)
    stored = store_payload(source.id, hashlib.sha256(data).hexdigest(), data, suffix="pdf")
    return {"url": raw.url, "text": ocr.extract(REPO / stored).text}, stored


DECODERS = {
    "application/json": _decode_json,
    "application/pdf": _decode_pdf,
}


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

    # `seen` counts NOTICES mapped, not payloads fetched. For every JSON source the
    # two are the same number, as they always were; for a bulletin source one
    # payload maps to its dossier notices, and that is the count the registry's
    # expected_items_per_run describes (decision 51).
    seen = new = 0
    try:
        raw_notices = connector.fetch()
        for raw in raw_notices:
            mapped, inserted = _store_notices(conn, source, raw, mapper)
            seen += mapped
            new += inserted
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


def _reference(document: object, url: str) -> str:
    """What to name in a NormaliseError: the notice's own number where it has one."""
    if isinstance(document, dict):
        return str(document.get("publication-number", url))
    return url


def _store_notices(conn, source: Source, raw: RawNotice, mapper) -> tuple[int, int]:
    """Decode one payload, map it to its notices, insert the new ones.

    Returns (notices mapped, notices new). A JSON row maps to one notice; a bulletin
    maps to every dossier notice inside it. Which it is, the mapper says by what it
    returns - one `MappedNotice` or a list - and nothing here infers it from the
    mime. A mapper that returns an empty list from a payload that decoded is a
    zero-yield on a source that normally yields, which rule 4 treats as a failure.
    """
    if raw.mime not in DECODERS:
        raise NormaliseError(source.id, raw.url, ValueError(f"no decoder for mime {raw.mime!r}"))
    document, stored = DECODERS[raw.mime](source, raw)

    try:
        mapped = mapper(document)
    except Exception as cause:  # noqa: BLE001 - re-raised with the source and notice attached
        raise NormaliseError(source.id, _reference(document, raw.url), cause) from cause

    items = mapped if isinstance(mapped, list) else [mapped]
    if not items:
        raise NormaliseError(source.id, raw.url, ValueError("mapper returned no notices from a payload that decoded"))

    new = 0
    for item in items:
        if _insert_notice(conn, source, item, raw, stored):
            new += 1
    return len(items), new


def _insert_notice(conn, source: Source, mapped, raw: RawNotice, stored: str | None) -> bool:
    """Insert one mapped notice if its content hash is new. Returns whether it was new."""
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

    # A one-notice payload is filed under the notice's hash at this point, as it
    # always was; a many-notice payload was filed once by its decoder.
    storage_path = stored or store_payload(source.id, notice.content_hash, raw.payload)
    url, mime = raw.url, raw.mime
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
                             cpv_codes, estimated_value, value_currency, value_note, body, status)
        values (%(content_hash)s, %(source_id)s, %(external_id)s, %(url)s, %(title)s, %(buyer)s,
                %(country)s, %(admin_level)s, %(published_at)s, %(deadline_at)s, %(language)s,
                %(language_confidence)s, %(cpv_codes)s, %(estimated_value)s, %(value_currency)s,
                %(value_note)s, %(body)s, %(status)s)
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


# The newest attempt per source, successful or not. Not `last_success_at` from
# source_health: a source that failed at 10:30 has spent its polite pass for the day,
# and coming back at 11:30 is a retry (rule 2) aimed at a host that just refused us.
LAST_ATTEMPT = "select source_id, max(started_at) from fetch_runs group by source_id"


def last_attempts(conn: psycopg.Connection) -> dict[str, datetime]:
    return dict(conn.execute(LAST_ATTEMPT).fetchall())


def fetch(conn: psycopg.Connection, source_id: str, *, now: datetime | None = None) -> list[FetchResult]:
    """`monitor fetch <id>` or `monitor fetch all`.

    `all` reads only the sources whose schedule says they are due, which is what makes
    an hourly wake correct: every wake asks each source whether it has been fetched
    since its schedule last fired, and only the ones that have not are read. Before
    this, `all` read every enabled source on every wake, so a source asking for
    `30 10 * * *` was fetched twenty-four times a day (decision 49).

    NAMING A SOURCE BYPASSES THE SCHEDULE, deliberately. `make fetch S=ted` is a person
    asking for this source now - to record a fixture, to check a connector after a
    change, to see whether a portal is back - and a command that answered "not due"
    would be obeying a cron expression written for unattended running against someone
    who is standing there. The politeness rule is about the scheduled cadence, and a
    person taking one pass by hand is inside it.

    `now` is a parameter so the tests can ask the question at a chosen moment rather
    than at whatever time they happen to run.
    """
    sources = {source.id: source for source in load_sources()}

    if source_id != "all":
        if source_id not in sources:
            raise KeyError(f"no source {source_id!r} in the registry")
        return [fetch_source(conn, sources[source_id])]

    enabled = [source for source in sources.values() if source.enabled]
    if not enabled:
        raise RuntimeError("no source is enabled; a source is enabled by the step that proves it parses")

    moment = now or datetime.now(UTC)
    attempts = last_attempts(conn)

    results: list[FetchResult] = []
    for source in enabled:
        if not is_due(source.schedule, attempts.get(source.id), moment):
            log.info(
                "fetch_not_due",
                source_id=source.id,
                schedule=source.schedule,
                last_attempt=attempts[source.id].isoformat(),
            )
            results.append(FetchResult(source_id=source.id, seen=0, new=0, failed=False, skipped=True))
            continue
        results.append(fetch_source(conn, source))
    return results
