# Applire — Agent Primer

This file is the starting point for any AI agent (Claude Code, OpenCode, Cursor, etc.) working on this codebase. Read it before writing a single line of code.

---

## What is Applire?

Applire is **the open-source, agent-ready job application tool for Europe** (AGPL-3.0, DACH-native first), built as an open-core product. The community edition (this repo) is a fully functional self-hosted application. A managed Cloud Edition (proprietary, separate repo) exists as a design; its buildout is currently paused (see `docs/ARCHITECTURE.md` §5).

**Core loop:** User provides a job description + one or more CVs → AI conducts a targeted interview to fill gaps → platform generates a culturally adapted, ATS-optimised PDF.

**Three first-class consumers:** human users (browser), AI agents (MCP), developers (REST API). All user-facing features must be accessible via MCP.

**Tech stack:** Python 3.12 / FastAPI / PostgreSQL 16 / Next.js 15 / Tailwind / ShadCN. Full architectural details in `docs/ARCHITECTURE.md`.

> **Scope note — two agent audiences.** This file is for agents working *on* the codebase. Agents *driving Applire over MCP* (the end-user channel) get their guidance from `backend/applire/mcp/AGENT_GUIDE.md`, served at runtime via the `get_guide` tool (ADR-056). If you change the MCP surface, keep that guide in sync — CI enforces that every registered tool name appears in it.

---

## Before You Touch Code

### 1. Read the architecture document

`docs/ARCHITECTURE.md` explains the *why* behind every major design decision. The decisions there are **not negotiable without a new ADR**. Key invariants:

- JD-First: job description analysis drives all downstream logic.
- Stateful backend: interview and flow logic lives server-side.
- Accumulate, don't overwrite: the Master Profile only grows richer.
- CV preview uses `<iframe srcDoc=...>`, never `<iframe src=...>` (Firefox CSP).
- All CI tests mock LLM providers — never call real APIs in tests.
- `NEXT_PUBLIC_API_URL` is empty in Docker Compose — frontend uses relative paths.

### 2. Understand the Community / Cloud boundary

This repo is Community Edition only. Cloud code lives in a separate private repository under `applire.cloud.*`. **Never add `applire.cloud.*` imports here.** Cloud-only endpoints return HTTP 402 — that is correct behaviour, not a bug.

Edition detection is import-based:
```python
from applire.edition import HAS_CLOUD_EDITION
```

---

## Repository Layout

```
applire-core/
├── backend/
│   └── applire/
│       ├── main.py              # FastAPI app entry point
│       ├── constants.py         # All TTLs, thresholds, edition flags
│       ├── edition.py           # HAS_CLOUD_EDITION detection
│       ├── auth/                # AuthProvider ABC + NoAuthProvider
│       ├── providers/           # LLM, OCR, Storage factories
│       ├── routers/             # FastAPI route handlers
│       ├── services/
│       │   ├── flow/            # Flow Orchestrator (VALID_TRANSITIONS)
│       │   ├── interview/       # Interview Orchestrator + signals
│       │   ├── profile/         # Master Profile merge logic
│       │   ├── cv/              # CV generation, section editor
│       │   └── gap/             # Gap detection
│       ├── models/              # SQLAlchemy ORM models
│       ├── schemas/             # Pydantic request/response schemas
│       ├── mcp/                 # MCP server (stdio transport)
│       ├── retention/           # GDPR retention worker
│       └── templates/           # Jinja2 CV HTML templates
├── frontend/
│   ├── app/                     # Next.js App Router pages
│   ├── components/
│   │   └── cv/CVPreview.tsx     # CV preview — always use srcDoc
│   └── lib/                     # API clients, utilities
├── tests/                       # unit/ integration/ iq/ oq/ pq/ fixtures/ — see docs/TESTING.md
│   ├── unit/                    # pytest, no Docker
│   ├── integration/             # pytest, full Docker stack (mock LLM by default)
│   ├── iq/                      # Playwright — Installation Qualification (stack up + reachable)
│   ├── oq/                      # Playwright — Operational Qualification (pages vs. mocked API)
│   ├── pq/                      # Playwright — Performance Qualification (persona journeys, one dir per persona)
│   ├── fixtures/                # Sample CVs, JDs, downloads
│   └── test_iter*.py            # Legacy per-iteration API tests
├── docs/
│   ├── ARCHITECTURE.md          # Architecture decisions (start here)
│   ├── TESTING.md               # Test strategy, vocabulary, and commands
│   └── CI_CD_GUIDE.md
├── docker-compose.yml           # Full stack (postgres, backend, frontend, nginx, retention)
├── .env.example                 # Environment template — copy to .env
└── AGENTS.md                    # This file
```

---

## Contributing Workflow

1. **Create a feature branch**: `git checkout -b feat/<kebab-name>` (e.g. `feat/master-profile-health`) — never commit directly to `main`.
2. **Plan before coding** — for non-trivial work, write a plan and confirm before implementing.
3. **Reference ADRs** — if a decision has a relevant ADR in `docs/ARCHITECTURE.md`, follow it. If you need to deviate, flag it and propose a new ADR.
4. **Test as you go** — unit tests for new backend logic, Playwright tests for new user journeys.
5. **All CI tests must pass** before the branch is ready for review.

