#!/usr/bin/env bash
# Stand up the pilot host, exactly as deploy/README.md and RUNBOOK.md describe it,
# and prove it with one pass before trusting the clock.
#
#   sudo deploy/host_setup.sh --repo <git-url> [--branch <name>] [--check]
#
# Every step checks before it acts and the script can be run again after any
# failure; a step already done is reported "ok" and skipped. Nothing here contains
# or prints a secret (rule 20): the environment file is created empty, 0600, and the
# operator fills it in; the script refuses to go past that step while any value
# still reads CHANGE_ME. Nothing here opens a port or installs anything that
# listens (rule 17): the timers run commands, and the review app is started by a
# person with `make review` on 127.0.0.1.
#
# Conventions, from deploy/README.md: checkout /opt/monitor, service account
# `monitor`, environment file /etc/monitor/monitor.env, logs /var/log/monitor/,
# Postgres native on localhost, uv at /usr/local/bin/uv.
#
# Two things here go beyond the README's install block, both named so they are
# not a surprise: monitor-fx.timer is enabled with the other three, because staging
# refuses to run on rates older than config/fx.yaml allows; and a logrotate snippet
# is written, closing the RUNBOOK's "no log rotation ships with the timers" gap.
#
# --check does the preflight and prints what each step would do, changing nothing.
# Ubuntu 24.04 with systemd is what this was written against; the preflight says
# so rather than guessing on anything else.

set -euo pipefail

REPO=""
BRANCH="main"
CHECK=0
CHECKOUT=/opt/monitor
ENV_DIR=/etc/monitor
ENV_FILE=/etc/monitor/monitor.env
SERVICE_USER=monitor
UV=/usr/local/bin/uv
PG_VERSION=16
DB_NAME=monitor
OWNER_ROLE=monitor_owner
TIMERS=(monitor-run.timer monitor-status.timer monitor-fx.timer monitor-metrics.timer)

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --check) CHECK=1; shift ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok    %s\n' "$*"; }
do_()  { printf '   do    %s\n' "$*"; }
stop() { printf '   STOP  %s\n' "$*" >&2; exit 1; }

run() {
    # Print the command; run it unless --check.
    do_ "$*"
    if [ "$CHECK" -eq 0 ]; then "$@"; fi
}

env_pairs() {
    # KEY=VALUE pairs from the env file, read the way systemd's EnvironmentFile
    # reads it and NOT the way a shell would: no expansion, no substitution, no
    # execution, whole-line comments and blank lines skipped, a double-quoted value
    # unwrapped. Sourcing the file with `.` would expand a `$` or a backtick inside
    # a password (rule 20) and would disagree with the units that read the same
    # file literally (rule 1: one parser for one file).
    local line key value
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in ''|\#*) continue ;; esac
        key="${line%%=*}"; value="${line#*=}"
        case "$value" in \"*\") value="${value#\"}"; value="${value%\"}" ;; esac
        printf '%s=%s\0' "$key" "$value"
    done < "$ENV_FILE"
}

as_monitor() {
    # Run a command as the service user, in the checkout, with the env file loaded.
    do_ "(as $SERVICE_USER, env loaded) $*"
    if [ "$CHECK" -eq 0 ]; then
        local -a pairs=()
        while IFS= read -r -d '' pair; do pairs+=("$pair"); done < <(env_pairs)
        sudo -u "$SERVICE_USER" env -i HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)" \
            PATH=/usr/local/bin:/usr/bin:/bin PYTHONUNBUFFERED=1 "${pairs[@]}" \
            bash -c "cd '$CHECKOUT' && exec \"\$@\"" _ "$@"
    fi
}

env_value() {
    # One value from the env file, quotes stripped, empty if absent.
    sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -n1 | sed 's/^"//; s/"$//'
}

# ---------------------------------------------------------------- preflight ---
say "preflight"
[ "$(id -u)" -eq 0 ] || stop "run with sudo: the script installs packages, a service account and systemd units"
[ -d /run/systemd/system ] || stop "no systemd on this host; deploy/README.md's Compose loops are the alternative"
if [ -r /etc/os-release ]; then
    . /etc/os-release
    case "${ID:-}:${VERSION_ID:-}" in
        ubuntu:24.04) ok "Ubuntu 24.04" ;;
        *) printf '   warn  %s\n' "written against Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}. Package names may differ." ;;
    esac
fi
if [ -z "$REPO" ] && [ ! -d "$CHECKOUT/.git" ]; then
    stop "--repo <git-url> is required the first time; after that the existing checkout at $CHECKOUT is updated"
fi
[ "$CHECK" -eq 1 ] && ok "--check: nothing below changes anything"

