# Self-Hosting Applire

This is the operator's runbook: what to know before you run Applire for yourself, and what to do when something goes wrong. It assumes you already have an install running — for first-time setup see [../README.md](../README.md) (Installation and Configuration sections).

Everything here talks about the **production** topology (`docker-compose.yml` alone, images pulled from GHCR). If you are hacking on Applire's source instead, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Contents

1. [Who this is for](#1-who-this-is-for)
2. [Which topology am I running?](#2-which-topology-am-i-running)
3. [Back up](#3-back-up)
4. [Check a backup without restoring it](#4-check-a-backup-without-restoring-it)
5. [Restore](#5-restore)
6. [⚠️ `docker compose down -v`](#6-️-docker-compose-down--v)
7. [Secrets](#7-secrets)
8. [TLS and a reverse proxy in front](#8-tls-and-a-reverse-proxy-in-front)
9. [Disk and pruning](#9-disk-and-pruning)
10. [Upgrading](#10-upgrading)
11. [Troubleshooting](#11-troubleshooting)
12. [Monitoring from outside](#12-monitoring-from-outside)
13. [What Applire does not do for you](#13-what-applire-does-not-do-for-you)

---

## 1. Who this is for

Applire Community is built for **one operator, one person, one instance**. `AUTH_PROVIDER=none` is the only Community option: there is no login, and anyone who can reach the URL can read and change the vault — your Master Profile, your applications, your generated documents. Treat network reach as the access control. Do not put an `AUTH_PROVIDER=none` instance on a network anyone else can reach without your own authenticating proxy in front of it.

One user maps to exactly one profile. That is a deliberate architecture decision, not a limitation to work around — Applire's data model has no concept of a second person sharing an instance, and nothing in the roadmap adds one to Community.

If a second person in your household or team wants their own Applire, the workaround is a **second compose project**, not a shared instance:

- a separate directory with its own copy of `docker-compose.yml` and `.env`
- `COMPOSE_PROJECT_NAME=applire-<name>` in that `.env` (or pass `-p applire-<name>` on every `docker compose` call)
- a different published port for its `nginx` service, since two projects can't both bind host port 80 — edit the `ports:` mapping, e.g. `"8081:80"`

A distinct project name gives you distinct named volumes and a distinct database automatically — the two installs never touch each other's data. This is two single-user instances side by side, not multi-user support.

## 2. Which topology am I running?

`docker-compose.yml` on its own is the **production** topology: every service is a pre-built GHCR image, and nginx on port 80 is the only published port — backend and frontend stay internal.

`docker-compose.override.yml` sitting beside it is the **development** topology: it builds the backend and frontend from source, mounts your working tree for hot reload, and publishes `3000` (frontend), `8001` (an *unauthenticated* API), and `5433` (Postgres with the compose default credentials).

Docker Compose auto-applies an override file whenever it sits next to the base compose file — which is every source clone — so a plain `docker compose up -d` inside a clone silently gives you the dev topology. For a real install, always be explicit:

```bash
docker compose -f docker-compose.yml up -d
```

Three independent ways to check which one you're actually running:

```bash
# 1. The published-port column
docker compose ps

# 2. The instance says so directly
curl -s http://localhost/health | grep topology
# "topology": "production"   — or —   "topology": "dev"

# 3. A startup warning naming the exposed ports, if you're on dev
docker compose logs backend | grep -i topology
```

`docker compose config` (no flags) prints the fully merged configuration — if you want to see exactly what the override added, look for `build:` blocks (only present when the override is applied) alongside the base `image:` directives.

## 3. Back up

A backup that only covers the database is not a backup. Two volumes have to travel together:

- **`postgres_data`** is the vault — your profile, applications, and every generated document, stored as JSONB.
- **`applire_uploads`** is every uploaded CV and profile photo, mounted into the backend container at `/app/data/uploads`.

`scripts/backup.sh` captures both in one archive. If you installed the no-clone way — the
two release assets and nothing else — you do not have the scripts yet; fetch them from the
same release (`releases/latest/download/` has no `scripts/` bundle, so take them from the
repository at the tag you are running) and `chmod +x` them.

```bash
scripts/backup.sh                      # writes into ./backups
scripts/backup.sh /mnt/nas/applire     # write somewhere else instead
```

Run it from the directory that holds `docker-compose.yml`, with the stack up. It reads `COMPOSE_PROJECT_NAME` and `POSTGRES_USER`/`POSTGRES_DB` from your environment (or from `.env`) the same way `docker compose` does, so it works under whatever project name or credentials your install actually uses:

```bash
COMPOSE_PROJECT_NAME=myapplire scripts/backup.sh
```

The archive it writes (`applire-backup-<timestamp>.tar.gz`) contains:

- `db.dump` — a `pg_dump --format=custom` of the database
- `uploads.tar` — the full contents of the uploads volume
- `manifest.txt` — the Applire version, compose project name, and the exact volume names the backup was taken from

On a successful run the script also records the timestamp as `last_backup_at` inside the instance itself (needs Applire 0.42 or later — on an older install the backup still succeeds, the script just notes it couldn't record the marker). That is what lets the running instance answer "when did I last back up?" on its own.

**The script exits non-zero on any failure** — a `set -euo pipefail` script that reports success on a partial archive would be worse than no backup at all.

A cron example, backing up nightly at 03:00 to an external mount:

```cron
0 3 * * * cd /path/to/applire && scripts/backup.sh /mnt/nas/applire/backups >> /var/log/applire-backup.log 2>&1
```

## 4. Check a backup without restoring it

```bash
scripts/backup.sh --verify applire-backup-20260908-030000.tar.gz
```

This confirms, without touching your running stack, that an archive is a usable backup:

- the archive isn't empty and is a readable `.tar.gz`
- both `db.dump` and `uploads.tar` are present in it
- `pg_restore --list` can actually read `db.dump` and finds at least one restorable entry

Make this a cheap weekly habit — point it at whatever your cron job just produced. It is **not** the same guarantee as having restored once: `--verify` proves the archive is structurally sound, not that a full restore onto a fresh stack comes back clean end to end. Do a real restore rehearsal occasionally too (Section 5), ideally onto a throwaway compose project.

## 5. Restore

```bash
scripts/restore.sh applire-backup-20260908-030000.tar.gz
scripts/restore.sh --force applire-backup-20260908-030000.tar.gz   # overwrite an install that already has data
```

Run it from the directory that holds `docker-compose.yml`, on a stack that is either down or freshly created. It walks through six steps, in order:

1. **Verifies the archive first** — the same check as `scripts/backup.sh --verify`. Nothing is touched if the archive itself is bad.
2. **Starts only `postgres`**, which creates the named volumes if they don't exist yet.
3. **Checks the target database is safe to restore into.** If it already has tables in the `public` schema, the script refuses and stops — unless you passed `--force`, in which case it drops and recreates the database. There is no undo for that drop.
4. **Restores the database with `pg_restore`** and **the uploads volume with `tar`**.
5. **Brings the rest of the stack up.** The backend runs `alembic upgrade head` on startup, so a backup taken on an older release is migrated forward to the schema the running image expects.
6. **Prints `GET /health`** so you can see what came back.

The script itself never runs `docker compose down -v` — deleting a volume stays your decision, made with the command in front of you, not a step buried inside a recovery script.

After it finishes: **open the app and confirm a document you expect is actually there.** `/health` returning `"status": "ok"` tells you the backend is serving, not that your data survived the round trip intact. Then take a fresh backup — the restored instance has no backup of its own yet, and its `last_backup_at` marker is necessarily *older* than the archive you just restored: a backup is dumped before its own completion timestamp is written, so what comes back is the timestamp of the run before it. Taking a backup now makes the marker true again.

## 6. ⚠️ `docker compose down -v`

`-v` deletes the named volumes — `postgres_data` and `applire_uploads` — and **there is no undo.** That is your vault and every uploaded file, gone.

`docker compose down` on its own is safe: it stops and removes the containers and keeps every volume untouched.

To update an install, you never need `-v`:

```bash
docker compose pull && docker compose up -d
```

If you've picked up the habit of `docker compose down -v` from a tutorial, or from this repo's own [CI/CD guide](CI_CD_GUIDE.md) or test workflows — it appears there deliberately, to reset a disposable CI database between test runs. That is a throwaway-environment habit, and it does not belong anywhere near an install that holds someone's career record.

## 7. Secrets

Database credentials are `${POSTGRES_USER:-applire}` / `${POSTGRES_PASSWORD:-applire}` / `${POSTGRES_DB:-applire}` in `docker-compose.yml`, read from your environment or `.env`. The defaults match what every install has used so far, so leaving `.env` alone changes nothing.

**Critical, and easy to get wrong:** PostgreSQL only reads `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` the *first time* it initialises the data directory. On an **existing** `postgres_data` volume, changing these in `.env` and restarting does not change the database's actual credentials — it just makes the backend fail to connect, because the database still has the old ones.

- **New install:** set them in `.env` before the first `docker compose up -d`. Done.
- **Existing install:** back up first (Section 3), then either restore into a fresh volume with the new credentials already set in `.env` (`scripts/restore.sh` creates the volumes fresh when they don't exist), or start a fresh compose project the way Section 1 describes and restore into that. Don't edit the credentials on a volume that already has data — that's the state that breaks the connection.

Other secrets to mind:

- **`chmod 600 .env`** — it holds your database credentials and your LLM provider's API key.
- The provider API key lives only in `.env` and is never written to a log.
- **`LLM_DEBUG_LOG=true`** writes every prompt and completion — including CV and interview PII — to JSONL files inside the backend container, with **no size or age cap**. The backend logs a WARNING at every startup while it's on, and `GET /health` reports `"debug_log_on": true`. Turn it off (`LLM_DEBUG_LOG=false` or delete the line) and delete the accumulated files when you're done debugging.

## 8. TLS and a reverse proxy in front

Applire's own nginx has no config file on the host to edit — it's baked into the `applire-nginx` image (`nginx/Dockerfile` `COPY`s `self-hosted.conf` in at build time), so there's nothing to place or patch after a fresh `docker compose pull`. Two ways to add TLS or a custom domain:

**Bind-mount your own file over the baked-in one:**

```yaml
  nginx:
    volumes:
      - ./my-nginx.conf:/etc/nginx/conf.d/default.conf:ro
```

**Or put your own reverse proxy (Caddy, Traefik, another nginx) in front of port 80** and let it terminate TLS, forwarding plain HTTP to Applire's nginx.

If you go the second route, set `APPLIRE_BASE_URL` in `.env` to your proxy's externally reachable `scheme://host` (or `scheme://host:port`). The MCP/agent channel uses it to build the `html_url`/`pdf_url` links it returns to a connected agent — left unset, those links point at `http://localhost:8001`, which is only correct on an unproxied local dev box.

## 9. Disk and pruning

Three named volumes hold everything durable: `postgres_data`, `ollama_data` (only used with `docker compose --profile ollama up`), and `applire_uploads`.

Container logs are already bounded — `docker-compose.yml` sets the `json-file` driver to `max-size: 10m`, `max-file: 3` for every service, so log growth alone won't fill your disk.

The real disk eater on a self-hosted box is **orphaned anonymous volumes** left behind by image rebuilds — not the named volumes above. Check before you clean, and clean in this order:

```bash
docker system df                      # see where the space actually is
docker volume ls -qf dangling=true    # look — what would prune actually remove?
docker volume prune                   # then remove the dangling ones
docker container prune                # then stopped containers, if you want them gone too
```

**Never** run `docker volume prune -a` / `--all`, and never run anything that would remove a volume named `*_postgres_data` or `*_applire_uploads` — `docker volume prune` on its own only touches volumes nothing references, so a running stack's named volumes are safe from it, but `-a`/`--all` widens that and is not worth the risk on an install with real data.

## 10. Upgrading

```bash
docker compose pull && docker compose up -d
```

Migrations run automatically when the backend container starts — there's no separate migration step to remember.

The instance tells you what an upgrade actually changed, in three places:

- a WARNING block in `docker compose logs backend`
- `upgrade_notice` on `GET /health` (`null` when there's nothing to report — a fresh install, an unchanged version, or a version jump that introduced nothing this environment is missing)
- a dismissable notice on the dashboard

Dismissing the notice (in the UI, or directly) records the now-running version as seen, so it won't repeat on the next restart:

```bash
curl -X POST http://localhost/api/settings/upgrade-notice/dismiss
```

**Back up before you upgrade** (Section 3) — migrations are one-directional in practice, and a bad upgrade is much easier to undo from a backup than by hand.

**Coming from a release older than `v0.37.0-beta`?** Step through `v0.37.2-beta` first. Profiles imported before the reconciliation engine could hold flat duplicate employers and orphaned projects, and the one-time `scripts/migrate_flat_duplicates.py` pass that folds them into the typed model only shipped in `v0.37.0-beta` … `v0.37.2-beta`. This is data hygiene, not a schema requirement — Alembic migrations still run automatically on any direct jump, it just leaves those duplicates sitting in your profile instead of cleaning them up.

## 11. Troubleshooting

### Generations hang for minutes and then fail

Usually **provider credit exhausted — an HTTP 402**, not slowness. This has been misdiagnosed as LLM latency before; check for 402 before you trust any latency observation.

```bash
docker compose logs backend | grep -i "402\|quota\|credit"
```

### Interview turns cost more input tokens than you expected

`LLM_STRUCTURED_OUTPUT` is `auto` by default. On the one call that writes your vault — the
interview/testimony reconciler — Applire sends your model the 15 operations it may emit as
a JSON schema alongside the prompt. That is **about 2,300 extra input tokens per turn**
(roughly a third more input on that call; output and latency are unchanged). It measurably
helps a model that drops a required field or loses whole entries; on the models that were
already clean it changes nothing but the bill.

```bash
# in .env — save the tokens, keep today's plain JSON mode
LLM_STRUCTURED_OUTPUT=off
```

If your model's endpoint does not support schemas it rejects the request once, the backend
logs a WARNING naming the model, falls back to plain JSON mode for the rest of that process
and completes the turn — so `auto` is safe to leave on even on a model you have not
checked. `docs/llm-models.md` carries the measured numbers.

### The backend keeps restarting after an upgrade

A database migration failed at startup. The backend deliberately refuses to serve on a schema it doesn't recognise, so it exits rather than run against half-migrated tables — which `restart: unless-stopped` then retries in a loop.

```bash
docker compose logs backend
```

Look for the Alembic traceback. Restore from your pre-upgrade backup (Section 5), or report the traceback.

### Ollama answers nothing / the model is missing

The Ollama server starts **empty** — nothing is pulled by default.

```bash
docker compose exec ollama ollama pull llama3.2
```

Or set `OLLAMA_MODEL` in `.env` to a model you've already pulled. CPU-only inference is slow — raise `LLM_TIMEOUT` (e.g. `600`) so a slow answer isn't cut off mid-generation.

### Requests die at exactly 5 minutes with a 504

The reverse proxy's read timeout is 300 seconds (`proxy_read_timeout 300s` / `proxy_send_timeout 300s` in `nginx/self-hosted.conf`), and it's baked into the `applire-nginx` image — **it is not configurable by an environment variable.** Keep `LLM_TIMEOUT` below 300, or bind-mount your own nginx config with a larger `proxy_read_timeout` (Section 8).

### I don't know which topology I'm running

See [Section 2](#2-which-topology-am-i-running).

### `docker compose exec postgres ...` says the role does not exist

You changed `POSTGRES_USER` (or `POSTGRES_PASSWORD`/`POSTGRES_DB`) against an **existing** volume — Postgres only reads those on first initialisation. See [Section 7](#7-secrets).

## 12. Monitoring from outside

Applire tells you how it is doing at **`GET /api/ops/health`**. It answers **200** while the
instance is `ok` or `degraded` and **503** when something is `down`, so a simple uptime check needs
no JSON parsing at all:

    curl -fsS http://localhost/api/ops/health > /dev/null || echo "Applire is down"

Point Uptime Kuma, a Zabbix HTTP agent or a cron job at that URL — this is a **supported** path.
Applire cannot send you an e-mail or a push message, and it deliberately does not try; your own
monitoring is the notification channel.

**The response is a contract.** New fields may appear in any release. A field is never renamed or
removed without an *Upgrade notes* entry in the release notes, so a dashboard you build on it keeps
working.

What it reports:

| Field | Means |
|---|---|
| `status` | `ok`, `degraded` (something wants a look, nothing is broken) or `down` |
| `components.database` | the database answers |
| `components.migrations` | the database schema matches this image. `degraded` after an image pull means the backend has not been restarted |
| `components.retention` | when the GDPR cleanup last ran, what it deleted, and whether the counts look unusual. `degraded` after two missed nightly runs |
| `components.disk` | free space on the uploads volume |
| `components.backup` | how long since `scripts/backup.sh` last succeeded; warns after 30 days |
| `components.provider` | whether your LLM provider answers, and your remaining credit where the provider publishes one |
| `components.errors` | failures in the last hour, counted inside this backend process |
| `usage` | tokens spent today and over the last seven days, and which documents and applications spent them |

The same facts are shown on the **Admin** page of the UI (one quiet line while everything is fine,
expanded when something is not).

**The provider check is the only one that costs anything**, and you choose how much:

| `OPS_PROVIDER_PROBE` | What runs |
|---|---|
| `both` *(default)* | a tiny test call every 15 minutes (about 96 a day — one job application is 89–105) **and** a balance read |
| `reachability` | the test call only |
| `credit` | the balance read only — this costs nothing; it is an account endpoint, not a model call |
| `off` | neither. Your instance can then no longer tell you that your provider credit ran out |

`OPS_PROVIDER_PROBE_INTERVAL_MINUTES` (default 15) sets the minimum gap between two checks. The
endpoint only ever serves the cached result, so calling it never spends your credit.

Only providers that publish a balance can report one — OpenRouter does. For a local Ollama or any
OpenAI-compatible endpoint the credit line reads **"this provider reports no balance"**, which is
the correct answer and not a fault.

**What the endpoint reveals.** There is no login in the Community edition, so treat this URL as
readable by anything that can reach the port. It reports versions, your provider and model name,
component statuses and the numeric gauges. It never reports an API key, a file path, a host name, or
anything from a candidate's documents.

**Token costs.** Every model call is recorded with its token counts — numbers and ids only, never
the text of a prompt or an answer. (Full text is only ever written when you switch `LLM_DEBUG_LOG`
on; that log contains personal data and is off by default.) Records are kept for
`LLM_USAGE_RETENTION_DAYS` days (365 by default; `0` keeps them forever) and are deleted by the same
nightly cleanup. Where a provider does not report token counts, Applire estimates them and says so
next to the figure.

## 13. What Applire does not do for you

- No automated off-host backup. `scripts/backup.sh` writes an archive; getting it off this machine (a NAS, object storage, another host) is on you.
- No alerting. `GET /api/ops/health` ([Section 12](#12-monitoring-from-outside)) tells you the state of the database, the disk, the nightly cleanup, your backups and your provider — but nothing pages you; point your own monitoring at it.
- No multi-user support (Section 1).
- No built-in TLS (Section 8).
