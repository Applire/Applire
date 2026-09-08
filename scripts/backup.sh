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
# Back up an Applire install: the database AND the uploads volume, in one archive.
#
#   scripts/backup.sh                     # write into ./backups
#   scripts/backup.sh /mnt/nas/applire    # write somewhere else
#   scripts/backup.sh --verify <archive>  # check an archive without restoring it
#
# Run it from the directory that holds docker-compose.yml. The stack must be up.
#
# Both volumes matter and a backup with only one of them is not a backup:
# postgres_data is the vault (profile, applications, generated documents) and
# applire_uploads holds every uploaded CV and profile photo. The volume NAMES are
# read from the running containers rather than guessed from the project name, so
# this works whatever `-p` or COMPOSE_PROJECT_NAME the install uses.
#
# Non-standard compose setup? `docker compose` reads COMPOSE_FILE (colon-separated)
# and COMPOSE_PROJECT_NAME from the environment, and so does this script by simply
# not overriding them:
#   COMPOSE_PROJECT_NAME=myapplire scripts/backup.sh
#
# Exits non-zero on ANY failure. A backup script that reports success on a partial
# archive is worse than none (docs/SELF-HOSTING.md).
# ---------------------------------------------------------------------------
set -euo pipefail

# The instance_state key the ops probe reads for "last backup: never / N days".
# Pinned to models/instance_state.py's KEY_LAST_BACKUP_AT by a unit test — this
# literal is the only thing joining a shell script to a Python constant.
LAST_BACKUP_KEY="last_backup_at"

# Image used to run pg_restore --list for --verify when the stack may be down.
PG_IMAGE="${APPLIRE_PG_IMAGE:-pgvector/pgvector:pg16}"

