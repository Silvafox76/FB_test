"""The reviewer's interface. Six pages, server-rendered, no JavaScript beyond a confirm.

This is the permanent interface for the whole pilot, not a stand-in for a CRM build
in weeks 3 to 5 (D31). There is no later surface that absorbs what is left out here,
which is a reason to build these six pages properly and still a reason not to build
a seventh.

Three properties, all of them deliberate:

  - **It connects as `monitor_review` and as nothing else** (rule 11). `db.connect`
    picks the role from an environment variable, so there is no argument in this file
    that could be computed into the pipeline's role by mistake.
  - **It binds to 127.0.0.1** and there is no authentication, because there is no
    inbound network path to authenticate against (rule 17). The binding is the
    control. A reviewer on the host reaches it directly; anyone else reaches it
    through the SSM tunnel or not at all. Do not change the host in the Makefile
    without changing rule 17 first.
  - **It writes through `review/decisions.py` and `review/export.py` and nowhere
    else** (rule 12). Every query in this file is a select. The four POST handlers
    call `approve`, `reject`, `export` and `re_export`, and none of them touches a
    table itself. The export's POST produces a file on disk and a batch row and the
    re-export's puts a file that has already left back on disk; both send nothing
    anywhere, because there is nowhere to send it (rules 15 to 18).

Every request opens a connection and closes it. That is not a pool and does not need
to be: one reviewer, a page at a time, a few requests a minute.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import psycopg
import structlog
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from monitor import caps, db
from monitor.health.metrics import latest
from monitor.registry.load import load_function_map, load_sources
from monitor.stage.record import build_record, value_narrative
from monitor.stage.stager import regions_config
from review.decisions import (
    DecisionRefused,
    approval_tags,
    approve,
    json_safe,
    load_candidate,
    load_cluster_sources,
    record_defaults,
    rejection_reasons,
    review_config,
)
from review.decisions import reject as reject_candidate
from review.export import ExportRefused, backlog, day_range, export, list_batches, load_batch, re_export

log = structlog.get_logger(__name__)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
# The candidate page's raw-JSON preview runs the proposed payload through Jinja's
# `tojson` filter, and `estimated_value` and `value_rate` reach it as `Decimal`
# (a Postgres numeric), which the stdlib encoder does not know how to write. Same
# fix as `review/decisions.py`'s approval insert, and the same function, so a page
# preview and a stored record cannot disagree about what "JSON-safe" means here.
templates.env.policies["json.dumps_kwargs"] = {"default": json_safe}

app = FastAPI(title="PFM Opportunity Monitor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

REVIEW_ORIGIN_ENV = "MONITOR_REVIEW_ORIGIN"
DEFAULT_REVIEW_ORIGIN = "http://127.0.0.1:8080"


def allowed_origin() -> str:
    """The app's own origin, for `refuse_cross_origin_posts` below (rule 6).

    Read from the environment the same way `review/export.py` reads `MONITOR_EXPORT_DIR`:
    a documented default for the address this app binds today, overridable because a
    deployed host is reached through an SSM port-forwarding tunnel where the local port
    can differ from 8080. Read fresh on every request rather than cached at import time,
    so a test — or an operator's tunnel — can change it without restarting the process.
    """
    return os.environ.get(REVIEW_ORIGIN_ENV) or DEFAULT_REVIEW_ORIGIN


@app.middleware("http")
async def refuse_cross_origin_posts(request: Request, call_next):
    """Closes SR-10. One control: refuse a POST whose `Origin` is neither absent nor ours.

    This app has no CSRF token, no session and no cookie (rules 15-18 rule out the session),
    so nothing distinguishes a form the reviewer is looking at from a form a hostile page
    the reviewer's browser also has open submits to the same loopback address. `Origin` is
    the one signal a browser attaches to a cross-origin POST that the browser itself will
    not let a page forge, so checking it here — once, for every POST, before any handler
    runs — is the whole fix. Rule 1 is why this is `Origin` alone and not `Origin` falling
    back to `Referer`: a second selector for the same decision is a second thing to keep in
    sync and a second thing an attacker only needs one gap in.

    **A missing `Origin` is allowed, deliberately.** The header is absent on a same-origin
    form POST from an older browser and on a request from a non-browser client such as the
    operator's own `curl` or a health check — both indistinguishable from each other and
    both harmless. It is not absent on the request this control exists to stop: the Fetch
    standard requires a browser to attach `Origin` to every cross-origin POST, precisely
    because that is the situation a server cannot otherwise see, and a browser will not omit
    it at a hostile page's request. The threat model here is a browser being steered by a
    page it loaded — not a script that speaks HTTP directly, which is a different path
    (SR-09) with a different remedy (network segmentation, not this middleware). So treating
    "no `Origin`" as "allow" does not open the door this control closes; it declines to
    guard a door this control was never meant to.

    Applies to every POST regardless of path — `/export` and `/export/re-export` produce a
    file leaving the system and are not exempt just because they are not a decision endpoint.
    A rejection is loud on both sides: 403 with a message naming the origin and what it was
    checked against, and a `structlog` warning, because a forged-origin POST is a security
    event whether or not the browser that sent it ever reads the response.
    """
    if request.method == "POST":
        origin = request.headers.get("origin")
        expected = allowed_origin()
        if origin is not None and origin != expected:
            log.warning("cross_origin_post_refused", origin=origin, expected=expected, path=request.url.path)
            return PlainTextResponse(
                f"refused: Origin {origin!r} does not match this app's own origin {expected!r}. "
                "This app has no CSRF token; a cross-origin POST is refused instead of trusted.",
                status_code=403,
            )

    return await call_next(request)


def function_names() -> dict[str, str]:
    """function_id -> the name a reviewer recognises, from config/function_map.yaml."""
    return {function["function_id"]: function["name"] for function in load_function_map()}


# --- decision 71: the dashboard's region names, and nothing else's -------------
#
# `config/thresholds.yaml`'s `regions:` block, country to region name, is read
# through `monitor.stage.stager.regions_config()` alone (rule 23): a region name or
# country list written into this file or a template would be the finding rule 23
# exists to catch. It is the same mapping the stager stamps onto every candidate's
# `region` column; nothing here decides a region a second way.


def region_map() -> dict[str, str]:
    """Country (or the literal `default`) -> region name."""
    return regions_config()


def region_names() -> list[str]:
    """Distinct region names, in the order they first appear in the config.

    Includes the `default` key's own name (last in the file today), because a
    country that reaches it is still a real selection a chip can show a count for.
    """
    seen: list[str] = []
    for name in region_map().values():
        if name not in seen:
            seen.append(name)
    return seen


def region_arrays(mapping: dict[str, str]) -> tuple[list[str], list[str], str]:
    """The mapping as two parallel arrays for `unnest`, plus the default region name.

    Passed into SQL as parameters rather than compiled into the query text, so the
    mapping stays data and the query stays the same query regardless of what config
    holds today (rule 23's "nowhere else" applies to a query string too).
    """
    countries = [country for country in mapping if country != "default"]
    names = [mapping[country] for country in countries]
    return countries, names, mapping["default"]


def source_regions(source, mapping: dict[str, str], default_region: str) -> set[str]:
    """Every region a source reaches, through the one mapping.

    A source that declares `covers` is described entirely by that list - every
    `country: multi` donor source and TED's own `country: EU` - so `covers`, when
    present, is used on its own rather than unioned with `country`. `country` there
    is a routing sentinel, not a place, and mapping it through `regions_config()`
    would resolve to `default_region` (nothing lists `multi` or `EU` as a country)
    and wrongly show a global source as covering only the default region's own
    selection. A source with no `covers` is described by its own `country`, mapped
    through the same fallback as an unlisted country anywhere else.
    """
    countries = set(source.covers) if source.covers else {source.country}
    return {mapping.get(country, default_region) for country in countries}


# Every notice the selection holds, how many passed the free filter, and how many
# are scored. Notices carry no region column, so the mapping travels as parameters
# and is joined on country; an unlisted country falls back to the default region
# the same way `region_for` does for the stager.
FIGURES_NOTICES = """
    with region_map(country, region) as (
        select * from unnest(%s::text[], %s::text[])
    ),
    scoped as (
        select n.status, n.filter_result, coalesce(rm.region, %s) as region
        from notices n
        left join region_map rm on rm.country = n.country
    )
    select
        count(*) filter (where region = coalesce(nullif(%s, ''), region)) as held,
        count(*) filter (where filter_result is not null
                          and status in ('filtered_in', 'scored', 'parked')
                          and region = coalesce(nullif(%s, ''), region)) as passed,
        count(*) filter (where status = 'scored'
                          and region = coalesce(nullif(%s, ''), region)) as scored
    from scoped
"""

# Candidates already carry their region as a column, set at staging from this same
# mapping, so these two read it directly rather than rejoining on country.
FIGURES_CANDIDATES = """
    select
        count(*) filter (where status in ('pending_review', 'approved', 'rejected')) as in_review,
        count(*) filter (where status = 'pending_review') as pending,
        count(*) filter (where status = 'approved') as approved,
        count(*) filter (where status = 'rejected') as rejected
    from candidates
    where region = coalesce(nullif(%s, ''), region)
"""

# `%s::text[]` carries `approval_tags()` (decision 69, `config/review.yaml`), never a
# bare tag name: which strings count as "tagged" is config, not a literal in a query
# (rule 6). Empty-string review_tag (an ordinary approval) is never in that list, so
# an untagged approval is never counted here by accident.
FIGURES_APPROVED = """
    select
        count(*) filter (where ar.review_tag = any(%s::text[])) as tagged,
        count(*) filter (where ar.exported_at is null) as waiting_export
    from approved_records ar
    join candidates c on c.id = ar.candidate_id
    where c.region = coalesce(nullif(%s, ''), c.region)
"""

# Up to ten countries, ordered by candidates then notices; a country with notices
# and no candidate yet (or the reverse) still gets a row through the full outer join.
TOP_COUNTRIES = """
    with region_map(country, region) as (
        select * from unnest(%s::text[], %s::text[])
    ),
    notice_country as (
        select n.country, coalesce(rm.region, %s) as region
        from notices n
        left join region_map rm on rm.country = n.country
    ),
    notices_agg as (
        select country, count(*) as notices
        from notice_country
        where region = coalesce(nullif(%s, ''), region)
        group by country
    ),
    candidates_agg as (
        select country,
               count(*) as candidates,
               count(*) filter (where status = 'approved') as approved
        from candidates
        where region = coalesce(nullif(%s, ''), region)
        group by country
    )
    select coalesce(n.country, c.country) as country,
           coalesce(n.notices, 0) as notices,
           coalesce(c.candidates, 0) as candidates,
           coalesce(c.approved, 0) as approved
    from notices_agg n
    full outer join candidates_agg c on c.country = n.country
    order by candidates desc, notices desc, country
    limit 10
"""

# Every region's own pending count, for the chip row - unfiltered by the current
# selection, because a chip has to show what picking it would mean.
PENDING_BY_REGION = "select region, count(*) from candidates where status = 'pending_review' group by region"

SOURCE_STATS = """
    select s.id, s.name, s.enabled, coalesce(h.state, 'unknown') as state,
           count(distinct n.id) as notices,
           count(distinct c.id) as candidates
    from sources s
    left join source_health h on h.source_id = s.id
    left join notices n on n.source_id = s.id
    left join candidates c on c.primary_notice_id = n.id
    where s.id = any(%s::text[])
    group by s.id, s.name, s.enabled, h.state
    order by s.id
"""

LAST_FETCH = "select max(finished_at) from fetch_runs where source_id = any(%s::text[])"


QUEUE = """
    select c.id, c.score, c.title_en, c.buyer, c.country, c.region, c.language,
           c.deadline_at, c.estimated_value, c.value_currency, c.estimated_value_usd,
           c.value_rate, c.value_rate_date, c.system_names, c.value_note, n.source_id,
           count(cn.notice_id) as notices
    from candidates c
    join notices n on n.id = c.primary_notice_id
    left join candidate_notices cn on cn.candidate_id = c.id
    where c.status = 'pending_review'
    group by c.id, n.source_id
    order by c.score desc, c.id
"""

DECIDED = """
    select c.id, c.status, c.score, c.title_en, c.buyer, c.country, c.reviewer,
           c.rejection_reason, c.updated_at, c.approved_record_id, r.edited, r.exported_at,
           r.review_tag
    from candidates c
    left join approved_records r on r.id = c.approved_record_id
    where c.status in ('approved', 'rejected')
    order by c.updated_at desc
    limit 200
"""

SOURCES = """
    select s.id, s.name, s.country, s.stream, s.wave, s.enabled,
           h.state, h.last_success_at, h.consecutive_failures, h.zero_yield_runs, h.median_items,
           (select count(*) from notices n where n.source_id = s.id)
    from sources s
    left join source_health h on h.source_id = s.id
    order by s.wave, s.id
"""

AUDIT = """
    select entity_type, entity_id, action, actor, before, after, at
    from events
    where (%s = '' or entity_type = %s)
    order by id desc
    limit 300
"""

CANDIDATE = """
    select c.id, c.score, c.status, c.title_en, c.buyer, c.country, c.region, c.language,
           c.admin_level, c.summary_en, c.matched_functions, c.system_names,
           c.procurement_type, c.estimated_value, c.value_currency, c.estimated_value_usd,
           c.value_rate, c.value_rate_date, c.eligibility_flags, c.deadline_at,
           c.reviewer, c.rejection_reason, c.approved_record_id, c.value_note
    from candidates c
    where c.id = %s
"""

CLUSTER = """
    select n.source_id, s.name, n.url, n.title, n.language, n.published_at, n.body,
           cn.match_method, cn.match_score, t.title_en, t.body_en
    from candidate_notices cn
    join notices n on n.id = cn.notice_id
    join sources s on s.id = n.source_id
    left join translations t on t.notice_id = n.id
    where cn.candidate_id = %s
    order by n.published_at nulls last, n.source_id
"""


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, region: str = "") -> HTMLResponse:
    """Decision 71: the hub. The figures, top countries and source coverage for one
    region or for all of them; the queue itself moved to its own tab at `/queue`.

    `region` absent means every region. A region name that is not one of
    `region_names()` is a 404 with a message naming the region, not a silent "all"
    (rule 23 again: this app does not get to invent a region that config never named).
    """
    mapping = region_map()
    names = region_names()
    if region and region not in names:
        return templates.TemplateResponse(
            request,
            "unknown_region.html",
            {"region": region, "regions": names},
            status_code=404,
        )

    countries, mapped_names, default_region = region_arrays(mapping)

    with db.connect("review") as conn:
        held, passed, scored = rows(
            conn, FIGURES_NOTICES, (countries, mapped_names, default_region, region, region, region)
        )[0]
        in_review, pending, approved, rejected = rows(conn, FIGURES_CANDIDATES, (region,))[0]
        tagged_approved, waiting_export = rows(conn, FIGURES_APPROVED, (approval_tags(), region))[0]
        country_rows = rows(conn, TOP_COUNTRIES, (countries, mapped_names, default_region, region, region))
        pending_by_region = dict(rows(conn, PENDING_BY_REGION))

        sources = load_sources()
        selected = [
            source for source in sources if not region or region in source_regions(source, mapping, default_region)
        ]
        source_ids = [source.id for source in selected]

        stats: dict[str, dict] = {}
        last_fetch = None
        if source_ids:
            for source_id, _name, _enabled, state, notices, candidates in rows(conn, SOURCE_STATS, (source_ids,)):
                stats[source_id] = {"state": state, "notices": notices, "candidates": candidates}
            last_fetch = conn.execute(LAST_FETCH, (source_ids,)).fetchone()[0]

        spend = caps.spend_today(conn)

    decided = approved + rejected

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "region": region,
            "chips": [{"name": name, "pending": pending_by_region.get(name, 0)} for name in names],
            "all_pending": sum(pending_by_region.values()),
            "held": held,
            "passed": passed,
            "scored": scored,
            "in_review": in_review,
            "pending": pending,
            "approved": approved,
            "tagged_approved": tagged_approved,
            "rejected": rejected,
            "waiting_export": waiting_export,
            "decided": decided,
            "countries": country_rows,
            "sources": [
                {
                    "id": source.id,
                    "name": source.name,
                    "enabled": source.enabled,
                    "state": stats.get(source.id, {}).get("state", "unknown"),
                    "notices": stats.get(source.id, {}).get("notices", 0),
                    "candidates": stats.get(source.id, {}).get("candidates", 0),
                }
                for source in selected
            ],
            "last_fetch": last_fetch,
            "calls_today": spend.calls,
            "cost_today": spend.usd,
        },
    )


@app.get("/queue", response_class=HTMLResponse)
def queue(request: Request, region: str = "") -> HTMLResponse:
    """The work. Everything pending_review, highest score first, and what is waiting to leave.

    The backlog is on this page because this is the page the reviewer opens: an approved
    record that has not been exported is work they have already done that nobody can act
    on, and nothing notifies them of it (rule 18).
    """
    with db.connect("review") as conn:
        pending = rows(conn, QUEUE)
        waiting = backlog(conn)

    # Appended rather than selected in SQL: one sentence, shared with the candidate
    # page and the export's Pricing Notes through monitor.stage.record.value_narrative,
    # so the queue list, the candidate page and the CSV cannot say three different
    # things about the same candidate's value (rule 1). Never "USD" as a literal here:
    # the currency comes from the row, per column 9. Column 14 is value_note
    # (migration 016): a lot-only BOAMP candidate states no procedure total but did
    # state something, and this reviewer must not read "not stated" for it either.
    sentences = record_defaults()["sentences"]
    pending = [
        row + (value_narrative(row[9], row[8], row[10], row[11], row[12], sentences, note=row[14]),) for row in pending
    ]

    regions = sorted({row[5] for row in pending})
    shown = [row for row in pending if not region or row[5] == region]
    minutes = len(shown) * int(review_config()["minutes_per_candidate"])

    return templates.TemplateResponse(
        request,
        "queue.html",
        {
            "candidates": shown,
            "regions": regions,
            "region": region,
            "total": len(pending),
            "minutes": minutes,
            "backlog": waiting,
        },
    )


@app.get("/candidate/{candidate_id}", response_class=HTMLResponse)
def candidate(request: Request, candidate_id: str, error: str = "") -> HTMLResponse:
    """One candidate, everything the reviewer needs to decide, and the decision form."""
    with db.connect("review") as conn:
        row = conn.execute(CANDIDATE, (candidate_id,)).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "missing.html", {"candidate_id": candidate_id}, status_code=404)
        cluster = rows(conn, CLUSTER, (candidate_id,))

    matched = row[10] if isinstance(row[10], list) else json.loads(row[10])
    names = function_names()
    functions = [
        {
            "function_id": entry["function_id"],
            "name": names.get(entry["function_id"], entry["function_id"]),
            "evidence": entry["evidence"],
        }
        for entry in matched
    ]

    # Columns 13-17 are estimated_value, value_currency, estimated_value_usd, value_rate,
    # value_rate_date; column 23 is value_note (migration 016). One sentence, shared with
    # the queue list and the export's Pricing Notes through monitor.stage.record.value_narrative,
    # so a reviewer reading the candidate page and BD reading the CSV are never told two
    # different things (rule 1). A lot-only candidate states no procedure total but did
    # state something, and value_narrative reads that verbatim in place of "not stated".
    value_text = value_narrative(
        row[14], row[13], row[15], row[16], row[17], record_defaults()["sentences"], note=row[23]
    )

    return templates.TemplateResponse(
        request,
        "candidate.html",
        {
            "c": row,
            "value_text": value_text,
            "functions": functions,
            "cluster": cluster,
            "payload": proposed_payload(candidate_id),
            "reasons": rejection_reasons(),
            "columns": record_defaults()["columns"],
            "error": error,
        },
    )


def proposed_payload(candidate_id: str) -> dict:
    """The record as it would be written if approved right now, for the reviewer to read.

    Built by the same `build_record` the decision uses, through the same loader, so
    the JSON on the page is the JSON that would be stored and not a second rendering
    of it (rule 1). Nothing here writes.
    """
    with db.connect("review") as conn:
        record_candidate, _ = load_candidate(conn, candidate_id)
        sources = load_cluster_sources(conn, candidate_id)

    return build_record(
        record_candidate,
        sources,
        load_function_map(),
        record_defaults(),
        reviewer="(the reviewer who approves)",
        approved_on=datetime.now(UTC).date(),
    )


@app.post("/candidate/{candidate_id}/approve")
async def approve_candidate(request: Request, candidate_id: str) -> RedirectResponse:
    """Approve, with whatever the reviewer changed in the payload form.

    Every appendix E column is posted back, so an edit to any of them is an edit;
    `apply_edits` drops the ones that came back unchanged, which is most of them.
    The form is read directly rather than declared field by field: the field names
    are appendix E's column names and they live in `record_defaults.yaml`, so a
    signature listing them here would be the same spec written twice (rule 6).
    """
    form = await request.form()
    reviewer = str(form.get("reviewer", ""))
    tag = str(form.get("tag", ""))
    edits = {key.removeprefix("field:"): str(value) for key, value in form.items() if key.startswith("field:")}

    with db.connect("review") as conn:
        try:
            record_id = approve(conn, candidate_id, reviewer, edits, tag)
        except DecisionRefused as refused:
            return RedirectResponse(f"/candidate/{candidate_id}?error={refused}", status_code=303)

    log.info("approved_from_form", candidate_id=candidate_id, record_id=record_id)
    return RedirectResponse("/decided", status_code=303)


@app.post("/candidate/{candidate_id}/reject")
def reject_form(
    candidate_id: str,
    reviewer: Annotated[str, Form()] = "",
    reason: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Reject. The reason comes from the fixed list; the note is free text beside it."""
    full = f"{reason}: {note.strip()}" if note.strip() else reason

    with db.connect("review") as conn:
        try:
            reject_candidate(conn, candidate_id, reviewer, full)
        except DecisionRefused as refused:
            return RedirectResponse(f"/candidate/{candidate_id}?error={refused}", status_code=303)

    return RedirectResponse("/decided", status_code=303)


@app.get("/decided", response_class=HTMLResponse)
def decided(request: Request) -> HTMLResponse:
    with db.connect("review") as conn:
        return templates.TemplateResponse(request, "decided.html", {"decided": rows(conn, DECIDED)})


@app.get("/sources", response_class=HTMLResponse)
def sources(request: Request) -> HTMLResponse:
    with db.connect("review") as conn:
        return templates.TemplateResponse(request, "sources.html", {"sources": rows(conn, SOURCES)})


@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request, entity_type: str = "") -> HTMLResponse:
    with db.connect("review") as conn:
        events = rows(conn, AUDIT, (entity_type, entity_type))
    return templates.TemplateResponse(
        request,
        "audit.html",
        {"events": events, "entity_type": entity_type, "entity_types": ["candidate", "approved_record", "source"]},
    )


