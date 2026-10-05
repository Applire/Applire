# Contributing to Applire

Thank you for your interest in contributing to Applire! This document explains how to get involved.

## Contributor License Agreement

By submitting a pull request you agree to the [Applire Contributor License Agreement (CLA)](CLA.md).

This grants Applire the right to distribute your contribution under the AGPL-3.0 (Community Edition) and, if applicable, under a commercial license (Cloud Edition). You retain copyright in your contribution.

> **Note:** CLA signature via [cla-assistant.io](https://cla-assistant.io) will be required before your first PR is merged (coming soon).

## How to Contribute

### Reporting Bugs

Open an issue on GitHub with:
- A clear title and description
- Steps to reproduce the bug
- Expected vs actual behaviour
- Version / environment info (OS, Python version, Docker version)

### Suggesting Features

Open an issue with the label `enhancement`. Describe the use case and why it matters for DACH job seekers or self-hosters.

### Code Contributions

1. **Fork** the repository and create a branch from `main`
2. **Set up** the development environment:
   ```bash
   cp .env.example .env           # fill in your values
   docker compose up -d           # docker-compose.override.yml is picked up automatically
   ```
   Sign-in is always on, also in development. A fresh dev database prints a one-time setup code
   in `docker compose logs backend` (`grep "SETUP REQUIRED"`); open **http://localhost** (nginx on
   port 80, not `:8001` or `:3000` — the origin check compares the browser's address with the
   `Host` nginx forwards) and enter it, or claim the instance from the shell:
   ```bash
   docker compose exec backend python -m applire.admin create-admin --email you@example.org
   ```
   A development database that predates 0.43 needs one of those, or a reset with
   `docker compose down -v` (deletes the dev volumes — development only, never an install).
   The old no-login mode is **not** a dev mode any more: `AUTH_HARNESS=true` exists only for the
   CI lanes (`.env.ci`, database `applire_ci`) and the backend refuses it on any database that is not a
   throwaway test database (SQLite in memory, or a name ending in `_ci`/`_test` with no profile at boot).
   Running the frontend outside Docker (`npm run dev`): set `NEXT_PUBLIC_API_URL=` (empty) so API
   calls stay on the page's own origin and go through the Next.js rewrite to `BACKEND_URL`
   (default `http://localhost:8001`).
3. **Write tests** for any new functionality (coverage gate: ≥75%)
4. **Run the test suite** before opening a PR:
   ```bash
   # Backend unit tests
   pytest tests/unit/ -v --cov=applire --cov-fail-under=75

   # Frontend unit tests
   cd frontend && npm test

   # Frontend production build — its strict type-check is a gate of its own
   cd frontend && npm run build

   # E2E tests (requires running stack)
   npx playwright test
   ```
   If you have run `npm run dev` in this checkout, delete `frontend/.next`
   before `npm run build` — the dev server leaves a development-mode build
   directory behind, and a production build on top of it is not a clean one:
   ```bash
   rm -rf frontend/.next && cd frontend && npm run build
   ```
   An `Error: ENVIRONMENT_FALLBACK` line during *Generating static pages* is
   noise, not a failure: it is printed on a clean build too and the build still
   exits 0. Read the exit status, not the log.
5. **Follow commit conventions**: `feat:`, `fix:`, `test:`, `chore:`, `docs:`
6. **Open a pull request** against `main` — CI must pass before review

### Architecture Changes

Changes that affect the open-core boundary (`applire` vs `applire.cloud`), data retention, or GDPR scope require an ADR. Please open a GitHub discussion first to align on the approach before implementing.

## Code Style

- **Python**: Black formatting, type annotations on all new functions
- **TypeScript**: strict mode, no `any`
- **Database**: all schema changes via Alembic migrations — never raw DDL
- **MCP tools**: always async, short-lived `AsyncSession` per tool call
- **Ownership (ADR-092):** every table that holds a person's data carries `user_id`; a new route or tool depends on one of the auth dependencies in `applire/auth/deps.py`, reads owned rows through `get_owned(...)` (a foreign id is a 404, never a 403) and a job posting through `get_job_for_user(...)`. A statement on an owned table outside an owner context is refused by an engine-level guard; code that legitimately has no user (the retention worker, a script) declares `unscoped("<reason>")`. A route-inventory test fails a route that has no auth dependency and is not on its allowlist

## Development Guidelines

- `applire.cloud.*` is cloud-only — never import it from `applire.*` directly; use the `HAS_CLOUD` guard in `config.py`
- Do not add `NEXT_PUBLIC_*` env vars that reference cloud infrastructure
- **Where the data goes is the operator's choice, never ours.** Applire offers the full range — a
  local model on your own machine, an EU-resident provider, or a US one — and privileges none of
  them. Any new outbound path for user data must go through a provider abstraction the operator
  can point elsewhere (`LLM_PROVIDER`, `EMBEDDING_PROVIDER`, …); never hardwire a third-party
  service as the only option, and say in the docs where the data lands. A dependency with no
  self-hosted or EU alternative narrows that choice — raise it in an issue before adding it.

## Getting Help

- Open a GitHub issue tagged `question`
- Check existing issues and the `docs/` directory first

## Code of Conduct

This project follows our [Code of Conduct](CODE_OF_CONDUCT.md). Be kind and respectful.
