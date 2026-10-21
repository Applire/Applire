#!/usr/bin/env bash
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Glama introspection entrypoint: a throwaway PostgreSQL inside the container,
# the schema, one claimed instance with one agent token, then the real MCP
# server over stdio. stdout is the MCP protocol channel: ALL setup output goes
# to stderr or to a log file. The token lives only in the environment of the
# MCP process; it is never echoed.
set -euo pipefail

PGVER="$(ls /usr/lib/postgresql | sort -n | tail -1)"
PGBIN="/usr/lib/postgresql/${PGVER}/bin"
PGDATA="/tmp/applire-glama-pg"
PGLOG="/tmp/applire-glama-pg.log"
PGPORT=5432
DB_NAME=applire
DB_USER=applire
DB_PASS="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"

log() { echo "[glama-entrypoint] $*" >&2; }

install -d -m 700 -o postgres -g postgres "${PGDATA}"
PWFILE="$(mktemp)"
printf '%s' "${DB_PASS}" > "${PWFILE}"
chmod 644 "${PWFILE}"

log "initialising a throwaway PostgreSQL ${PGVER} cluster"
runuser -u postgres -- "${PGBIN}/initdb" -D "${PGDATA}" -U "${DB_USER}" \
    --auth=scram-sha-256 --pwfile="${PWFILE}" >&2
rm -f "${PWFILE}"

runuser -u postgres -- "${PGBIN}/pg_ctl" -D "${PGDATA}" -l "${PGLOG}" -w -t 60 \
    -o "-c listen_addresses=127.0.0.1 -c port=${PGPORT} -c unix_socket_directories=/tmp -c fsync=off" \
    start >&2

for _ in $(seq 1 60); do
    if "${PGBIN}/pg_isready" -h 127.0.0.1 -p "${PGPORT}" -q; then break; fi
    sleep 0.5
done
"${PGBIN}/pg_isready" -h 127.0.0.1 -p "${PGPORT}" >&2

PGPASSWORD="${DB_PASS}" "${PGBIN}/createdb" -h 127.0.0.1 -p "${PGPORT}" -U "${DB_USER}" "${DB_NAME}" >&2

export DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1:${PGPORT}/${DB_NAME}"

cd /app
log "applying migrations"
alembic upgrade head >&2

log "claiming the throwaway instance and minting an agent token"
APPLIRE_AGENT_TOKEN="$(python /app/glama_seed.py)"
export APPLIRE_AGENT_TOKEN

log "starting the MCP server (stdio)"
exec python -m applire.mcp
