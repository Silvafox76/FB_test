"""The only way for the pipeline or the review app to get a connection at runtime.

The role is chosen by which environment variable is read, never by a code path
and never by an argument that could be computed (rule 11). There are two runtime
roles that do work — `pipeline` and `review` — and this module offers exactly
those two and no more. Migrations connect as the owner through
`monitor/migrate.py`, which is not runtime.

**Reporting is the exception and it is now an automated one.** This docstring used
to say reporting reached `monitor_readonly` through psql or the ops-analyst agent
and "not through here", and that stopped being true when `monitor status` grew an
export-backlog line: the backlog lives in `approved_records`, which
`migrations/002_roles.sql` revokes from `monitor_pipeline`, and connecting the
pipeline as `monitor_review` to read it is rule 11's blocking case. So
`review/export.py`'s `reporting_connection()` opens a `monitor_readonly`
connection directly, and `monitor/cli.py` calls it.

That is correct use of the role rule 11 carves out for reporting, and it is not a
second path to either runtime role — nothing reads or writes as `pipeline` or
`review` except through here. But it is a second `psycopg.connect` in the
codebase, so the claim of exclusivity above is narrowed rather than restated. If
reporting grows a third caller, the right move is to add `readonly` as a `Role`
here and have `reporting_connection()` become `db.connect("readonly")`, which is
what its own docstring already anticipates.
"""

from __future__ import annotations

import os
from typing import Literal

import psycopg

Role = Literal["pipeline", "review"]

ENV_VAR_FOR_ROLE: dict[Role, str] = {
    "pipeline": "DATABASE_URL_PIPELINE",
    "review": "DATABASE_URL_REVIEW",
}


def connect(role: Role) -> psycopg.Connection:
    """Open a connection as one of the two runtime roles.

    Raises rather than falling back to a default URL: a pipeline that quietly
    connects as the reviewer is the one failure this design exists to prevent.
    """
    if role not in ENV_VAR_FOR_ROLE:
        raise ValueError(f"unknown role {role!r}: the runtime roles are pipeline and review")

    env_var = ENV_VAR_FOR_ROLE[role]
    url = os.environ.get(env_var)
    if not url:
        raise RuntimeError(f"{env_var} is not set; copy .env.example to .env and fill it in")

    return psycopg.connect(url)
