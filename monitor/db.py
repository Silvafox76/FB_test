"""The only way to get a database connection at runtime.

The role is chosen by which environment variable is read, never by a code path
and never by an argument that could be computed (rule 11). There are two runtime
roles and this module offers exactly those two. Reporting connects as
`monitor_readonly` through psql or the ops-analyst agent, not through here, and
migrations connect as the owner through `monitor/migrate.py`, which is not
runtime.
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