# ---------------------------------------------------------------- packages ---
say "packages: postgresql-$PG_VERSION, pgvector, poppler-utils (pdftotext, pdfinfo for the Burkina Faso bulletin), git, curl"
NEEDED=()
for pkg in "postgresql-$PG_VERSION" "postgresql-$PG_VERSION-pgvector" poppler-utils git curl ca-certificates; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then ok "$pkg"; else NEEDED+=("$pkg"); fi
done
if [ ${#NEEDED[@]} -gt 0 ]; then
    run apt-get update -q
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${NEEDED[@]}"
fi

# ---------------------------------------------------------- service account ---
say "service account $SERVICE_USER and checkout $CHECKOUT"
if getent passwd "$SERVICE_USER" >/dev/null; then ok "user $SERVICE_USER exists"; else
    run useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if [ -d "$CHECKOUT/.git" ]; then
    if [ -n "$(sudo -u "$SERVICE_USER" git -C "$CHECKOUT" status --porcelain)" ]; then
        stop "$CHECKOUT has local changes; a host checkout is never edited in place. Commit or discard them first."
    fi
    ok "checkout exists and is clean; updating to origin/$BRANCH"
    run sudo -u "$SERVICE_USER" git -C "$CHECKOUT" fetch -q origin "$BRANCH"
    run sudo -u "$SERVICE_USER" git -C "$CHECKOUT" checkout -q "$BRANCH"
    run sudo -u "$SERVICE_USER" git -C "$CHECKOUT" merge -q --ff-only "origin/$BRANCH"
else
    run install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$CHECKOUT"
    run sudo -u "$SERVICE_USER" git clone -q --branch "$BRANCH" "$REPO" "$CHECKOUT"
fi

# ---------------------------------------------------------------------- uv ---
say "uv at $UV (the units' ExecStart is an absolute path)"
if [ -x "$UV" ]; then ok "$($UV --version)"; else
    run sh -c "curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh"
fi

# --------------------------------------------------------- environment file ---
say "environment file $ENV_FILE (0600, owned by $SERVICE_USER, never in the repository)"
if [ -f "$ENV_FILE" ]; then ok "exists"; else
    run install -d -m 0750 -o root -g "$SERVICE_USER" "$ENV_DIR"
    if [ "$CHECK" -eq 0 ]; then
        # The template with every key, hosts set to localhost for native Postgres.
        # Values stay CHANGE_ME: the operator fills them in; this script never does.
        install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_USER" /dev/null "$ENV_FILE"
        grep -v '^\s*$' "$CHECKOUT/.env.example" | sed 's/@postgres:/@localhost:/' > "$ENV_FILE"
    fi
    do_ "wrote the template from .env.example with CHANGE_ME values"
fi
if [ "$CHECK" -eq 0 ]; then
    if grep -q 'CHANGE_ME\|CONTACT_EMAIL_HERE' "$ENV_FILE"; then
        printf '\n'
        printf '   The environment file still has placeholder values. Fill in every CHANGE_ME and\n'
        printf '   CONTACT_EMAIL_HERE in %s (sudo -e), keeping the same value in POSTGRES_PASSWORD\n' "$ENV_FILE"
        printf '   and in DATABASE_URL_OWNER, then run this script again. It resumes here.\n'
        printf '   Rule 20: no value in that file is ever printed, logged or committed by anything.\n'
        exit 3
    fi
    [ "$(stat -c '%a %U' "$ENV_FILE")" = "600 $SERVICE_USER" ] || stop "$ENV_FILE must be mode 0600 owned by $SERVICE_USER"
    ok "no placeholders left; permissions 0600 $SERVICE_USER"
    OWNER_PASSWORD="$(env_value POSTGRES_PASSWORD)"
    [ -n "$OWNER_PASSWORD" ] || stop "POSTGRES_PASSWORD is empty in $ENV_FILE"
    case "$(env_value DATABASE_URL_OWNER)" in
        *@localhost:*|*@127.0.0.1:*) ok "database URLs point at localhost" ;;
        *) stop "DATABASE_URL_OWNER does not point at localhost; this script sets up native Postgres on this host" ;;
    esac
fi