@app.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request) -> HTMLResponse:
    """The sixth page, and the only one added after step 10: what the week 14 gate will read.

    It renders the newest row the weekly job wrote and computes nothing itself. That is
    rule 5 rather than caution about the cost: measuring is the pipeline's job and
    `monitor/health/metrics.py` does it on a monitor_readonly connection, so a page that
    measured on request would put the pipeline's arithmetic in the review app and give
    two answers to the same question depending on which was looked at last.

    Half the numbers on it have no data behind them yet and say so in place of a figure.
    That is the page working, not the page broken.
    """
    with db.connect("review") as conn:
        run = latest(conn)

    return templates.TemplateResponse(request, "metrics.html", {"run": run})


@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request, batch: str = "", error: str = "", note: str = "") -> HTMLResponse:
    """What is waiting to leave, the form that produces a batch, and every batch produced.

    The range defaults to the backlog's own window — the day of the oldest approved record
    that has not been exported, through today — so the operator can press the button
    without first working out which records are still waiting.

    The listing is here because a batch's file can go missing — deleted, or on a host that
    was replaced — and the manifest's sha256 is how a copy is checked. Each row says whether
    its file is still where the batch row put it, which is what makes re-exporting a
    decision rather than a reflex.
    """
    with db.connect("review") as conn:
        waiting = backlog(conn)
        batches = list_batches(conn)
        produced = load_batch(conn, batch) if batch else None

    today = datetime.now(UTC).date()
    oldest = waiting.oldest
    return templates.TemplateResponse(
        request,
        "export.html",
        {
            "backlog": waiting,
            "batches": batches,
            "range_from": (oldest.approved_at.date() if oldest else today).isoformat(),
            "range_to": today.isoformat(),
            "batch": produced,
            "error": error,
            "note": note,
        },
    )