log()  { printf '%s\n' "$*" >&2; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# One cleanup for every temp directory this script makes, registered once, so a
# `fail` in the middle of a verify does not leave an extracted dump on disk.
_TMPDIRS=()
cleanup() { for d in ${_TMPDIRS+"${_TMPDIRS[@]}"}; do rm -rf "$d"; done; }
trap cleanup EXIT
mktempdir() { local d; d="$(mktemp -d)"; _TMPDIRS+=("$d"); printf '%s' "$d"; }

# .env is read by Docker Compose for ${POSTGRES_*} interpolation but not by this
# shell. Read the same values the same way, without executing the file.
read_env_value() {
  local key="$1"
  [ -f .env ] || return 0
  sed -n "s/^[[:space:]]*${key}=//p" .env | tail -1 | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

POSTGRES_USER="${POSTGRES_USER:-$(read_env_value POSTGRES_USER)}"
POSTGRES_DB="${POSTGRES_DB:-$(read_env_value POSTGRES_DB)}"
POSTGRES_USER="${POSTGRES_USER:-applire}"
POSTGRES_DB="${POSTGRES_DB:-applire}"

# --------------------------------------------------------------------------
# --verify: is this archive a backup, without restoring it? (JF-O-7.2)
# --------------------------------------------------------------------------
verify_archive() {
  local archive="$1"
  [ -f "$archive" ] || fail "no such archive: $archive"

  local size
  size=$(wc -c < "$archive" | tr -d ' ')
  [ "$size" -gt 0 ] || fail "archive is empty: $archive"
  log "  size ......... ${size} bytes"

  local listing
  listing=$(tar -tzf "$archive") || fail "archive is not a readable .tar.gz: $archive"
  printf '%s\n' "$listing" | grep -qx "db.dump"     || fail "archive has no db.dump (the database half is missing)"
  printf '%s\n' "$listing" | grep -qx "uploads.tar" || fail "archive has no uploads.tar (the uploads volume is missing)"
  log "  contents ..... db.dump + uploads.tar present"

  local tmp
  tmp="$(mktempdir)"
  tar -xzf "$archive" -C "$tmp" db.dump manifest.txt 2>/dev/null \
    || tar -xzf "$archive" -C "$tmp" db.dump

  if ! docker run --rm -v "$tmp":/v:ro "$PG_IMAGE" \
        pg_restore --list /v/db.dump > "$tmp/listing.txt" 2>"$tmp/listing.err"; then
    sed 's/^/    /' "$tmp/listing.err" >&2 || true
    fail "pg_restore --list could not read db.dump — the dump is unusable"
  fi
  local entries
  entries=$(grep -vc '^;' "$tmp/listing.txt" || true)
  [ "${entries:-0}" -gt 0 ] || fail "pg_restore --list found no restorable entries — the dump is empty"
  log "  pg_restore ... readable, ${entries} restorable entries"

  if [ -f "$tmp/manifest.txt" ]; then
    log "  manifest:"
    sed 's/^/    /' "$tmp/manifest.txt" >&2
  fi
  log "OK — $archive looks like a usable backup."
}

if [ "${1:-}" = "--verify" ]; then
  [ -n "${2:-}" ] || fail "usage: scripts/backup.sh --verify <archive.tar.gz>"
  log "Verifying $2"
  verify_archive "$2"
  exit 0
fi

# --------------------------------------------------------------------------
# Back up
# --------------------------------------------------------------------------
OUTDIR="${1:-./backups}"
mkdir -p "$OUTDIR"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARCHIVE="$OUTDIR/applire-backup-${STAMP}.tar.gz"

command -v docker >/dev/null 2>&1 || fail "docker is not on PATH"

PG_CID="$(docker compose ps -q postgres || true)"
[ -n "$PG_CID" ] || fail "the postgres container is not running — start the stack first (docker compose up -d)"
BACKEND_CID="$(docker compose ps -q backend || true)"
[ -n "$BACKEND_CID" ] || fail "the backend container is not running — start the stack first (docker compose up -d)"

volume_at() {  # volume_at <container> <mount destination>
  docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$2\"}}{{.Name}}{{end}}{{end}}" "$1"
}
PG_VOLUME="$(volume_at "$PG_CID" /var/lib/postgresql/data)"
UPLOADS_VOLUME="$(volume_at "$BACKEND_CID" /app/data/uploads)"
[ -n "$PG_VOLUME" ]      || fail "could not find the postgres data volume on container $PG_CID"
[ -n "$UPLOADS_VOLUME" ] || fail "could not find the uploads volume on container $BACKEND_CID"

log "Applire backup"
log "  database ..... $POSTGRES_DB (user $POSTGRES_USER) in volume $PG_VOLUME"
log "  uploads ...... volume $UPLOADS_VOLUME"
log "  archive ...... $ARCHIVE"

TMP="$(mktempdir)"


log "  dumping the database ..."
docker compose exec -T postgres \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom \
  > "$TMP/db.dump"
[ -s "$TMP/db.dump" ] || fail "pg_dump produced an empty file"

log "  archiving the uploads volume ..."
docker run --rm \
  -v "$UPLOADS_VOLUME":/src:ro \
  -v "$TMP":/out \
  --entrypoint sh "$PG_IMAGE" -c 'cd /src && tar -cf /out/uploads.tar .'
[ -f "$TMP/uploads.tar" ] || fail "the uploads archive was not produced"

APP_VERSION="$(docker compose exec -T backend python -c 'from applire._version import __version__; print(__version__)' 2>/dev/null | tr -d '\r' || echo unknown)"
{
  echo "created_utc=${STAMP}"
  echo "applire_version=${APP_VERSION}"
  echo "compose_project=${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
  echo "postgres_volume=${PG_VOLUME}"
  echo "uploads_volume=${UPLOADS_VOLUME}"
  echo "postgres_db=${POSTGRES_DB}"
  echo "postgres_user=${POSTGRES_USER}"
} > "$TMP/manifest.txt"

tar -czf "$ARCHIVE" -C "$TMP" db.dump uploads.tar manifest.txt
log "  wrote $(wc -c < "$ARCHIVE" | tr -d ' ') bytes"

# --------------------------------------------------------------------------
# Record the success in the product, so "when did I last back up?" has an answer
# the instance can give (JF-O-7.1). Tolerant of a pre-0.42 install where the
# instance_state table does not exist yet: the backup itself already succeeded,
# and failing here would report a good backup as a failure.
# --------------------------------------------------------------------------
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 \
      -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
      "INSERT INTO instance_state (key, value, updated_at)
       VALUES ('${LAST_BACKUP_KEY}', to_jsonb('${NOW_ISO}'::text), now())
       ON CONFLICT (key) DO UPDATE
         SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;" >/dev/null 2>&1; then
  log "  recorded ${LAST_BACKUP_KEY}=${NOW_ISO} in instance_state"
else
  log "  note: could not record ${LAST_BACKUP_KEY} (instance_state needs Applire 0.42+)."
  log "        The backup itself is fine."
fi

log ""
log "Verifying the archive we just wrote:"
verify_archive "$ARCHIVE"
log ""
log "Done. Restore with:  scripts/restore.sh $ARCHIVE"
