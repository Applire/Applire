# Changelog

All notable changes to Applire are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.36.2-beta] – 2026-06-10

Bug-fix release driven by a full Milan-persona QA run (English CVs + German job ad,
real LLM) that exercised the ADR-038 document-language path end-to-end.

### Fixed
- **Cover-letter date is now system-injected.** The prompt asked the LLM for "today's
  date", which it cannot know — every letter was dated "10. Oktober 2023". The date now
  comes from the server clock, formatted per document language ("10. Juni 2026" /
  "10 June 2026") with locale-independent month names.
- **Document language is routed deterministically** (ADR-038 amended). The cover letter
  resolved its language from `language_requirement` — an LLM-extracted *candidate*
  requirement (e.g. "Bilingual DE/EN"), which misrouted a German job ad to an English
  letter — and CV tailoring had no language input at all. A new `job_analyses.jd_language`
  column (migration 0032) stores the language the job ad is *written in*, detected in code
  by a dependency-free stopword/umlaut scorer; both document generators route on it, with
  raw-text fallback for pre-migration rows.
- **CV tailoring now translates.** The hallucination guards ("keep facts EXACTLY as
  provided") read as a translation ban, so work-history bullets stayed in the source-CV
  language. The prompt (v3) carries an explicit `OUTPUT LANGUAGE` directive and states
  that translating prose is required and is not invention; proper nouns, dates, and
  metrics stay unchanged.
- **Interview answers no longer duplicate positions.** An answer mentioning a known
  employer by a shortened name ("TWENTYONE" vs "TWENTYONE Digital") with a loose title
  created a spurious undated position. The interview enrichment path now shares the
  CV-upload merge's fuzzy employer matching: known employers are enriched in place
  (bullets accumulate as achievements, differing titles become role aliases); only dated,
  non-overlapping stints or new employers create new entries. Answer bullets — previously
  discarded on match and stored under a schema-invisible key on append — now land in
  `achievements` in both cases.
- **A null CV summary no longer fails the whole generation.** The tailoring LLM
  occasionally returns `"summary": null`; this now degrades to an empty section instead
  of a validation error requiring a manual retry.
- **The sidebar version label matches the release.** It was frozen at v0.31.0-beta
  (read from `frontend/package.json`, which releases never bumped). Release images now
  bake the GitHub tag in via an `APP_VERSION` build arg (release workflow → Dockerfile →
  `next.config.ts`); dev builds fall back to `package.json`, now kept in sync.

### Changed
- Database schema: migration 0032 adds nullable `job_analyses.jd_language` — applied
  automatically on backend startup, no manual action needed.

## [0.36.1-beta] – 2026-06-09

Documentation-only patch release — no runtime code changes.

### Changed
- **Public docs audited and repositioned.** README reframes LLM providers as
  bring-your-own-key (no advertised default; "EU-hosted" kept as a neutral note on
  Mistral), rewrites "Who is Applire for" around use cases (multi-version CV, DACH
  conversion, agent-first), corrects the CV template list to the 7 real templates,
  bumps the roadmap to v0.36.0-beta, and notes the conversation-vs-document language
  split (ADR-038).
- `docs/ARCHITECTURE.md` is now the canonical home of the port topology (new port
  table); ADR-009 summary made provider-neutral; temperature defaults grounded in code.
- GitHub org move completed: GHCR images now publish under `ghcr.io/applire/*` and all
  repo links point to `Applire/Applire` (compose files, release CI, badges,
  issue templates, `package.json`).

### Fixed
- Self-host quickstart actually works again: download `nginx/self-hosted.conf` and
  access the app via `http://localhost` (backend/frontend are internal-only).
- Corrected the gap-analysis REST path in the README to `/api/session/{id}/analyze-gaps`.
- Removed dangling references to `docs/TRACEABILITY.md` (TESTING.md) and `docs/mcp.md`
  (docker-compose.yml).
- `backend/pyproject.toml` version brought back in sync with the release tag (the
  0.36.0-beta tag shipped with the version still reading 0.35.1-beta).

## [0.36.0-beta] – 2026-06-09

### Fixed
- **Interview & enrichment questions now follow the UI language** (ADR-038, US137).
  Previously the gap-interview and profile-enrichment questions drifted to whatever
  language the input material was in — e.g. an English-UI user who pasted a German job
  description was interviewed in German, because the JD-language `jd_context` was injected
  into the question prompt with no output-language directive. Questions and their answer
  choices are now generated in the user's `ui_language` regardless of the profile/JD/context
  language. The split is explicit: **conversation** (interview/enrichment questions) follows
  the **UI language**; **documents** (tailored CV, cover letter) continue to follow the
  **job-description language**.

### Added
- `with_language()` directive applied to all question system prompts (MODE A targeted,
  MODE B guided, follow-up probes, Mode C enrichment), forcing the output language and
  instructing the model never to mirror the context language.
- Language-verification pass over generated questions, reusing the ADR-021
  `review_and_refine` loop (`prompts/review_question_language.py`), gated by
  `INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES` (default 1; `0` disables).

### Changed
- `UserSettings.ui_language` is now **non-nullable, default `en`** and is the single
  authoritative source for conversation language (resolved via `get_ui_language()`).
- Removed the now-dead `Accept-Language` auto-detect branch in `GET /api/settings`; the
  settings response `ui_language` is now non-optional.

### Migration
- Alembic migration 0031: backfills `user_settings.ui_language` NULL → `'en'`, then sets
  the column `NOT NULL` with server default `'en'`. No data loss; reversible.

## [0.35.1-beta] – 2026-06-02

### Fixed
- **UI color-scheme flash on load** (#30). The active palette is now injected server-side
  onto `<html>` during SSR, so the first paint already uses it. Previously the static
  `globals.css` default rendered first and the client `ThemeProvider` swapped in the DB
  scheme a few frames later — a visible flash.
- Corrected the mislabeled **"EU Blue"** built-in scheme: it now carries the canonical
  Continental Excellence EU palette (`#003399` / `#fecb00`) instead of an unrelated
  murky slate-teal. "GNOME Blue" is unchanged — its name accurately describes its palette.

### Migration
- Alembic migration 0030: re-colors the EU Blue built-in scheme in place (idempotent
  `UPDATE` by id); fixes both fresh and existing databases. No schema change.

## [0.35.0-beta] – 2026-06-02

### Changed
- **Match Score is now computed deterministically** (ADR-035). The LLM classifies each
  job requirement as `direct` / `partial` / `gap`; Python computes the score as a weighted
  ratio (required = 1.0, nice-to-have = 0.5). The headline percentage can no longer disagree
  with the gap categories, is reproducible (same inputs → same score), and is explainable.
- Recalibrated interview score bands: ≥ 60% (minor gaps), 40–59% (moderate), < 40% (low fit).

### Added
- `requirement_breakdown` field on gap analyses — stores `{requirement, source, status,
  slot, earned, reason}` per requirement for explainability and audit.

### Fixed
- Admin appearance editor no longer blacks out the UI on exit; saturation ceiling lowered
  (88 → 50) for legible palettes.
- nginx re-resolves upstreams to avoid stale-DNS `502`s after container rebuilds (self-hosting).
- Patched moderate XSS advisory in the `postcss` transitive dependency (#47).

### Migration
- Alembic migration 0029: `match_score` becomes nullable; adds `requirement_breakdown` column.
  Existing rows keep their stored scores and are not retroactively recomputed.

## [0.34.0-beta] – 2026-06-01

### Added
- **Continental Excellence design system** — Manrope type, glassmorphism, gold-pill accents,
  and AI-card light-leak styling across the app.
- Unified application shell: `AppTopbar` with section / detail / flow modes, sidebar user-strip,
  and an Admin nav entry; `/admin` and `/match` now share the shell sidebar.
- Reworked CV workspace: `CVPageActionBar` (Download · Anschreiben · Eingestellt · Weiter),
  `RefinementHeader` with a match-score ring and expiry chip, and a 2-tab `RefinementPanel`.
- **MCP agent channel expansion** — new tools `get_cv_status` (US048), `start_flow` /
  `advance_flow` / `get_flow_state` with a `flow://` resource (US109), `create_application`
  (US110), `import_cv` (US107), `add_role` (US108), and JD-URL scraping in `analyze_jd` (US056);
  CV now renders inline over stdio.
- `frontend-lint` CI job enforcing ESLint + de/en i18n key parity on every push.

### Changed
- Full i18n sweep: remaining hard-coded JSX strings replaced with `t()` calls, a11y aria-labels
  localized, and de/en catalog parity enforced via `eslint-plugin-formatjs`.

### Fixed
- Flow advance is now idempotent; stop over-drilling a gap the candidate already declined.
- Each gap maps to a single CV section; refinement panel de-duplicated.
- Master-Profil tiles derive from real profile data; in-app back/home actions route to `/dashboard`.
- Static assets (template thumbnails, favicon) served reliably in the standalone runner.

### Security
- Upgraded FastAPI 0.115.6 → 0.136.3 to patch Starlette CVEs.
- Patched `ws` and `brace-expansion` advisories via npm audit; bumped Vitest 1.6 → 4.1.

## [0.33.1-beta] – 2026-05-18

### Added
- **Post-hire flow** — "Mark as Hired" action on the dashboard, a `hired` user status, and a
  `POST /applications/{id}/mark-hired` endpoint; `application_id` exposed in the flow state.
- **Add-role / profile-update flow** — `ProfileUpdateChooser` and `AddRoleView` (manual entry,
  JD-paste, multi-role close-out, and pre-fill from an application), backed by
  `POST /api/profile/roles`; new `profileUpdate` i18n namespace.
- Retry-refinement prompts for CV extraction, profile extraction, CV tailoring, and response
  parsing — reviewers now quote source text in feedback.

### Fixed
- CI builds multi-arch images on native runners to avoid the 6-hour emulation timeout.

## [0.32.0-beta] – 2026-05-15

> Consolidates the self-hosting hardening line (tags `v0.32.0-beta`–`v0.32.4-beta`).

### Added
- **Self-hosting reverse proxy** — `applire-nginx` image with baked-in config plus a
  pull-based `docker-compose.yml` for self-hosters and a build-from-source
  `docker-compose.override.yml` for development.
- Release pipeline now publishes `applire-backend`, `applire-frontend`, and `applire-nginx`
  images to GHCR.

### Fixed
- API routing and CORS now work for non-localhost deployments.
- Made the env file optional with a `DATABASE_URL` default for platform deployments;
  renamed `.env.dev` → `.env`.

### Changed
- Bumped `next` 15.3.6 → 15.5.18, `next-intl` 4.9.1 → 4.9.2.

## [0.31.0-beta] – 2026-05-13 (First public release)

### Added
- AI-powered CV tailoring for the DACH job market (Germany, Austria, Switzerland)
- CV section editor with smart gap analysis and interview preparation
- Job description URL ingestion with skill extraction
- Cover letter generation
- Multilingual UI (de/en) via next-intl
- CV export to PDF with multiple color profiles and templates
- Photo management (upload, crop, remove)
- LLM review layer with OpenRouter / Mistral AI support
- Comprehensive CI/CD pipeline (GitHub Actions, GHCR)
- Docker Compose setup for self-hosting
- Offline mode with service worker
- MCP server integration (Kaile agent channel)
- AGPL-3.0 Community Edition open-source release

### Tech Stack
- Backend: FastAPI 0.115, Python 3.12, SQLAlchemy 2, Alembic
- Frontend: Next.js 15.2, React 19, TypeScript 5, Tailwind CSS 4
- AI: OpenRouter (multi-model), Mistral AI, MCP tool integration
- Database: SQLite (dev), PostgreSQL (prod)

[Unreleased]: https://github.com/Applire/Applire/compare/v0.35.0-beta...HEAD
[0.35.0-beta]: https://github.com/Applire/Applire/compare/v0.34.0-beta...v0.35.0-beta
[0.34.0-beta]: https://github.com/Applire/Applire/compare/v0.33.1-beta...v0.34.0-beta
[0.33.1-beta]: https://github.com/Applire/Applire/compare/v0.32.4-beta...v0.33.1-beta
[0.32.0-beta]: https://github.com/Applire/Applire/compare/v0.31.2-beta...v0.32.4-beta
[0.31.0-beta]: https://github.com/Applire/Applire/releases/tag/v0.31.0-beta