---

## Architecture Rules

These are hard constraints. Do not work around them.

### Backend

| Rule | Where it applies |
|---|---|
| All schema changes via Alembic migrations — never raw DDL | Any database change |
| Every model with PII must carry `expires_at` or `updated_at` + `deleted_at` from migration 0 | New models |
| Interview + flow logic stays server-side | Services layer |
| One active `interview_session` per `(user_id, job_id)` | `POST /api/session` must be idempotent |
| One `flow_session` per `(user_id, job_id)` — unique constraint enforced at DB level | Flow orchestrator |
| Steps that produce artifacts require `artifact_id` in `AdvanceFlowRequest` | Flow transitions |
| LLM calls go through the `LLMProvider` abstraction — never instantiate a provider SDK directly | Any LLM usage |
| Auth goes through the `AuthProvider` abstraction | Any auth check |
| `applire.cloud.*` is never imported here | Everywhere |
| Edition-gated features return HTTP 402 in Community | Cloud-only endpoints |

**Flow Orchestrator — linear DAG:** `jd_analysis → cv_import → gap_analysis → interview → cv_generation → complete`. Returning users skip `cv_import`; a user with a job but no CV takes `jd_analysis → interview` instead (skipping `cv_import` + `gap_analysis`). Full state-machine semantics — the gap-driven interview offer, the no-CV onboarding path, DB-arbitrated concurrent gap-analysis kickoffs — are in `docs/ARCHITECTURE.md` (ADR-016).

### Frontend

| Rule | Where it applies |
|---|---|
| CV preview uses `<iframe srcDoc=...>` — never `<iframe src=...>` | `CVPreview.tsx` and any new preview |
| TypeScript strict mode — no `any` | All frontend code |
| `NEXT_PUBLIC_API_URL` is empty in Docker Compose — use relative API paths | `fetch()` calls |

---

## Key Commands

```bash
# Start full stack
docker compose up -d

# Run database migrations
docker compose exec backend alembic upgrade head
# or standalone:
cd backend && alembic upgrade head

# Backend unit tests (no Docker)
pytest tests/unit/ -v --cov=applire --cov-fail-under=75

# Backend integration tests (spins up Docker stack)
pytest tests/test_iter*.py -v

# Full LLM integration test (real API key required)
INTEGRATION_LLM=1 pytest tests/integration/test_happy_path.py -v

# Frontend unit tests
cd frontend && npm test

# E2E tests (requires running stack)
npx playwright test
npx playwright test --headed   # headed mode for debugging
npx playwright show-report

# Frontend dev server (standalone)
cd frontend && npm run dev      # http://localhost:3000

# Backend dev server (standalone, requires DB)
cd backend && uvicorn applire.main:app --reload --port 8001

# Run MCP server (stdio transport)
python -m applire.mcp
```

---

## LLM Provider Configuration

Set in `.env` (copy from `.env.example`):

```env
LLM_PROVIDER=openrouter          # mistral | requesty | openrouter | anthropic | openai | ollama
OPENROUTER_API_KEY=your-key      # get one at openrouter.ai/keys
```

EU-resident options: `mistral` (EU-hosted) or `requesty` (EU endpoint, also an EU path to Claude/GPT/Gemini). `anthropic` is BYO-API-key only — a Claude subscription cannot be used. For fully offline use: `LLM_PROVIDER=ollama` and `docker compose --profile ollama up`.

---

## Testing Rules

