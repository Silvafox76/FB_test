"""Apply migrations/*.sql in order, once each.

Connects as the database owner, which is why it is a separate module from
`monitor.db`: no runtime code path can reach an owner connection (rule 11).

Passwords are not in the SQL files (rule 20). 002_roles.sql creates the three
roles with LOGIN and no password; this module sets each one from the environment
after the migrations have applied, and re-sets them on every run so a changed
.env and the database cannot drift apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Role -> the environment variable holding its password.
ROLE_PASSWORD_ENV = {
    "monitor_pipeline": "MONITOR_PIPELINE_PASSWORD",
    "monitor_review": "MONITOR_REVIEW_PASSWORD",
    "monitor_readonly": "MONITOR_READONLY_PASSWORD",
}

SCHEMA_MIGRATIONS = """
create table if not exists schema_migrations (
    filename    text primary key,
    applied_at  timestamptz not null default now()
)
"""


def owner_url() -> str:
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        raise RuntimeError("DATABASE_URL_OWNER is not set; migrations run as the database owner")
    return url


def pending(conn: psycopg.Connection) -> list[Path]:
    """Migration files not yet recorded, in filename order."""
    applied = {row[0] for row in conn.execute("select filename from schema_migrations")}
    return [path for path in sorted(MIGRATIONS_DIR.glob("*.sql")) if path.name not in applied]


def apply(conn: psycopg.Connection, path: Path) -> None:
    """Apply one migration and record it in the same transaction.

    Either the file and its row both land or neither does. A half-applied
    migration is the state that makes the next run unpredictable.
    """
    with conn.transaction():
        conn.execute(path.read_text(encoding="utf-8"))
        conn.execute("insert into schema_migrations (filename) values (%s)", (path.name,))


def sync_role_passwords(conn: psycopg.Connection) -> None:
    for role, env_var in ROLE_PASSWORD_ENV.items():
        password = os.environ.get(env_var)
        if not password:
            raise RuntimeError(f"{env_var} is not set; role {role} would be left without a password")
        conn.execute(sql.SQL("alter role {} with password {}").format(sql.Identifier(role), sql.Literal(password)))


def main() -> int:
    with psycopg.connect(owner_url(), autocommit=True) as conn:
        conn.execute(SCHEMA_MIGRATIONS)

        outstanding = pending(conn)
        if not outstanding:
            print("migrations: nothing to apply")
        for path in outstanding:
            apply(conn, path)
            print(f"migrations: applied {path.name}")

        sync_role_passwords(conn)
        print(f"migrations: passwords set for {', '.join(sorted(ROLE_PASSWORD_ENV))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