# ------------------------------------------------------------------ postgres ---
say "postgres $PG_VERSION: cluster, owner role $OWNER_ROLE (LOGIN CREATEROLE), database $DB_NAME, extension vector"
run systemctl enable --now postgresql
if [ "$CHECK" -eq 0 ]; then
    pg_isready -q -h localhost -p 5432 || stop "postgres is not accepting connections on localhost:5432"
    psql_su() { sudo -u postgres psql -v ON_ERROR_STOP=1 -qAtX "$@"; }
    case "$(psql_su -c 'show listen_addresses')" in
        localhost|127.0.0.1|"localhost, ::1"|::1) ok "postgres listens on loopback only (rule 17)" ;;
        *) stop "postgres listen_addresses is '$(psql_su -c 'show listen_addresses')'; this host serves nothing off loopback (rule 17)" ;;
    esac
    if [ "$(psql_su -c "select 1 from pg_roles where rolname = '$OWNER_ROLE'")" = "1" ]; then
        ok "role $OWNER_ROLE exists (password left as it is; change it with ALTER ROLE if the env file changed)"
    else
        do_ "create role $OWNER_ROLE login createrole (password from the env file, passed on stdin, never on a command line)"
        psql_su -c "create role \"$OWNER_ROLE\" login createrole" >/dev/null
        printf "alter role \"%s\" with password '%s';\n" "$OWNER_ROLE" "${OWNER_PASSWORD//\'/\'\'}" | psql_su >/dev/null
    fi
    if [ "$(psql_su -c "select 1 from pg_database where datname = '$DB_NAME'")" = "1" ]; then ok "database $DB_NAME exists"; else
        do_ "create database $DB_NAME owner $OWNER_ROLE"
        psql_su -c "create database \"$DB_NAME\" owner \"$OWNER_ROLE\"" >/dev/null
    fi
    # pgvector needs a superuser to create; unused this phase (compose does the same).
    psql_su -d "$DB_NAME" -c "create extension if not exists vector" >/dev/null && ok "extension vector"
    unset OWNER_PASSWORD
else
    do_ "create role $OWNER_ROLE, database $DB_NAME, extension vector (idempotent)"
fi

# ------------------------------------------------------- migrate and seed ---
say "migrate and seed, as $SERVICE_USER with the env file loaded (make migrate && make seed)"
as_monitor "$UV" sync --frozen
as_monitor "$UV" run python -m monitor.migrate
as_monitor "$UV" run python -m monitor.registry

# --------------------------------------------------------------- log rotate ---
say "log rotation for /var/log/monitor (RUNBOOK known gap: none shipped with the timers)"
if [ -f /etc/logrotate.d/monitor ]; then ok "exists"; else
    do_ "write /etc/logrotate.d/monitor: weekly, 8 kept, compressed, copytruncate"
    if [ "$CHECK" -eq 0 ]; then
        cat > /etc/logrotate.d/monitor <<'EOF'
/var/log/monitor/*.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
    fi
fi

# --------------------------------------------------------------------- units ---
say "systemd units: install, reload, enable the TIMERS (never the services)"
run install -m 0644 "$CHECKOUT"/deploy/systemd/monitor-*.service /etc/systemd/system/
run install -m 0644 "$CHECKOUT"/deploy/systemd/monitor-*.timer /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable --now "${TIMERS[@]}"

# --------------------------------------------------------------------- prove ---
say "prove it: one run of each service by hand, in the order deploy/README.md gives"
prove() {
    local unit="$1"
    do_ "systemctl start $unit"
    if [ "$CHECK" -eq 0 ]; then
        if systemctl start "$unit"; then ok "$unit exited 0"; else
            printf '   FAIL  %s; last lines of its log:\n' "$unit" >&2
            log="${unit#monitor-}"; log="/var/log/monitor/${log%.service}.log"
            tail -n 20 "$log" 2>/dev/null | sed 's/^/         /' >&2 || true
            journalctl -u "$unit" -n 5 --no-pager 2>/dev/null | sed 's/^/         /' >&2 || true
            exit 1
        fi
    fi
}
prove monitor-status.service    # fast, read-only, proves the env file
prove monitor-fx.service        # the day's rates; staging refuses to run on stale ones
prove monitor-metrics.service   # the one unit that needs the readonly URL too
prove monitor-run.service       # the real thing: fetch, filter, score, dedupe, stage

say "done"
if [ "$CHECK" -eq 0 ]; then
    systemctl list-timers 'monitor-*' --no-pager
    printf '\n   The first unattended pass is the next :00 UTC. Read it with:\n'
    printf '     tail -f /var/log/monitor/run.log\n'
    printf '     sudo systemctl start monitor-status.service && tail -n 40 /var/log/monitor/status.log\n'
    printf '   The review app is not a unit: a person starts it with `make review` on 127.0.0.1:8080 (rule 17).\n'
    printf '   Backfill the days the pipeline was not running, once, per RUNBOOK "Known gaps":\n'
    printf '     make backfill-ted FROM=2026-08-24 TO=2026-09-08 DRY=1\n'
fi
