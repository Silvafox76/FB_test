"""The reviewer's interface. Five pages, server-rendered, no JavaScript beyond a confirm.

This is the permanent interface for the whole pilot, not a stand-in for a CRM build
in weeks 3 to 5 (D31). There is no later surface that absorbs what is left out here,
which is a reason to build these five pages properly and still a reason not to build
a sixth.

Three properties, all of them deliberate:

  - **It connects as `monitor_review` and as nothing else** (rule 11). `db.connect`
    picks the role from an environment variable, so there is no argument in this file
    that could be computed into the pipeline's role by mistake.
  - **It binds to 127.0.0.1** and there is no authentication, because there is no
    inbound network path to authenticate against (rule 17). The binding is the
    control. A reviewer on the host reaches it directly; anyone else reaches it
    through the SSM tunnel or not at all. Do not change the host in the Makefile
    without changing rule 17 first.
  - **It writes through `review/decisions.py` and nowhere else** (rule 12). Every
    query in this file is a select. The two POST handlers call `approve` and
    `reject`, and neither one touches a table itself.

Every request opens a connection and closes it. That is not a pool and does not need
to be: one reviewer, a page at a time, a few requests a minute.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import psycopg
import structlog
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from monitor import db
from monitor.registry.load import load_function_map
from monitor.stage.record import build_record
from review.decisions import (
    DecisionRefused,
    approve,
    load_candidate,
    load_cluster_sources,
    record_defaults,
    rejection_reasons,
    review_config,
)
from review.decisions import reject as reject_candidate

log = structlog.get_logger(__name__)

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

app = FastAPI(title="PFM Opportunity Monitor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def function_names() -> dict[str, str]:
    """function_id -> the name a reviewer recognises, from config/function_map.yaml."""
    return {function["function_id"]: function["name"] for function in load_function_map()}


QUEUE = """
    select c.id, c.score, c.title_en, c.buyer, c.country, c.region, c.language,
           c.deadline_at, c.estimated_value_usd, c.system_names, n.source_id,
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
           c.rejection_reason, c.updated_at, c.approved_record_id, r.edited, r.exported_at
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
           c.procurement_type, c.estimated_value_usd, c.eligibility_flags, c.deadline_at,
           c.reviewer, c.rejection_reason, c.approved_record_id
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
def queue(request: Request, region: str = "") -> HTMLResponse:
    """The work. Everything pending_review, highest score first."""
    with db.connect("review") as conn:
        pending = rows(conn, QUEUE)

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

    return templates.TemplateResponse(
        request,
        "candidate.html",
        {
            "c": row,
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
    edits = {key.removeprefix("field:"): str(value) for key, value in form.items() if key.startswith("field:")}

    with db.connect("review") as conn:
        try:
            record_id = approve(conn, candidate_id, reviewer, edits)
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
