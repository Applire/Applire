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
├── tests/                       # Integration + E2E tests
├── docs/
│   ├── ARCHITECTURE.md          # Architecture decisions (start here)
│   ├── TESTING.md               # Test strategy and commands
│   └── CI_CD_GUIDE.md
├── docker-compose.yml           # Full stack (postgres, backend, frontend, nginx, retention)
├── .env.example                 # Environment template — copy to .env
└── AGENTS.md                    # This file
```

---

## Contributing Workflow

1. **Create a feature branch**: `git checkout -b feature/my-change` (never commit directly to `main`).
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
- E2E tests run against Chromium AND Firefox.
- All JavaScript/TypeScript uses ES modules (`"type": "module"`). Never `require()` in tests.

---

## Code Conventions

```
Python:     Black formatting, type annotations on all new code
TypeScript: strict mode, no `any`
Commits:    Conventional commits — feat:, fix:, test:, chore:, docs:
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
| **Felix** | Detail-oriented user who reads every line | Section-level editing, live preview, AI assist on demand |
| **Kaile** | AI agent calling Applire via MCP/API | Structured tools, deterministic flow, session recovery via `flow_id` |

The Jason (recruiter/headhunter) persona is a Cloud/B2B concern — do not surface it in Community features or documentation. (The former "Dr. Weber" pharma segment was removed entirely in 2026-06 — do not reintroduce industry-specific positioning.)

---

## What to Escalate

Flag these to the product owner before proceeding:

- Any change to the `master_profiles` JSONB schema that would break existing enrichment history.
- Any proposal to move interview or flow logic to the frontend.
- Any dependency on `applire.cloud.*` from this repo.
- Any new Cloud-only feature request (should live in the Cloud repo, not here).
- Any change to the `VALID_TRANSITIONS` dict in the Flow Orchestrator (requires ADR or ADR amendment).
- Any new model that holds PII without `expires_at` or `deleted_at`.