@app.post("/export")
async def export_form(request: Request) -> RedirectResponse:
    """Produce one batch. It writes a file and a row and it sends nothing anywhere (rules 16 to 18).

    The reconfirmation step (decision 69): each waiting row's checkbox is checked by default,
    so ticking nothing off exports the whole range as before, and unticking one holds it back.
    Read from the raw form rather than a `Form()` parameter for the same reason
    `approve_candidate` does: a repeated checkbox name is a list, which FastAPI's declared
    form parameters do not represent naturally.

    **Untick-all must refuse, not fall back to the whole range.** An unchecked checkbox posts
    nothing at all, so a form with every row unticked and a form with no rows to tick look
    identical to this route: no `record_ids` field either way. `reconfirmed` is the hidden
    field `export.html` renders only when the backlog has rows, so its presence is what tells
    the two apart. `reconfirmed` present and no `record_ids`: the operator saw rows and ticked
    none, so `[]` is passed and `export()` refuses it with "nothing selected", shown on the
    page like any other refusal. `reconfirmed` absent: either nothing was waiting to show a
    checkbox for, or the caller is not this form at all (`make export`, a script, an older
    client), so `None` is passed and behaviour is unchanged.
    """
    form = await request.form()
    range_from = str(form.get("range_from", ""))
    range_to = str(form.get("range_to", ""))
    operator = str(form.get("operator", ""))
    checked = [str(value) for value in form.getlist("record_ids")]
    reconfirmed = "reconfirmed" in form
    record_ids = checked if (checked or reconfirmed) else None

    try:
        first, last = day_range(range_from, range_to)
        batch_id = export(first, last, operator, record_ids)
    except ExportRefused as refused:
        return RedirectResponse(f"/export?error={refused}", status_code=303)

    log.info("export_from_form", batch_id=batch_id, operator=operator)
    return RedirectResponse(f"/export?batch={batch_id}", status_code=303)


@app.post("/export/re-export")
def re_export_form(
    batch: Annotated[str, Form()] = "",
    operator: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Write a named batch's file out again. A separate button because it is a separate act.

    It cannot produce a batch: `re_export` refuses anything but an existing batch id, issues
    no new one and touches no stamp, so pressing this can only ever put back the file that
    already left. The outcome is carried back as a note rather than an error, because
    "the file was already there and its bytes match" is an answer and not a failure.
    """
    try:
        reissued = re_export(batch, operator)
    except ExportRefused as refused:
        return RedirectResponse(f"/export?error={refused}", status_code=303)

    log.info("re_export_from_form", batch_id=reissued.batch_id, operator=operator, rewritten=reissued.rewritten)
    outcome = "written again" if reissued.rewritten else "already on disk, and its bytes match the manifest"
    return RedirectResponse(f"/export?batch={reissued.batch_id}&note={reissued.batch_id} {outcome}", status_code=303)
