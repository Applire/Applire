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
13. [Accounts, first-run setup and sign-in](#13-accounts-first-run-setup-and-sign-in)
14. [Single sign-on (OIDC)](#14-single-sign-on-oidc)
15. [Outgoing mail (SMTP)](#15-outgoing-mail-smtp)
16. [Tokens: agents and scripts](#16-tokens-agents-and-scripts)
17. [Settings an administrator can change without a restart](#17-settings-an-administrator-can-change-without-a-restart)
18. [You are the controller of other people's data](#18-you-are-the-controller-of-other-peoples-data)
19. [What Applire does not do for you](#19-what-applire-does-not-do-for-you)

---

## 1. Who this is for

Applire Community is built for **one operator running one instance for one or several people** — yourself, a household, a small team. Sign-in is always on: every person has their own account (email + password, or your identity provider), and every person's vault — Master Profile, applications, generated documents — is visible to that person only. There is no user cap. Accounts are created by an administrator; there is no open sign-up.

One user maps to exactly one profile. That is a deliberate architecture decision: a second person gets their own account, not a second profile on yours.

Two roles exist. An **administrator** manages accounts (create, invite, disable, delete, reset a password, revoke tokens) and sees account metadata on the People page — email, role, status, last sign-in, counts, storage and AI usage. That page has no view of anybody's profile or documents. A **user** works with their own data. The instance has one LLM provider and key (yours); every call is attributed to the person who triggered it.

Everything about accounts is in [Section 13](#13-accounts-first-run-setup-and-sign-in). The rest of this runbook is for whoever has shell access to the server.

## 2. Which topology am I running?

`docker-compose.yml` on its own is the **production** topology: every service is a pre-built GHCR image, and nginx on port 80 is the only published port — backend and frontend stay internal.

`docker-compose.override.yml` sitting beside it is the **development** topology: it builds the backend and frontend from source, mounts your working tree for hot reload, and publishes `3000` (frontend), `8001` (the API, bypassing nginx — login still applies, but use the app through port 80, see Section 8), and `5433` (Postgres with the compose default credentials).

Docker Compose auto-applies an override file whenever it sits next to the base compose file — which is every source clone — so a plain `docker compose up -d` inside a clone silently gives you the dev topology. For a real install, always be explicit:

```bash
docker compose -f docker-compose.yml up -d
```

Three independent ways to check which one you're actually running:

```bash
# 1. The published-port column
docker compose ps

# 2. The instance says so directly (needs an admin session or a monitoring token — Section 12)
curl -s -H "Authorization: Bearer apl_…" http://localhost/api/ops/health | grep topology
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

Run it with the stack up, from the directory that holds your install's `docker-compose.yml`. The script itself can live anywhere, for example in a checkout of the repository or in `/opt/applire-scripts/`:

```bash
cd /path/to/your/applire                  # the folder with docker-compose.yml and .env
/opt/applire-scripts/backup.sh /mnt/nas/applire
```

The script backs up the stack that `docker compose` in that folder means. That is `COMPOSE_PROJECT_NAME` from your shell or `.env`, else the `name:` in the compose file, else the folder's name, and it prints the project it picked. You only need to name the project yourself if you started the stack with `docker compose -p <name>` or with a `COMPOSE_PROJECT_NAME` that you exported in a shell and did not put in `.env`. With the name set, the script also works from a folder that has no compose file:

```bash
COMPOSE_PROJECT_NAME=myapplire /opt/applire-scripts/backup.sh
```

If the project it picked has no running database, the script stops and lists the Applire stacks that *are* running on the host, so you can see which name to set. The database name and user come from your shell, else from the running database container, else from `.env`, else the compose defaults.

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

Run it from the directory that holds `docker-compose.yml`, on a stack that is either down or freshly created. Unlike `backup.sh`, `restore.sh` starts services itself with `docker compose`, so it restores into the project that `docker compose` in that folder means. If you started your stack with `docker compose -p <name>`, set `COMPOSE_PROJECT_NAME=<name>` for the restore too. Otherwise the restore goes into a new, empty project named after the folder, and your real stack stays as it was. It walks through six steps, in order:

1. **Verifies the archive first** — the same check as `scripts/backup.sh --verify`. Nothing is touched if the archive itself is bad.
2. **Starts only `postgres`**, which creates the named volumes if they don't exist yet.
3. **Checks the target database is safe to restore into.** If it already has tables in the `public` schema, the script refuses and stops — unless you passed `--force`, in which case it drops and recreates the database. There is no undo for that drop.
4. **Restores the database with `pg_restore`** and **the uploads volume with `tar`**.
5. **Brings the rest of the stack up.** The backend runs `alembic upgrade head` on startup, so a backup taken on an older release is migrated forward to the schema the running image expects.
6. **Prints `GET /health`** so you can see what came back.

Accounts, sessions, tokens and the audit log live in the database, so a restore brings them back with it. A backup taken **before** the upgrade to 0.43 restores to an instance that has no account yet: after the backend has migrated it forward you meet the setup screen again (Section 13).

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
- The provider API key lives in `.env`, or, if an administrator entered one in the settings panel, encrypted in the database (Section 17). It is never written to a log and never shown again.
- **The session secret** (it signs document links for agents) is generated on first start and stored in the database (`instance_state`) — there is nothing to configure, and it travels with your backup. Passwords are stored hashed (scrypt); tokens are stored hashed and shown once.
- **The setup code** is printed in the backend log until the instance is claimed (Section 13). Treat the log as sensitive until then.
- **`LLM_DEBUG_LOG=true`** writes every prompt and completion — including CV and interview PII — to JSONL files inside the backend container, with **no size or age cap**. The backend logs a WARNING at every startup while it's on, and `GET /api/ops/health` reports `"debug_log_on": true`. Since 0.43 one instance can serve several people, so the file then holds **other people's** CVs and interview answers as well as yours. Turn it off (`LLM_DEBUG_LOG=false` or delete the line) and delete the accumulated files when you're done debugging. In the shipped `docker-compose.yml` the files live inside the backend container, not on a volume, so recreating the container (for example on an upgrade) also deletes them.

## 8. TLS and a reverse proxy in front

Applire's own nginx has no config file on the host to edit — it's baked into the `applire-nginx` image (`nginx/Dockerfile` `COPY`s `self-hosted.conf` in at build time), so there's nothing to place or patch after a fresh `docker compose pull`. Two ways to add TLS or a custom domain:

**Bind-mount your own file over the baked-in one:**

```yaml
  nginx:
    volumes:
      - ./my-nginx.conf:/etc/nginx/conf.d/default.conf:ro
```

**Or put your own reverse proxy (Caddy, Traefik, another nginx) in front of port 80** and let it terminate TLS, forwarding plain HTTP to Applire's nginx.

If you go the second route, **two things are mandatory**:

1. **Your proxy must forward the `Host` header unchanged** (including a non-default port) — **or** you set `APPLIRE_BASE_URL` in `.env` to your proxy's externally reachable `scheme://host` (or `scheme://host:port`). Every sign-in, setup and other state-changing request is checked: the browser's `Origin` must match the `Host` Applire receives (or the host of `APPLIRE_BASE_URL`). If it does not, the request is refused with 403 `origin_mismatch`, and the message names both remedies. Applire's own nginx already forwards `Host $http_host`; a bind-mounted config of your own must do the same (the shipped default is `nginx/self-hosted.conf`).
2. **Set `COOKIE_SECURE=true`** as soon as people reach Applire over https. It marks the sign-in cookie `Secure`. The default is `false` so plain-http LAN installs keep working — browsers drop `Secure` cookies on http, so setting it on a plain-http install locks everybody out.

`APPLIRE_BASE_URL` is also what builds the `html_url`/`pdf_url` links the MCP/agent channel returns. Left unset, those links point at `http://localhost`, which is right only when the agent runs on the server itself. "Unset" means the line is absent from `.env` or empty. Writing the default out (`APPLIRE_BASE_URL=http://localhost`) counts as a setting, with the consequences below. Links in invitation and reset **mails** are built from `APPLIRE_BASE_URL` only, so mail needs it (Section 15). OIDC (Section 14) requires it.

**Set it to the one address people open Applire on.** Once `APPLIRE_BASE_URL` is set, Applire accepts sign-ins only for that host name and for `localhost`. A request for any other name is refused with 403 `origin_mismatch`, which protects `/setup` against DNS-rebinding. So `APPLIRE_BASE_URL=http://localhost` on a server that people reach as `http://192.168.1.5` locks out every device except the server itself. If people reach the instance under two names, pick the one in your links and use only that.

**Your proxy's timeouts.** Applire's own nginx waits up to 300 seconds for the backend (Section 11). Many proxies give up much earlier; nginx's default `proxy_read_timeout`, for example, is 60 seconds. A long request then fails at your proxy with a 504 while Applire is still working. Give your proxy at least the same 300 seconds for Applire's address.

**The login throttle and your proxy.** The login throttle is keyed on the email and the client address that Applire's nginx sees. By default nginx trusts **no** `X-Forwarded-For` header from anyone, because a device on your network could otherwise give itself a fresh address on every attempt and never be slowed down. Without a proxy you need nothing. Behind your own TLS proxy, every request arrives from the proxy's address and everyone shares one throttle key. To fix that, name the proxy with one line in `.env`:

```env
APPLIRE_TRUSTED_PROXY=172.18.0.1        # your proxy's address as nginx sees it (IP or CIDR, comma-separated)
```

Then run `docker compose up -d`. To find the address, look at the first field of `docker compose logs nginx` while you open Applire through the proxy. nginx refuses to start on a value that is not an IP address or CIDR range, and `docker compose logs nginx` names the entry. A bind-mounted config of your own trusts nobody unless it contains `include /etc/nginx/applire/*.conf;` (the shipped default does).

What nginx sees without a proxy: Docker's port publishing keeps the real IPv4 address of a client on your network. Requests to `localhost`, over IPv6, or from another container on the same host arrive from the Docker bridge gateway (for example `172.17.0.1`), so those clients share one throttle key. That is harmless for a household. Under an active attack on the same account from the same path, though, the owner's sign-in on that path waits behind the attacker's attempts. The wait is at most 30 seconds. An attempt that cannot start within those 30 seconds is turned away unchecked, with the same "several failed attempts" message the delay shows. In that one case the correct password does **not** get the owner in. The owner then tries again once the attack stops, or from another path. The cure is the `APPLIRE_TRUSTED_PROXY` line above, not a lockout: Applire never locks an account.

Two ways to get the setting wrong without any message:

- **An address nginx never sees.** Behind Docker's port publishing, your proxy may reach nginx from the Docker bridge gateway, not from its LAN address. The setting then matches nothing, and every client still shares one key. Take the address from `docker compose logs nginx`, as above, not from your proxy's own configuration.
- **A range that is too wide.** Every address in the range may set `X-Forwarded-For` and choose its own throttle key, which removes the throttle for that whole range. Name the proxy's single address (or its container network), never your whole LAN or `0.0.0.0/0`.

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

**Upgrading from 0.42 or earlier to 0.43 (accounts):** the first start stops at the setup screen. Read the one-time code from `docker compose logs backend`, open `/setup`, and claim the instance — your existing vault becomes the administrator's account (Section 13). Also: delete `AUTH_PROVIDER=none` from `.env` (it now means `local`), add `APPLIRE_AGENT_TOKEN` to your MCP client configs (Section 16), and move any uptime probe that read more than the status from `/health` to `/api/ops/health` (Section 12). If your database held more than one live Master Profile for a user, the migration keeps the newest and retires the others; the administrator's upgrade notice names how many. Usage recorded before the upgrade is not attributed to a person, so AI usage per person starts at 0. The full list is in the `CHANGELOG.md` *Upgrade notes*. Four checks in an old `.env` that the notes are easy to miss on:

- **The comment above `AUTH_PROVIDER=none` is no longer true.** Older `.env` files carry a comment block that begins `# 'none' disables authentication` (or `# Auth — 'none' disables authentication`). It describes the pre-0.43 behaviour, so delete it together with the line.
- **`LLM_DEBUG_LOG=true`** now writes other people's CVs and interview answers to the debug log as well as yours, and the backend warns about it at every start (Section 7). Unless you are debugging right now, set it to `false`.
- **Variables your `.env` does not set.** The upgrade notice (below) names the new and changed ones. To compare by hand, fetch the release's `env.example` next to your `.env` and list the variable names it has and your `.env` lacks:

  ```bash
  curl -L -o env.example.new https://github.com/Applire/Applire/releases/latest/download/env.example
  comm -23 <(sed -n 's/^#\{0,1\}\([A-Z][A-Z0-9_]*\)=.*/\1/p' env.example.new | sort -u) \
           <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort -u)
  ```

  Most of them are optional with a working default. Read the comment above each one in `env.example.new`.
- **Your compose file.** If you wrote or trimmed `docker-compose.yml` yourself, compare it with the release's file. The agent setup in Section 16, for example, uses its `mcp` service.

The instance tells you what an upgrade actually changed, in three places:

- a WARNING block in `docker compose logs backend`
- `upgrade_notice` on `GET /api/ops/health` (admin session or monitoring token; `null` when there's nothing to report — a fresh install, an unchanged version, or a version jump that introduced nothing this environment is missing)
- a dismissable notice on the dashboard (shown to administrators)

Dismissing the notice (in the UI, or directly) records the now-running version as seen, so it won't repeat on the next restart:

The dismiss endpoint needs an administrator, so the in-app button is the easy way. From a script, use an API token of an administrator (Section 16):

```bash
curl -X POST -H "Authorization: Bearer apl_…" http://localhost/api/settings/upgrade-notice/dismiss
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
interview/testimony reconciler — Applire sends your model the operations it may emit as
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

### I cannot sign in

- **"origin_mismatch" (403) on the sign-in or setup page:** Applire compares the address in your browser with the `Host` it receives. Open Applire on the address your proxy publishes, make your proxy forward `Host` unchanged, or set `APPLIRE_BASE_URL` (Section 8).
- **Sign-in "works" but you are signed out on the next click:** `COOKIE_SECURE=true` on a plain-http install. Set it back to `false`, or serve Applire over https.
- **The setup page asks for a code you do not have:** `docker compose logs backend | grep "SETUP REQUIRED"` — a new code is printed at every start until the instance is claimed. Or skip the page: `docker compose exec backend python -m applire.admin create-admin --email you@example.org`.
- **You forgot the last administrator's password:** `docker compose exec backend python -m applire.admin reset-password --email you@example.org` (add `--password-stdin` to pipe the new one in).
- **A person is told their account is disabled:** an administrator disabled it (Administration → People). It is only shown after a correct password.
- **Repeated failures make the page slow:** the login throttle only delays; it never locks an account.

### The backend exits at start with a message about `AUTH_HARNESS`

`AUTH_HARNESS=true` is the test harness (every request is the administrator, no login). It is refused unless the database is a throwaway test database. Remove the line from `.env` — a real install never sets it.

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

Applire has two health endpoints.

**`GET /health`** is open and reports liveness only: `status`, `edition` and `version`. That is all
the compose healthcheck and a simple "is it up" probe need.

**`GET /api/ops/health`** tells you how the instance is doing. It needs either an **administrator
session** or a **monitoring token** — a read-only token an administrator creates under
Administration → Monitoring (Section 16). It answers **200** while the instance is `ok` or
`degraded` and **503** when something is `down`, so a simple uptime check needs no JSON parsing
at all:

    curl -fsS -H "Authorization: Bearer apl_…" http://localhost/api/ops/health > /dev/null || echo "Applire is down"

A monitoring token works for this one URL only, and only while the administrator who created it
is still an active administrator. Fields that used to be on `/health` (`llm_provider`,
`upgrade_notice`, `debug_log_on`, `topology`, `ops`) are now here. A probe that read them from
`/health` before 0.43 must switch.

Point Uptime Kuma, a Zabbix HTTP agent or a cron job at that URL with the token in an `Authorization: Bearer` header — this is a **supported** path.
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

The same facts are shown on the **Administration → Monitoring** page of the UI (one quiet line while everything is fine,
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

**What the endpoint reveals.** It is readable only with an administrator session or a monitoring
token, so keep that token as secret as a password. It reports versions, your provider and model name,
component statuses and the numeric gauges, and a count of profiles the upgrade retired. It never reports an API key, a file path, a host name, or
anything from a candidate's documents.

**Token costs.** Every model call is recorded with its token counts — numbers and ids only, never
the text of a prompt or an answer. (Full text is only ever written when you switch `LLM_DEBUG_LOG`
on; that log contains personal data and is off by default.) Records are kept for
`LLM_USAGE_RETENTION_DAYS` days (365 by default; `0` keeps them forever) and are deleted by the same
nightly cleanup. Where a provider does not report token counts, Applire estimates them and says so
next to the figure.

## 13. Accounts, first-run setup and sign-in

**First start (new install or upgrade).** Until someone claims the instance, the backend prints a block like this at every start, with a fresh one-time code:

```
SETUP REQUIRED — open /setup on the address where you normally open Applire … and enter: <code> — or run: docker compose exec backend python -m applire.admin create-admin --email you@example.org. A new code is printed at every start until setup is done.
```

```bash
docker compose logs backend | grep "SETUP REQUIRED"
```

**Only the code from the latest start works.** Every start replaces the code, so after a few restarts the log holds several codes and only the last one is valid. The code is the six groups of four letters and digits after `enter:` (`XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`; case and dashes do not matter). To print only the newest:

```bash
docker compose logs backend | grep "SETUP REQUIRED" | tail -1
```

Open `/setup`, enter the code, your email and a password (12–256 characters, typed twice), and the instance is yours. Only a person with access to the server can read the code, which is the point: a stranger who finds the URL cannot claim your instance. On an **upgraded** install, the existing vault becomes the administrator's account — same data, now behind a login. The `create-admin` command does the same from the shell and refuses once the instance is claimed. It asks for the password twice, or reads it from standard input with `--password-stdin` (for scripts).

**Adding people.** Sign in as the administrator and open Administration → People. *Add person* creates a pending account and an **invite link** (valid 7 days, single use) which you hand over — or, with SMTP configured (Section 15), Applire mails it. The person opens the link and chooses a password. There is no open sign-up. You can also re-invite, issue a **reset link** (valid 1 hour; you never see the new password), change a role, disable or delete an account, and revoke all of a person's tokens. The last administrator cannot be demoted, disabled or deleted. Every one of these actions is written to an audit log (who, what, when, target — no IP address), kept `AUDIT_LOG_RETENTION_DAYS` days (730; `0` = forever).

**Sessions.** A session lasts 14 days idle and 90 days absolute. Sign-in attempts are slowed down per (email, client) after failures; nothing locks an account.

**People can leave on their own.** Settings → Account lets a person change their password, link or unlink single sign-on, sign out, and delete their own account (confirmed with the password, or a fresh sign-in at the identity provider). Their data is erased; other people's data and job postings that other people still use stay. The last administrator cannot delete themselves.

**Forgot password.** With SMTP, a "forgot password" link on the sign-in page mails a reset link. Without SMTP, an administrator issues a reset link; if the last administrator is locked out, use the CLI (`python -m applire.admin reset-password --email …`).

**Why `AUTH_PROVIDER=none` no longer disables sign-in.** The value is still accepted and means `local`, with a startup WARNING; delete it from `.env`. The old no-login behaviour exists only as a test harness (`AUTH_HARNESS`), which refuses to start on anything but a throwaway test database.

**`/docs` and `/openapi.json`** need a sign-in too.

## 14. Single sign-on (OIDC)

Applire can sign people in through any OpenID Connect provider (Keycloak, Authentik, Zitadel, Entra ID, …). It is optional and sits next to password sign-in. Authorization-code flow with PKCE.

At your provider, create a client for Applire with the redirect URI **`<APPLIRE_BASE_URL>/api/auth/oidc/callback`**, then set in `.env`:

```env
OIDC_ISSUER=https://auth.example.org
OIDC_CLIENT_ID=applire
OIDC_CLIENT_SECRET=…
APPLIRE_BASE_URL=https://applire.example.org
#OIDC_BUTTON_LABEL=Single sign-on
```

- **https only.** `OIDC_ISSUER` and every endpoint the provider's discovery document names must be `https` (only `localhost` may use `http`). The backend refuses to start with an unusable OIDC configuration: a missing client id or secret, or an unset `APPLIRE_BASE_URL`.
- **Single sign-on binds to invited accounts.** A first-time identity is matched to a *pending, invited* account by email, and only when the provider reports that email as verified (`email_verified` is the boolean `true`). So the sequence is: an administrator invites the person by email; the person signs in with the provider instead of choosing a password. There is no automatic account creation. Existing accounts link single sign-on from Settings → Account. The account is identified afterwards by (issuer, subject), not by email.
- **Destructive actions ask for a fresh sign-in.** A person without a password confirms self-deletion or an unlink by signing in again at the provider. This needs the provider to send an **`auth_time`** claim in the ID token. A provider that sends none makes that confirmation fail closed: **an SSO-only person cannot delete their own account, and an administrator deletes it for them** (Administration → People). Nothing else is affected.
- An account cannot unlink single sign-on while it has no password (it would lock the person out).

## 15. Outgoing mail (SMTP)

Optional. With no mail configured, nothing breaks: administrators copy invite and reset links from the dialog and hand them over.

```env
SMTP_HOST=smtp.example.org
#SMTP_PORT=587
#SMTP_SECURITY=starttls        # starttls | tls | none
#SMTP_USERNAME=
#SMTP_PASSWORD=
SMTP_FROM=applire@example.org
```

**Mail requires `APPLIRE_BASE_URL`** (Section 8), set to the address people open Applire on. A link in a mail is built only from that value, never from the address a request claims to come from. Otherwise anyone could ask for a password-reset mail whose link points at their own server. With `SMTP_HOST` set and `APPLIRE_BASE_URL` unset, Applire sends **no** mail and logs a WARNING at every start. "Forgot password" then answers as usual but sends nothing. When the administrator adds a person, the dialog shows the link with the "could not be sent" notice (`mail_failed_reason: base_url_unset`).

With `SMTP_HOST` and `APPLIRE_BASE_URL` set, invitations (unless the administrator unticks the mail box when adding the person) and "forgot password" requests are mailed. The invite or reset link is always shown to the administrator as well. Mail is sent with Python's standard library; no extra service is needed. The forgot-password endpoint always answers the same way whether or not the address is known, and sends at most three mails per hour per account.

## 16. Tokens: agents and scripts

Everybody creates their own tokens under **Settings → Tokens**. A token is shown once, stored hashed, checked on every call, and can be revoked at any time; an administrator can revoke all of a person's tokens.

| Scope | Used for | How |
|---|---|---|
| **agent** | the MCP stdio server (`python -m applire.mcp`) only — acts as that person. The REST API answers it with 401. | environment variable `APPLIRE_AGENT_TOKEN` |
| **api** | scripts against the REST API only. The MCP server refuses it. | `Authorization: Bearer apl_…` instead of a login cookie |
| **monitoring** | `GET /api/ops/health` only | created by an administrator under Administration → Monitoring |

**Copy the token from the dialog, all of it.** After *Create*, the full token is shown once, in a dialog with a copy button. The token list behind it shows only the start of each token (`apl_xxxxxxxx_…`), and nothing can be read from that. A complete token is 56 characters: `apl_`, 8 lowercase letters or digits, `_`, then 43 characters. Check a stored token before you put it into a client config:

```bash
printf '%s' "$APPLIRE_AGENT_TOKEN" | grep -Eq '^apl_[a-z0-9]{8}_[A-Za-z0-9_-]{43}$' && echo "format ok" || echo "truncated or mistyped"
```

A token you lost or truncated cannot be shown again: revoke it in the list and create a new one.

A **script** needs no cookie jar:

```bash
curl -H "Authorization: Bearer apl_…" http://localhost/api/profile
```

An **MCP client** configuration gains the token (the Docker form passes it through):

```json
{
  "mcpServers": {
    "applire": {
      "command": "docker",
      "args": [
        "compose", "-f", "/absolute/path/to/applire/docker-compose.yml",
        "run", "--rm", "-e", "APPLIRE_AGENT_TOKEN", "-T", "mcp"
      ],
      "env": { "APPLIRE_AGENT_TOKEN": "apl_…" }
    }
  }
}
```

The `mcp` service is part of the shipped `docker-compose.yml` under the profile `mcp`, and has been since the first public release. A compose file you wrote or trimmed yourself may lack it. Then copy the service from the release's file, or start the server inside your running backend container instead:

```json
"args": ["compose", "-f", "/absolute/path/to/applire/docker-compose.yml",
         "exec", "-T", "-e", "APPLIRE_AGENT_TOKEN", "backend", "python", "-m", "applire.mcp"]
```

Either way, set `APPLIRE_BASE_URL` in `.env` to the address you open Applire on (Section 8), so the document links your agent receives open in your browser.

Without a valid token (missing, malformed, revoked, wrong scope, or the owner is disabled or deleted) `python -m applire.mcp` prints one line naming Settings → Tokens and exits. A revoked token is refused on its next call, and the document links it handed out stop working. Those `html_url` / `pdf_url` links are signed and expire after 60 minutes.

## 17. Settings an administrator can change without a restart

Administration → Settings lets an administrator change a short, fixed list of settings while the instance runs. There is no file to edit and no container to recreate:

| Setting | What it does |
|---|---|
| `LLM_PROVIDER`, `<PROVIDER>_MODEL`, `<PROVIDER>_API_KEY` | Which provider and model every LLM call uses, and that provider's key. Use it when a provider is slow or down. |
| `SCRAPER_FETCH_LINKEDIN_GUEST_PAGES` | Whether a LinkedIn job URL is fetched (default **on**). Off means a LinkedIn URL is refused at the web page and at the agent door with "paste the job description", and the message names this setting. Whether fetching LinkedIn's public guest pages is acceptable is your decision. |
| `RETENTION_ENABLED` | Whether the nightly GDPR clean-up deletes personal data on its schedule (default **on**). See below. |

Everything else, including provider base URLs, timeouts and the retention periods themselves, stays in `.env`.

**Which value wins.** A value set in the panel wins over `.env`. Each field shows where its value comes from ("from the environment" or "set by an administrator"), and *Reset to environment value* removes the panel value. If you edit `.env` and restart and nothing changes, check that badge first.

**When it takes effect.** The next request uses the new value. The agent (MCP) door uses it from its next tool call, and the nightly clean-up uses it from its next run. Work already running keeps the settings it started with: a CV or letter generation that started before a provider switch finishes, including its review rounds, on the provider it started with. One document is never written by one model and checked by another.

**API keys entered in the panel** are never shown again, not even partly. The panel only says whether a key is stored. The key is stored encrypted in the database, under a key derived from the instance secret. **That secret is in the same database**, so anyone who has a full database dump or a `scripts/backup.sh` archive can recover the key. Treat backups like the `.env` file, because they hold the same secrets. Database encryption is planned and not built yet. If you rotate the instance secret (Section 7), panel-entered keys can no longer be read. The dashboard then shows a critical notice, and you enter the key again.

**Dependencies.** Image uploads use Mistral for OCR (`OCR_BACKEND=mistral_vision`) whatever the LLM provider is. The panel warns when no Mistral key is available. Switching the LLM provider never removes the Mistral key.

**Every change is on the audit log** (Administration → Audit log): who, what, when, and the old and new value. Keys appear only as "changed", never with a value. A change made through `.env` is recorded at the next start.

**GDPR retention switch (`RETENTION_ENABLED`).** If the instance holds only your own data, you may not want it deleted automatically. Switching retention off suspends **only** the scheduled deletion of personal data: uploads, interview sessions, generated CVs and cover letters, unused job postings, and the inactivity clean-up of profiles, applications and accounts. It **never** suspends:
- account deletion, by the person or by an administrator;
- the deletion of a cancelled application's documents;
- the clean-up of expired sign-in links and sessions;
- background-job housekeeping;
- the orphan-file scan;
- the audit-log and usage-record age limits.

While it is off, the administration dashboard shows a permanent notice, and every nightly run records that it skipped and why. Switching it back on catches up in the next nightly run: everything past its retention period is then deleted at once. If other people keep their data on your instance, read Section 18 before you switch it off.

## 18. You are the controller of other people's data

If other people keep their CV data on your instance, you are the one who decides how long it is kept and who can reach it. The retention worker deletes data on a schedule — the five TTLs in `.env.example` (`GENERATED_DOCUMENTS_TTL_DAYS`, `CANCELLED_APPLICATION_TTL_DAYS`, `INTERVIEW_SESSION_TTL_DAYS`, `UPLOAD_TTL_DAYS`, `PROFILE_INACTIVITY_TTL_DAYS`) — and tombstones an account after `PROFILE_INACTIVITY_TTL_DAYS` of inactivity (last sign-in or write; never an administrator). The defaults did not change in 0.43. Read them, decide whether they fit the people you invited, and tell those people. Applire does not encrypt the database for you; run it on an encrypted volume if your context requires it. The retention switch in Section 17 is meant for instances that hold only your own data. While it is off, the TTLs above do not run, and the audit log and the nightly run records show for how long they did not.

## 19. What Applire does not do for you

- No automated off-host backup. `scripts/backup.sh` writes an archive; getting it off this machine (a NAS, object storage, another host) is on you.
- No alerting. `GET /api/ops/health` ([Section 12](#12-monitoring-from-outside)) tells you the state of the database, the disk, the nightly cleanup, your backups and your provider — but nothing pages you; point your own monitoring at it.
- No open sign-up and no per-person LLM key: one provider and key per instance, accounts created by an administrator (Sections 1 and 13).
- No built-in TLS (Section 8).
