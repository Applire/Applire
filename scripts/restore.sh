#!/usr/bin/env bash
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.
#
# ---------------------------------------------------------------------------
# Restore an Applire backup made by scripts/backup.sh.
#
#   scripts/restore.sh applire-backup-20260908-201500.tar.gz
#   scripts/restore.sh --force <archive>     # overwrite an install that has data
#
# Run it from the directory that holds docker-compose.yml, on a stack that is
# either down or freshly created. It:
#
#   1. verifies the archive first (scripts/backup.sh --verify),
#   2. starts ONLY postgres, which creates the named volumes if they are new,
#   3. refuses if the database already contains tables — unless --force, which
#      drops and recreates it,
#   4. restores the database with pg_restore and the uploads volume with tar,
#   5. brings the rest of the stack up; the backend runs `alembic upgrade head`
#      at startup, so a backup from an older release is migrated forward,
#   6. prints GET /health so you can see what came back.
#
# THIS SCRIPT NEVER RUNS `docker compose down -v`. Deleting a volume is the
# operator's decision, made with the command in front of them, never a step
# inside a script that was supposed to be recovering data.
#
# Exits non-zero on ANY failure.
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PG_IMAGE="${APPLIRE_PG_IMAGE:-pgvector/pgvector:pg16}"

log()  { printf '%s\n' "$*" >&2; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

_TMPDIRS=()
cleanup() { for d in ${_TMPDIRS+"${_TMPDIRS[@]}"}; do rm -rf "$d"; done; }
trap cleanup EXIT
mktempdir() { local d; d="$(mktemp -d)"; _TMPDIRS+=("$d"); printf '%s' "$d"; }

read_env_value() {
  local key="$1"
  [ -f .env ] || return 0
  sed -n "s/^[[:space:]]*${key}=//p" .env | tail -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

FORCE=0
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi
ARCHIVE="${1:-}"
[ -n "$ARCHIVE" ] || fail "usage: scripts/restore.sh [--force] <archive.tar.gz>"
[ -f "$ARCHIVE" ] || fail "no such archive: $ARCHIVE"
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

POSTGRES_USER="${POSTGRES_USER:-$(read_env_value POSTGRES_USER)}"
POSTGRES_DB="${POSTGRES_DB:-$(read_env_value POSTGRES_DB)}"
POSTGRES_USER="${POSTGRES_USER:-applire}"
POSTGRES_DB="${POSTGRES_DB:-applire}"

command -v docker >/dev/null 2>&1 || fail "docker is not on PATH"

log "== 1/6  Verifying the archive before touching anything =="
"$SCRIPT_DIR/backup.sh" --verify "$ARCHIVE"

log ""
log "== 2/6  Starting postgres (creates the volumes if they are new) =="
docker compose up -d postgres
PG_CID=""
for _ in $(seq 1 60); do
  PG_CID="$(docker compose ps -q postgres || true)"
  if [ -n "$PG_CID" ] && docker compose exec -T postgres \
        pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
[ -n "$PG_CID" ] || fail "postgres did not start"
docker compose exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null \
  || fail "postgres did not become ready within 120s"
log "  postgres is ready"

log ""
log "== 3/6  Checking that this database is safe to restore into =="
TABLE_COUNT="$(docker compose exec -T postgres psql -tAq -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" | tr -d '\r[:space:]')"
log "  the target database has ${TABLE_COUNT} table(s) in schema public"
if [ "${TABLE_COUNT:-0}" -gt 0 ]; then
  if [ "$FORCE" -ne 1 ]; then
    fail "$POSTGRES_DB already contains data. Restoring would overwrite it.
       Restore into a FRESH stack, or re-run with --force to drop and recreate
       the database (this destroys what is there now, and there is no undo)."
  fi
  log "  --force: dropping and recreating ${POSTGRES_DB}"
  docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\" WITH (FORCE);" \
    -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";"
fi

log ""
log "== 4/6  Restoring the database and the uploads volume =="
TMP="$(mktempdir)"
tar -xzf "$ARCHIVE" -C "$TMP"

docker cp "$TMP/db.dump" "$PG_CID:/tmp/applire-restore.dump"
docker compose exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges \
  /tmp/applire-restore.dump
docker compose exec -T postgres rm -f /tmp/applire-restore.dump
log "  database restored"

# The uploads volume is named by the compose file and prefixed by the project, so
# read it off the container the compose file created rather than guessing.
docker compose up -d --no-deps backend >/dev/null
BACKEND_CID="$(docker compose ps -q backend || true)"
[ -n "$BACKEND_CID" ] || fail "the backend container was not created"
UPLOADS_VOLUME="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/data/uploads"}}{{.Name}}{{end}}{{end}}' "$BACKEND_CID")"
[ -n "$UPLOADS_VOLUME" ] || fail "could not find the uploads volume on the backend container"
log "  uploads volume: $UPLOADS_VOLUME"

docker run --rm \
  -v "$UPLOADS_VOLUME":/dst \
  -v "$TMP":/src:ro \
  --entrypoint sh "$PG_IMAGE" -c 'cd /dst && tar -xf /src/uploads.tar'
log "  uploads restored"

log ""
log "== 5/6  Bringing the stack up (migrations run at backend startup) =="
docker compose up -d
for _ in $(seq 1 90); do
  if docker compose exec -T backend python -c \
      "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=3)" \
      >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

log ""
log "== 6/6  What came back =="
docker compose exec -T backend python - <<'PY' || fail "the backend did not answer /health after the restore"
import json, urllib.request
with urllib.request.urlopen("http://localhost:8000/health", timeout=5) as r:
    print(json.dumps(json.load(r), indent=2))
PY

log ""
log "Restore complete. Open the app and check that a document you expect is there."
log "Then run scripts/backup.sh again — the restored instance has no backup of its own yet."