- **Coverage gate:** ≥75% backend unit coverage — enforced by CI (`--cov-fail-under=75`).
- **Mock all LLM providers in tests** — unit and integration tests must never call real APIs.
- Unit tests run without Docker (`tests/unit/conftest.py` sets up an in-memory SQLite DB).
- Integration tests use a real Docker Compose stack — they spin it up automatically.
- E2E runs Chromium only (`chromium` + `mobile-chromium` projects). Firefox is installed in CI but no suite targets it, so there is no cross-browser gate.
- All JavaScript/TypeScript uses ES modules (`"type": "module"`). Never `require()` in tests.
- **Vocabulary is three axes, not one ladder** — kind (Unit · Integration · IQ · OQ · PQ — *what a test proves*) × environment (CI stack · local dev stack · edge) × driver (CI · a subagent · a human). E2E is an **umbrella** over IQ+OQ+PQ, not a fourth kind. UAT is not a CI concept at all. `OQ` here means *pages against a mocked API* (Playwright + `page.route()`), not module-interface testing. Full definitions: `docs/TESTING.md` → *Vocabulary — three axes, not one ladder*.
- **Frontend tests: run `./node_modules/.bin/vitest run` (or `npm test`) from `frontend/` — never `npx vitest`.** `npx` resolves a different, cached rolldown build that intermittently fails to parse valid `.tsx` (non-deterministic flakiness easily misread as a broken build). Tell: `transform 0ms / tests 0ms` with the error path running under `_npx/<hash>/…/rolldown`.
- **Run `npm run build` from `frontend/` before pushing any `.ts`/`.tsx` change.** The Next.js production build runs a strict type-check that lint and Vitest do not — a strict-null mismatch can pass every other gate and fail only the `Frontend Production Build` CI job.
- **Any literal text node in JSX fails the build** — ESLint rule `formatjs/no-literal-string-in-jsx`, enforced in both the `Frontend Lint` and `Frontend Production Build` CI jobs (Vitest does not catch it). Decorative glyphs count too (`→`, `·`, `—`). Fixes: user-facing strings become `next-intl` keys added to **both** `messages/en.json` and `messages/de.json` (parity is enforced); decorative punctuation goes through a lucide icon (e.g. `ArrowRight`) or a function call (`[...].join(" · ")`) instead of a bare JSX child. A string literal as a function *argument* is fine — only literals rendered as JSX children/text trip the rule.
- **Provider stubs in tests must absorb the full provider-ABC signature.** `acomplete`/`aparse_json` gain kwargs over time; a stub with an explicit signature (not `**kwargs`) raises `TypeError` the instant a service threads a new kwarg through, and the stubs are scattered enough that it surfaces one test file at a time. Prefer `**kwargs` in new stubs; when the ABC changes, grep `tests/` for `def acomplete` / `def aparse_json` and update every explicit stub in one pass.
- **Template-render changes** (new required Jinja context, a new `.render(`/`get_template` call) must run `pytest tests/ats/test_roundtrip.py` before pushing — `tests/unit` does not render templates the production way. Every Jinja environment comes from `applire.templates.filters.build_template_env`; `tests/unit/test_dach_conventions.py` fails the build on any hand-rolled `Environment(`.

---

## Code Conventions

```
Python:     Black formatting, type annotations on all new code
TypeScript: strict mode, no `any`
Commits:    Conventional commits — feat:, fix:, test:, chore:, docs:
CI gates:   Never skip pre-commit hooks (--no-verify) or bypass CI gates
Migrations: Always via Alembic — never raw DDL
MCP tools:  Always async, short-lived AsyncSession per tool call
Copyright:  Add AGPL-3.0 header to every new Python/TS/JS file
            (copyright: Tobias Rosenbaum)
```

AGPL-3.0 file header (Python):
```python
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
```

AGPL-3.0 file header (TypeScript/JavaScript):
```typescript
// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
```

---

## Key Constants

All tunable values live in `backend/applire/constants.py` and are backed by environment variables. Check this file before hardcoding any threshold:

| Constant | Default | Purpose |
|---|---|---|
| `MODE_B_COMPLETENESS_THRESHOLD` | 0.3 | Score below which interview uses Guided mode |
| `INTERVIEW_HARD_CEILING_TARGETED` | 12 | Max questions in Targeted mode |
| `INTERVIEW_HARD_CEILING_GUIDED` | 20 | Max questions in Guided mode |
| `INTERVIEW_MAX_QUESTIONS_PER_GAP` | 3 | Max follow-ups per gap (env-var backed) |
| `UPLOAD_TTL_DAYS` | 7 | Retention: uploaded files |
| `INTERVIEW_SESSION_TTL_DAYS` | 30 | Retention: interview sessions |
| `GENERATED_DOCUMENTS_TTL_DAYS` | 90 | Retention: generated CVs (human channel) |
| `PROFILE_INACTIVITY_TTL_DAYS` | 730 | Retention: user profile inactivity threshold |

---

## Personas Reference

Understanding these helps you make the right product decisions:

| Persona | Who they are | What they need |
|---|---|---|
| **Marcus** | Experienced professional, any industry | Precision tailoring, efficiency, no hand-holding |
| **Priya** | International candidate relocating to DACH | Cultural "translation" of career history, German CV norms |
| **Felix** | Detail-oriented user who reads every line (the finetuner) | Section-level editing, live preview, AI assist on demand |
| **Emma** | Returning power user, existing profile already on file | One-click parallel tailoring of that profile against several new jobs — retention, highest lifetime value |

The Jason (recruiter/headhunter) persona is a Cloud/B2B concern — do not surface it in Community features or documentation. (The former "Dr. Weber" pharma segment was removed entirely in 2026-06 — do not reintroduce industry-specific positioning.)

**Kaile is a channel, not a persona** — the AI agent driving Applire over MCP/API, not a human archetype. It needs structured tools, a deterministic flow, and session recovery via `flow_id`. Agent-facing guidance (served at runtime via the `get_guide` MCP tool) lives in `backend/applire/mcp/AGENT_GUIDE.md` — see the scope note at the top of this file.

---

## What to Escalate

Flag these to the product owner before proceeding:

- Any change to the `master_profiles` JSONB schema that would break existing enrichment history.
- Any proposal to move interview or flow logic to the frontend.
- Any dependency on `applire.cloud.*` from this repo.
- Any new Cloud-only feature request (should live in the Cloud repo, not here).
- Any change to the `VALID_TRANSITIONS` dict in the Flow Orchestrator (requires ADR or ADR amendment).
- Any new model that holds PII without `expires_at` or `deleted_at`.
