# Versioning and the 1.x stability promise

Applire follows [Semantic Versioning](https://semver.org/). Releases before 1.0.0
were betas (`0.x.y-beta`) and promised nothing between versions. **From 1.0.0 on,
every 1.x release keeps the four promises below**, and CI checks the parts a
machine can check before a release can ship.

This page is for self-hosting operators and for anyone who connects an AI agent
to Applire over MCP. It answers one question: *can I update from 1.x to 1.y
without my configuration, my data or my agent integration breaking?* Yes, within
the limits stated here.

## The four promises

1. **Upgrades run forward.** Every 1.x → 1.y update is
   `docker compose pull && docker compose up -d`. It runs the database
   migrations by itself and loses no data. If a release needs anything from you,
   its [CHANGELOG](../CHANGELOG.md) entry says so under **Upgrade notes**, and
   after the update the administrator dashboard shows a version notice naming
   any new setting you have not set and any setting whose meaning changed.
2. **Your configuration keeps working.** An environment variable in
   `.env.example` is not renamed or removed within 1.x — except the variables in
   the file's last block, *Internal — no stability promise* (see below). Every new
   variable has a default, so an `.env` that worked on 1.x keeps working on 1.y
   without edits. If a variable's *meaning* has to change, the release says so in
   its upgrade notes and in the version notice.
3. **Your agent integration keeps working.** The MCP server's tools are not
   removed or renamed. A tool's parameters are not removed or renamed, an optional
   parameter does not become required, and the JSON types and enum values a
   parameter accepts do not narrow. New tools and new *optional* parameters may
   appear in any minor release.
4. **The database moves forward only.** Migrations are append-only: a migration
   that shipped is never removed or renumbered, and every new one builds on the
   previous head. There is no supported downgrade; back up before you upgrade
   ([SELF-HOSTING.md](SELF-HOSTING.md)).

`GET /health` keeps its three fields — `status`, `edition`, `version` — with
their names and meaning. `version` is the release in Python's normalised
spelling (`1.2.0`; a beta such as `0.44.0-beta` read `0.44.0b0`).

## What is *not* promised

- **Document quality.** Each release aims to write better CVs and letters; the
  wording a given model produces for a given profile can change between any two
  releases.
- **The REST API's shape.** The REST API is the web frontend's API. It is
  documented and usable from scripts, but apart from `GET /health` its routes and
  fields may change in a minor release. Automate through MCP if you need a
  stable surface. One change already landed before 1.0: the synchronous REST
  import doors `POST /api/profile/upload` and `POST /api/profile/import` were
  removed; they answer `410 Gone` with a pointer to the asynchronous door
  `POST /api/profile/import-jobs` (`202`, then poll
  `GET /api/profile/import-jobs/{import_id}`), which also takes LinkedIn
  ZIP/PDF/JSON exports.
- **What a tool returns.** The promise covers what an MCP tool *accepts*. New
  fields may appear in a tool's result in any minor release; agents should ignore
  fields they do not know. Existing result fields are kept as far as possible,
  but they are not part of the checked contract.
- **Values inside a free-form text parameter.** Where a parameter is a plain
  string whose allowed values the tool checks itself, the checked contract sees
  "a string", not the list of values.
- **Log lines, UI wording and the admin pages' layout.**
- **Internal settings** — next section.

## The internal settings

Some variables exist for the developers, not for operators: switches between
two generation pipelines, the retry and token budgets of the review loops and
the truthfulness checks, score weights. They sit in the last block of
`.env.example`, *Internal — no stability promise*. Any 1.x minor release may
rename, re-mean or remove them; a release that does lists the change in its
upgrade notes. Leave them at their defaults unless you are measuring something
and accept that your setting may stop applying after an update.

The list is declared in one place, `INTERNAL_KEYS` in
`backend/applire/settings_registry.py`.

## How a breaking change is announced

A change that breaks one of the four promises ships only in a new **major**
version (2.0.0). Its CHANGELOG entry carries an **Upgrade notes** section that
names every break and what to do about it. Nothing breaks inside 1.x on purpose;
if something does, that is a bug — please
[open an issue](https://github.com/Applire/Applire/issues).

The container images carry a major tag: `ghcr.io/applire/applire-backend:1`
(likewise `applire-frontend`, `applire-nginx`) always points at the newest 1.x
release, so pinning `:1` gets you every 1.x update and never a 2.0. `:latest`
follows the newest release of any major version.

## Supported versions

Security fixes go to the latest 1.x minor release only. See
[SECURITY.md](../SECURITY.md).

## For contributors: how the promise is checked

`tests/contract/contract.json` is a committed snapshot of the promised surface:
every MCP tool's input schema (flattened to parameter paths with their types,
whether they are required, and any enum), every settings-registry variable that is
not internal, and every migration revision. `tests/unit/test_contract_snapshot.py`
compares it with the code on every CI run:

- a **breaking** difference fails, naming what broke (a removed or renamed tool,
  parameter, variable or migration; an optional parameter made required; a new
  required parameter; a narrowed type or enum);
- an **addition** also fails until you record it — run

  ```bash
  python3 scripts/generate_contract_snapshot.py
  ```

  and commit the changed `contract.json` with your change.

The generator refuses to record a breaking difference. Its `--accept-breaking`
flag exists for the 2.0 release only; a pull request that uses it must say so.

To add something, add — don't rename: keep the old parameter or variable working
beside the new one and remove it in the next major version.
