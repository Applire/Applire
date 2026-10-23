<div align="center">

![Applire](docs/images/applire_full.png)

# Applire

**The open-source, agent-ready job application tool for Europe — DACH-native first**

*Transform hours of CV tailoring into seconds. Upload your CVs, paste a job description, and let AI guide you through an intelligent interview to create perfectly matched application documents.*

[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-15+-black.svg)](https://nextjs.org/)
[![Docker](https://img.shields.io/badge/docker-ready-blue.svg)](https://www.docker.com/)
[![GitHub Stars](https://img.shields.io/github/stars/Applire/Applire?style=social)](https://github.com/Applire/Applire)

[🌐 applire.de](https://applire.de) • [🚀 Quick Start](#-installation) • [📖 Documentation](docs/) • [💬 Community](#-community--support) • [🐛 Report Bug](https://github.com/Applire/Applire/issues)

**🌐 English · [Deutsch](README.de.md)**

</div>

---

## 📸 See it in action

From a CV and a job ad to a complete application package — in minutes.

**1. Upload your CVs & paste the job ad**

![Upload your CVs and paste the job description](docs/images/screenshots/en-upload.png)

Drop in one or more CVs and add the job posting as text or URL. Applire merges them into a Master Profile, scores your fit for the role, and groups what's missing into a handful of themes.

**2. A short, targeted AI interview closes the gaps**

![A targeted AI interview that closes the gaps](docs/images/screenshots/en-interview.png)

A job-specific interview fills the gaps and sharpens your story — with editable answer starters, progress tracking, and a live role-requirements checklist on the side.

**3. Pick a template — get a tailored CV & matching cover letter**

![Choose from seven DACH-ready CV templates](docs/images/screenshots/en-templates.png)

Generate a DACH-ready Lebenslauf in seven templates (Classic German, Modern Swiss, Executive, Tech, Academic, and more) and colour variants — plus, on request, a matching cover letter (Anschreiben) in the same design, with recipient and subject auto-extracted from the job ad.

> _Screenshots use synthetic demo data (example profile "Milan Novak"). Applire ships in German and English — see the [German README](README.de.md) for German-UI screenshots. Output documents follow the job ad's language, so an English source CV becomes a German Lebenslauf for a German posting._

---

## 💡 What is Applire?

**Applire** is the open-source, agent-ready job application tool for Europe. It turns your entire career history into truthful, perfectly tailored application documents — DACH conventions built in, further countries planned as community-contributable packs.

Built for **all job seekers** — from career changers consolidating years of CV versions to international professionals adapting to German application conventions. Runs on your own hardware; your AI agent can drive it — and if you don't have one, the built-in assistant has you covered.

Unlike generic CV builders, Applire:
- 🧠 **Learns from you**: Builds a persistent Master Profile that gets smarter with every CV you upload — every claim traceable to where it came from
- 💬 **Interviews you intelligently**: Asks targeted questions to fill gaps between your experience and job requirements
- ✨ **Tailors with precision**: Generates culturally appropriate CVs optimized for DACH recruiters and ATS systems
- ✅ **Keeps you truthful**: Every job-ad keyword is classified as backed, claimable, or an honest gap — the system never claims what your profile can't back. Every generated document also ships a per-claim [truthfulness report](#-built-for-the-ai-agent-era) grading each statement against your profile
- 🤖 **Agent-ready**: Your AI assistant can run the whole loop via the Model Context Protocol (MCP)
- 🔒 **Privacy by design**: GDPR-compliant, self-hosted, full data sovereignty

**In 3 simple steps:**
1. 📄 Upload 2-4 versions of your CV
2. 🔗 Paste the job description
3. 💬 Answer a few intelligent questions → ✨ Get a perfectly tailored CV

---

## 👥 Who is Applire for?

Applire is built around two everyday problems job seekers actually have — plus a third, agent-ready way to solve them.

### 📚 "I have five versions of my CV and I'm afraid of copy-paste mistakes"
Most professionals keep several CVs — some in English, some in their native language — and every new application means copy-pasting fragments between them, rebuilding the layout, and hoping nothing important slipped through. Applire stores **every fact about your career in one place**, retrieves exactly the parts that fit a specific job, and interviews you to close the remaining gaps as well as possible — so each CV is complete, consistent, and tailored without the manual shuffle.

### 🌍 "I want to apply in the DACH region, but my CV is from somewhere else"
You have your existing CV — say, an Indian one — but how do you turn it into something a German, Austrian, or Swiss recruiter expects? Applire converts your profile into a CV fine-tuned for DACH conventions (Lebenslauf structure, expected sections, cultural signals), so you compete on equal footing.

### 🤖 "Let my AI agent handle it"
Applire is **agent-ready**. Connect your AI agent — Claude, ChatGPT, or any MCP-capable assistant — and have it run the whole loop for you interactively over the Model Context Protocol: import your CVs, analyse the job ad, fill gaps, and generate the finished CV. No UI required. And if your agent writes better than our built-in generator, good — Applire's job is to *enable* it: your agent stays the strategist, Applire provides the career vault, the gap evidence, the DACH norms, and the rendering.

---

## ✨ Key Features

### 🧠 Intelligent Master Profile

- **Multi-CV Consolidation**: Upload multiple CVs and automatically merge them into a rich, conflict-aware Master Profile
- **Additive Enrichment**: Every CV upload, interview session, and edit enriches your profile — it never overwrites, only accumulates
- **Source Tracking**: Full audit trail of where every piece of information came from
- **Conflict Resolution**: Smart detection of factual contradictions (dates, degrees) with user-controlled resolution

### 🎯 Job-First Analysis & Gap Detection

- **Deep JD Analysis**: Extracts requirements, skills, cultural signals, and industry context from job descriptions
- **Transparent Gap Scoring**: 0-100% match score with detailed explanations of what's missing
- **Categorized Gaps**:
  - **Category A** (Hard blockers): Must-have requirements you don't meet
  - **Category B** (Confirmation needed): You likely have this, but it's not stated clearly
  - **Category C** (Exploratory): Soft requirements worth discussing

### 💬 Conversational Interview Orchestrator

- **Three Modes**:
  - **Targeted** (for experienced users): Focuses on filling specific gaps identified in your profile
  - **Guided** (for new users): Systematically builds your profile section by section
  - **Profile enrichment** (no job ad needed): Improves your Master Profile on its own, outside any application
- **Stateful Backend**: Pause and resume anytime — your progress is saved server-side
- **Smart Completion**: Automatically detects when you're done or when all gaps are resolved
- **Profile Updates**: Every answer enriches your Master Profile in real-time

### 📄 CV Generation & Fine-Tuning

- **ATS-Optimized PDFs**: Generated via Playwright/Chromium with CSS-based themes
- **Live Browser Preview**: See exactly what your CV will look like before downloading
- **Section-Level Editing**: Fine-tune individual sections (introduction, positions, skills) with live re-rendering
- **Dual Save Path**: Save edits to your Master Profile (permanent) or just to this CV (one-time)
- **AI-Assisted Editing**: Optional "Let Kaile help" for targeted gap completion within the editor
- **Cover Letter Generation**: AI-powered cover letter creation based on JD and Master Profile
- **Cultural Adaptation**: Automatic detection and formatting for German, Austrian, and Swiss CV conventions

### 🗺️ DACH Cultural Intelligence

- **Market-Specific Formatting**: Lebenslauf vs. international CV formats
- **Cultural Signal Detection**: Identifies when a CV needs adaptation (e.g., Indian-format CV → German Lebenslauf conventions)
- **Multilingual Support**: German and English UI (English by default, switchable in Settings). Interview questions follow your UI language, while the generated CV and cover letter follow the language the job ad is written in — detected from the ad itself, so an English source CV becomes a German Lebenslauf for a German posting. French and Spanish planned.

### 🔒 Privacy & GDPR Compliance

- **Privacy by Design** (GDPR Art. 25): Data minimisation throughout — the LLM provider you choose is the only third party your data reaches, and Applire sends it the least it can
- **Automated Retention**: A daily worker enforces TTLs, all configurable via environment variables:
  - Uploaded files: 7 days (`UPLOAD_TTL_DAYS`)
  - Interview sessions: 30 days (`INTERVIEW_SESSION_TTL_DAYS`)
  - Generated CVs and cover letters: 90 days (`GENERATED_DOCUMENTS_TTL_DAYS`)
  - Cancelled applications: 7 days (`CANCELLED_APPLICATION_TTL_DAYS`)
  - Master Profile after inactivity: 730 days (`PROFILE_INACTIVITY_TTL_DAYS`)
- **Right to Erasure** (GDPR Art. 17): One-click full data deletion — including self-service deletion of your whole account
- **Your data is yours alone**: on an instance shared by several people, every person's vault, applications and documents are private to that person; an id that is not yours answers like one that does not exist
- **Self-Hosted**: Your data never leaves your infrastructure
- **Encryption at rest is yours to provide.** Applire adds no plaintext store of its own beyond PostgreSQL, and it does not encrypt the database for you — run it on an encrypted volume or a full-disk-encrypted host if your context requires it

---

## 🤖 Built for the AI Agent Era

Applire is the first CV tool built for **AI agents as first-class users** — under a simple doctrine: **bring your own intelligence**. Your agent is the strategist and, if it's strong, the writer; Applire supplies what an agent structurally cannot give itself:

- **State that can't silently drift** — the Master Profile is a reconciled vault where every change is recorded with its source, not a lossy notes file
- **Checks it can't fake** — deterministic keyword-coverage and ATS checks, plus `audit_document`: a per-claim document-vs-profile truthfulness audit (grounded / inflated / misattributed / unbacked / unverifiable / not applicable, with profile evidence) that also accepts documents your agent wrote itself. Limit stated openly: it verifies document ↔ profile consistency; it cannot prove the profile itself
- **A renderer it doesn't have to fight** — `render_document`: your agent's structured content (public versioned schemas, served as MCP resources) through Applire's templates and DACH norms checks — PDF plus ATS and truthfulness reports out, and Applire never rewrites your content
- **Rules it only half-remembers** — DACH application norms enforced as tested, reviewable data, not vague model recall
- **A campaign, not one document** — applications, versions, staleness, and follow-ups tracked across the whole search
- **Compounding memory** — facts surfaced in interviews land in the profile with receipts and benefit every future application. If your agent runs the interview itself, `submit_claims` records the elicited facts with agent-interview provenance — your agent asks, Applire notarises: only what the candidate actually said can land, ambiguities go to the profile Health hub instead of being silently applied

The weaker your agent's model, the more of Applire's built-in pipeline you can lean on — the generation tools remain available end-to-end.

### Model Context Protocol (MCP)
- **Seamless Integration**: First-class support for Claude Desktop, ChatGPT, Cursor, and custom AI agents
- **Agent-supplied documents**: Agents can ingest CVs (base64-encoded PDF, with a plain-text fallback) and job descriptions (raw text **or** a URL scraped server-side) directly over stdio — no UI required
- **Stateful Sessions**: Agents can pause, resume, and recover from interruptions via a stable `flow_id`
- **Flow Orchestrator**: Guides agents through the correct sequence (JD analysis → CV import → gap analysis → interview → generation)
- **Data-minimal by default**: Write and ingest tools return summaries and receipts, not the profile — `import_cv` reports what it extracted rather than echoing the vault back. The full profile is available when an agent actually needs it, deliberately and by name: `get_profile()` and the `profile://current` resource
- **Async Generation**: Non-blocking CV generation with polling-based status checks

### REST API
- **Full HTTP API**: Programmatic access for remote integrations
- **OpenAPI Documentation**: Interactive Swagger UI at `/docs`

### Agent Workflow Example
```bash
# Start MCP server (stdio transport) — needs your personal agent token
APPLIRE_AGENT_TOKEN=apl_… python -m applire.mcp

# A typical agent session:
1. start_flow()                              → flow_id  (stable recovery handle)
2. import_cv(file_base64="<base64 PDF>")     → profile summary
3. analyze_jd(url="https://.../job-posting") → job_id
4. analyze_gaps(job_id)                      → gap_report
5. run_interview(job_id)                     → session_id + first question
6. send_message(session_id, "I have 5 yrs…") → next question / {complete: true}
7. generate_cv(job_id)                       → cv_id  (async)
8. get_cv_status(cv_id)                      → {status: "ready", pdf_url: "…"}
9. create_application(job_id)                → application logged to pipeline
```

---

## 🏗️ Architecture & Tech Stack

### Backend

- **Python 3.12+**: Modern async Python with type hints
- **FastAPI**: High-performance async web framework
- **PostgreSQL 16**: JSONB for flexible Master Profile schema
- **Pydantic**: Type-safe data validation and serialization
- **SQLAlchemy 2.0**: Async ORM with full type support
- **Alembic**: Database migrations

### Frontend

- **Next.js 15**: React framework with App Router
- **TypeScript**: Type-safe JavaScript
- **ShadCN/UI**: Accessible component library
- **Tailwind CSS v4**: Utility-first styling

### AI/ML

- **Bring your own key**: You choose the LLM provider and supply the API key — your data goes only where you point it
- **LLM Provider Abstraction**: Pluggable backends — Mistral (EU-hosted), Requesty (EU-hosted gateway), OpenRouter, Anthropic (Claude, BYO-API-key), OpenAI (or any OpenAI-compatible endpoint), and Ollama (fully offline, self-hosted)
- **Custom State Machine**: Async interview orchestrator (no LangGraph dependency)
- **Playwright**: Headless Chromium for PDF generation

### Infrastructure

- **Docker & Docker Compose**: Containerized deployment
- **PostgreSQL 16**: Primary database with JSONB support
- **Retention Worker**: Daily cron for GDPR TTL enforcement
- **GitHub Actions**: CI/CD pipeline with pytest and Playwright E2E tests

### Agent Integration

- **Model Context Protocol (MCP)**: stdio transport for local AI agents
- **REST API**: Full HTTP API for remote integrations
- **Flow Orchestrator**: State machine for multi-step agent workflows
- **Session Recovery**: Agents can resume interrupted sessions via `flow_id`

---

## 🚀 Installation

### Prerequisites

- **Docker & Docker Compose**
- **About 8 GB of free disk space and 2 GB of RAM** (allow 4 GB of RAM if several people share the instance). The four images take about 5.2 GB once pulled: the backend alone is about 4 GB because it carries the Chromium that renders your PDFs, plus about 0.6 GB for PostgreSQL, 0.4 GB for the frontend and 0.1 GB for nginx. An update keeps the old images until you prune them ([docs/SELF-HOSTING.md](docs/SELF-HOSTING.md#9-disk-and-pruning)). The stack idles near 0.5 GB of RAM; every PDF render starts a headless Chromium on top of that. Running a local model with Ollama needs more of both.
- **An LLM provider of your choice** (bring your own key): Mistral, Requesty, OpenRouter, Anthropic, OpenAI (or any OpenAI-compatible endpoint), or Ollama (local/free, no key needed)

### Self-hosting (no clone required)

> **Which version does this install?** These commands always fetch the **newest published
> release** — the same one Docker's `:latest` resolves to, so the compose file, the env
> template and the images are one matching set, with no version number for you to look
> up. This page describes the *development* version; for documentation that matches what
> you just installed, open the
> [latest release](https://github.com/Applire/Applire/releases/latest).

```bash
# 1. Download the two files you need, straight from the newest release
curl -LO https://github.com/Applire/Applire/releases/latest/download/docker-compose.yml
curl -L -o .env.example https://github.com/Applire/Applire/releases/latest/download/env.example

# 2. Configure your environment
cp .env.example .env
# Edit .env: set LLM_PROVIDER and the matching API key (see below and Configuration)

# 3. Fetch the images, then start all services.
#    The explicit `pull` matters: `up -d` on its own reuses an older `:latest`
#    that is already cached locally. Database migrations run automatically on
#    backend startup — there is no separate migration step.
docker compose pull && docker compose up -d
```

Every service — including the reverse proxy, whose config is baked into the `applire-nginx` image — is a pre-built image, so `docker compose pull` fetches a complete, working stack with no config files to place on the host.

**The provider step in three lines.** In the `.env` you just copied, these are the only lines you have to set. Any provider from the Configuration list below works; Mistral is shown as an example, not a recommendation:

```env
LLM_PROVIDER=mistral
MISTRAL_API_KEY=your-key-here
MISTRAL_MODEL=mistral-small-latest
```

For another provider, change the prefix: `LLM_PROVIDER=openrouter` goes with `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`, and so on for `requesty`, `anthropic` and `openai`; `ollama` needs no key (`OLLAMA_MODEL` only). The model line is optional — every provider has a default — and [docs/llm-models.md](docs/llm-models.md) helps you choose. You do not need to read the rest of `.env.example` to start.

Access the application at **http://localhost** — the bundled nginx reverse proxy serves the frontend and routes `/api/*` to the backend. Port 80 is the only one you need to publish; the backend and frontend containers stay internal. For the full entry-point and port topology, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Check that it is up.** `docker compose up -d` returns before the backend has finished starting (the database migrations run first; give the first start about a minute). Then ask the backend through nginx:

```bash
curl -s http://localhost/health
# {"status":"ok","edition":"community","version":"…"}
```

`"status":"ok"` means the backend is serving. `version` is the release you installed, in Python's normalised spelling: `0.44.0-beta` reads `0.44.0b0`, and `1.0.0` reads `1.0.0`. The endpoint is open and returns only these three fields; the compose healthcheck uses the same one.

> **First start: claim your instance.** Applire always asks for a sign-in. A new install opens a setup screen that asks for a one-time code, which the backend prints in its log:
> ```bash
> docker compose logs backend | grep -o "enter: [A-Z2-7-]*" | tail -n 1
> ```
> It prints one line, `enter: XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`; the code is the part after `enter:`. Every start prints a new code and only the newest works, which is why the command keeps the last line. Enter the code with your email and a password (12+ characters) — you are the administrator. (Or skip the screen: `docker compose exec backend python -m applire.admin create-admin --email you@example.org`.) Add the other people who should use this instance under **Administration → People**; there is no open sign-up. **Upgrading from 0.42 or earlier?** The same screen appears once, and your existing data becomes the administrator's account — read the [upgrade notes](CHANGELOG.md) first. Several people can share one instance, each with their own private vault; details, single sign-on and mail in [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).
>
> **What you will see.** The screens follow your browser's language: German if your browser prefers German, English otherwise. Switch under **Settings → Language**. When you generate your first CV, a photo step comes before the template choice if your profile has no photo yet (you can skip it).
>
> **Behind your own proxy or over https?** Forward the `Host` header unchanged (or set `APPLIRE_BASE_URL`), and set `COOKIE_SECURE=true` once people reach Applire over https.

> **Custom domain or TLS?** The image ships a sensible default proxy config. To override it, bind-mount your own file over the baked one — add to the `nginx` service in `docker-compose.yml`:
> ```yaml
>     volumes:
>       - ./my-nginx.conf:/etc/nginx/conf.d/default.conf:ro
> ```

To update to the latest release:
```bash
docker compose pull && docker compose up -d
```

What a version number promises, and what may change between releases: [Versioning and stability promise](docs/versioning.md).

> **Never `docker compose down -v`.** The `-v` flag deletes the named volumes — `postgres_data` (the vault) and `applire_uploads` (every uploaded CV and profile photo) — and there is no undo. `docker compose down` on its own is safe and keeps both. To update, `pull` and `up -d` as above; you never need to take the stack down first. Back up before you upgrade: [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).

> **Updating from a release older than `v0.37.0-beta`?** Step through
> `v0.37.2-beta` first. Profiles imported before the reconciliation engine
> (E035) can hold flat duplicate employers and orphaned projects, and the
> one-time `scripts/migrate_flat_duplicates.py` pass that folds them into the
> typed model shipped only in `v0.37.0-beta` … `v0.37.2-beta`. It is data
> hygiene, not schema — Alembic migrations still run automatically on startup,
> so a direct jump upgrades cleanly, it just leaves those duplicates in place.

### Self-hosting from source

Prefer to build what you run (or can't reach GHCR)? Clone the repo and build the same
three images locally, then start the same production topology:

```bash
git clone https://github.com/Applire/Applire.git && cd Applire
cp .env.example .env   # set LLM_PROVIDER and the matching API key

docker build -t ghcr.io/applire/applire-backend:latest ./backend
docker build --target runner -t ghcr.io/applire/applire-frontend:latest ./frontend
docker build -t ghcr.io/applire/applire-nginx:latest ./nginx

docker compose -f docker-compose.yml up -d
```

> **Note the explicit `-f docker-compose.yml`.** Inside a clone, a plain
> `docker compose up -d` also applies `docker-compose.override.yml` — the
> *development* stack (hot-reload servers, source bind mounts, extra published
> ports including the database). Great for hacking on Applire, not for serving
> real users.

> **Contributing?** See [CONTRIBUTING.md](CONTRIBUTING.md) for the build-from-source developer setup.

### 💾 Back up

Two named volumes hold everything: `postgres_data` (the vault — profile, applications, generated documents) and `applire_uploads` (uploaded CVs and profile photos). A backup that covers only one of them is not a backup.

```bash
# Back up the database and the uploads volume into ./backups
scripts/backup.sh

# Check an archive without restoring it
scripts/backup.sh --verify backups/applire-backup-<timestamp>.tar.gz

# Restore into a fresh stack
scripts/restore.sh backups/applire-backup-<timestamp>.tar.gz
```

Installed from the release assets without cloning? The scripts live in the repository, not in the release assets. Fetch them once, before you need them:

```bash
# Installed from the release assets without cloning? Fetch both scripts once, from the
# same release your images come from, into the folder that holds docker-compose.yml:
TAG=$(curl -fsSL https://api.github.com/repos/Applire/Applire/releases/latest | grep -o '"tag_name": *"[^"]*"' | cut -d'"' -f4)
mkdir -p scripts
for f in backup.sh restore.sh; do
  curl -fsSL -o "scripts/$f" "https://raw.githubusercontent.com/Applire/Applire/$TAG/scripts/$f"
done
chmod +x scripts/backup.sh scripts/restore.sh
```

Full runbook — backup, restore, secrets, TLS, disk, troubleshooting: [docs/SELF-HOSTING.md](docs/SELF-HOSTING.md).

A backup you have never restored is a hope, not a backup — restore it once, on purpose, before you need to.

---

## ⚙️ Configuration

### Environment Variables

Applire is **bring-your-own-key**: pick any supported provider and supply its key — your data goes only to the provider you choose. The authoritative list of every setting Applire reads, complete with its default and the release it first appeared in, is generated straight from the code into [`.env.example`](.env.example); the block below is a shorter excerpt of the ones most installs touch. Copy `.env.example` to `.env` and configure:

```env
# Database
DATABASE_URL=postgresql+asyncpg://applire:applire@postgres:5432/applire

# LLM Provider — choose one: mistral | requesty | openrouter | anthropic | openai | ollama
LLM_PROVIDER=mistral

# Mistral AI — EU-hosted, strong German proficiency
MISTRAL_API_KEY=your-mistral-api-key-here
MISTRAL_MODEL=mistral-medium-latest

# Requesty — EU-hosted gateway (Frankfurt); also an EU-resident path to Claude/GPT/Gemini
REQUESTY_API_KEY=your-requesty-api-key-here
REQUESTY_MODEL=mistral/mistral-medium-3-5   # EU-region model for full residency

# OpenRouter — multi-model gateway: one key for Mistral, Claude, and more (not EU-hosted)
# Get a key at https://openrouter.ai/keys
OPENROUTER_API_KEY=your-openrouter-api-key-here
OPENROUTER_MODEL=mistralai/mistral-medium-3

# Anthropic (Claude) — native API, BYO-API-key only (a Claude subscription cannot be used)
# Model names move fast: take a current id from Anthropic's model list rather than
# copying one from a README. See docs/llm-models.md for how to choose.
ANTHROPIC_API_KEY=your-anthropic-api-key-here
#ANTHROPIC_MODEL=<current-claude-model-id>

# OpenAI or any OpenAI-compatible server (e.g. LM Studio)
OPENAI_API_KEY=your-openai-api-key-here
#OPENAI_MODEL=gpt-4o
#OPENAI_BASE_URL=http://host.docker.internal:1234/v1

# Ollama — fully offline (docker compose --profile ollama up)
# Pull a model once before the first run — the server starts empty:
#   docker compose exec ollama ollama pull llama3.2      # or set OLLAMA_MODEL to the model you pulled
# CPU-only inference is slow: raise LLM_TIMEOUT (e.g. 600) so generation is not cut off.
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=llama3.2

# LLM timeout in seconds (raise for reasoning models)
LLM_TIMEOUT=180

# Sign-in — built-in accounts. (The old value "none" now also means "local";
# login is always on. If your .env still says AUTH_PROVIDER=none, delete the line.)
AUTH_PROVIDER=local

# Set true as soon as Applire is served over https; leave false on plain http
COOKIE_SECURE=false

# Public address people open Applire on (needed behind a proxy that does not forward
# Host, for OIDC, and for the links in invitation mails)
#APPLIRE_BASE_URL=https://applire.example.org

# Optional single sign-on (https issuer) and optional outgoing mail:
#OIDC_ISSUER=
#OIDC_CLIENT_ID=
#OIDC_CLIENT_SECRET=
#SMTP_HOST=

# CORS — comma-separated list of allowed origins
# The browser app is same-origin and needs nothing here; sign-in cookies are
# never sent cross-origin.
#CORS_ORIGINS=*

# The reverse proxy's read timeout is 300s, baked into the applire-nginx image.
# Keep LLM_TIMEOUT below it, or bind-mount your own nginx config to raise it.

# Frontend API URL
# docker compose: leave empty — nginx at :80 routes /api/* to the backend
# standalone dev: set to http://localhost:8001
#NEXT_PUBLIC_API_URL=http://localhost:8001
```

### LLM Provider Options

Applire is bring-your-own-key — no provider is privileged. Pick whichever fits your needs and supply the matching key via a pluggable abstraction layer:

| Provider | Configuration | Use Case |
|----------|---------------|----------|
| **Mistral AI** | `LLM_PROVIDER=mistral`<br>`MISTRAL_API_KEY=...` | EU-hosted, strong German proficiency |
| **Requesty** | `LLM_PROVIDER=requesty`<br>`REQUESTY_API_KEY=...` | EU-hosted gateway (Frankfurt, zero-retention); an EU-resident path to Claude/GPT/Gemini via EU-region deployments |
| **OpenRouter** | `LLM_PROVIDER=openrouter`<br>`OPENROUTER_API_KEY=...` | Multi-model gateway; access Mistral, Claude, and others with one key (not EU-hosted) |
| **Anthropic** (Claude) | `LLM_PROVIDER=anthropic`<br>`ANTHROPIC_API_KEY=...` | Claude via a Console API key (BYO-key) — a Claude Pro/Max subscription cannot be used. US-hosted |
| **OpenAI** | `LLM_PROVIDER=openai`<br>`OPENAI_API_KEY=...` | High quality, widely available; also supports LM Studio via `OPENAI_BASE_URL` |
| **Ollama** (local) | `LLM_PROVIDER=ollama`<br>`OLLAMA_BASE_URL=http://localhost:11434` | Fully offline, no API costs, no key required |

---

## 📖 API Documentation

### REST API

In the Docker stack the REST API is reached through nginx at `http://localhost/api/*`; the interactive Swagger UI is available when running the backend standalone in development. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the entry-point and port topology.

#### Authentication

Every route except `GET /health` and the sign-in/setup pages needs a sign-in. A browser uses the session cookie; a **script** uses a personal **API token** (create it under Settings → Tokens; an *agent* token is for MCP only and the REST API refuses it) instead of a cookie jar:

```bash
curl -H "Authorization: Bearer apl_…" http://localhost/api/profile/exists
# {"exists":false,"completeness_score":0.0}   (true and a score once a CV is imported)
```

`GET /api/profile` returns the whole profile and answers 404 ("No profile found") until the first CV is imported; write the path without a trailing slash, because `/api/profile/` is redirected (307).

`GET /health` is open and returns `status`, `edition` and `version` only. Instance health and the detail fields are at `GET /api/ops/health` (administrator or monitoring token). `/docs` needs a sign-in.

#### Core Endpoints

```bash
# Job Description Analysis
POST /api/job/analyze
{
  "text": "Senior Software Engineer role...",
  "url": "https://example.com/job"  # Optional
}

# CV Upload & Profile Enrichment (async job — poll until status is "ready" or "failed")
POST /api/profile/import-jobs
Content-Type: multipart/form-data
file: cv.pdf          # one document per job; post again for a second CV or a LinkedIn export
GET  /api/profile/import-jobs/{job_id}

# Gap Analysis (session-scoped)
POST /api/session/{session_id}/analyze-gaps

# Start Interview Session
POST /api/session
{ "job_id": "uuid", "mode": "targeted" }

# Send Interview Message
POST /api/session/{session_id}/message
{ "message": "I have 5 years of experience with Python..." }

# Generate CV
POST /api/cv/generate
{ "job_id": "uuid", "template": "classic_german", "target_pages": 2 }
# template and target_pages are optional; target_pages falls back to your
# settings, then the region standard

# Check CV Generation Status
GET /api/cv/{cv_id}/status
# Returns: { "status": "pending" | "ready" | "failed" }

# Download CV
GET /api/cv/{cv_id}/pdf
```

### Model Context Protocol (MCP)

**Connect your agent — copy-paste config.** Most MCP clients (Claude Desktop,
Claude Code, Cursor, …) take an `mcpServers` block. For a Docker install, point
it at the `docker-compose.yml` you installed from (use its absolute path); the
client then starts the MCP server as a short-lived container next to your
running Applire, over stdio:

```json
{
  "mcpServers": {
    "applire": {
      "command": "docker",
      "args": [
        "compose", "-f", "/absolute/path/to/applire/docker-compose.yml",
        "run", "--rm", "-T", "-e", "APPLIRE_AGENT_TOKEN", "mcp"
      ],
      "env": { "APPLIRE_AGENT_TOKEN": "apl_…" }
    }
  }
}
```

**The server needs your personal agent token.** Create one in Applire under
**Settings → Tokens** (it is shown once: use the **Copy** button in the dialog and
paste it straight into the `env` entry — the list afterwards shows only the first
characters), and the agent works on *your* data only. A complete token has the form
`apl_<8 characters>_<43 characters>`, 56 characters in all (the 8 are lowercase
letters or digits); a shorter one was cut off and must be replaced — a token cannot
be shown again, so revoke it and create a new one. Without a valid token `python -m applire.mcp`
refuses to start and says where to create one. A source checkout takes the same
variable: `APPLIRE_AGENT_TOKEN=apl_… python -m applire.mcp`.

The `mcp` service in that command is defined in the `docker-compose.yml` of the
release, under `profiles: [mcp]`, so a plain `docker compose up -d` never starts it.
A compose file that predates it, or one you copied and trimmed by hand, lacks it:
download the current one from the release assets
(`curl -LO https://github.com/Applire/Applire/releases/latest/download/docker-compose.yml`,
the same command as in the installation above; it replaces your file, so copy over any edits of your own) and keep your `.env`.

`-T` matters: it gives the server a plain pipe instead of a terminal, which is
what the stdio transport needs. Start Applire first (`docker compose up -d`);
the MCP container shares its database and your `.env`. The document links your
agent gets back point at `http://localhost` by default, which works when the
agent runs on the same machine as Applire. Otherwise set `APPLIRE_BASE_URL` in
that `.env` to the address people open Applire on (for example
`http://192.168.1.5` or `https://applire.example.org`). Once set, it is also the
only host name, besides `localhost`, that Applire accepts sign-ins on.

For a source checkout, run the server directly instead:

```bash
# Start MCP server (stdio transport)
python -m applire.mcp
```

`APPLIRE_BASE_URL` is what `generate_cv`/`get_cv_status`/`generate_cover_letter`
use to build `html_url`/`pdf_url`. It defaults to `http://localhost` (Applire's
own nginx on port 80), which is right only when the agent runs on the server
itself; the server logs a startup warning when it's unset. A source checkout
that runs the backend outside Docker on port 8001 sets
`APPLIRE_BASE_URL=http://localhost:8001`. See `.env.example`.

> **First call:** after connecting, have your agent call `get_guide` — it returns
> the agent-usage guide and Applire's honesty contract (tool flow, à-la-carte
> paths, grounding rules). The canonical file is
> [`backend/applire/mcp/AGENT_GUIDE.md`](backend/applire/mcp/AGENT_GUIDE.md).

#### MCP Tools

**Guidance**

| Tool | Description |
|------|-------------|
| `get_guide()` | Returns the agent-usage guide + honesty contract — call before your first application run |

**Ingestion & profile**

| Tool | Description |
|------|-------------|
| `import_cv(file_base64?, filename?, text?)` | Seed or extend the Master Profile from a CV. Primary: base64-encoded PDF (≤10 MB); fallback: pre-extracted text. Returns an extraction summary (never the raw profile) |
| `analyze_jd(text?, url?)` | Analyze a job description. Provide exactly one of `text` (JD body) or `url` (scraped server-side); reposts of jobs already in the pipeline carry a `duplicate_of` hint |
| `get_profile()` | Return the current Master Profile |
| `update_profile(section, data)` | Patch one section (`personal_info`, `professional_summary`, `work_experience`, `education`, `certifications`, `skills`, `languages`, `publications`, `volunteer_activities`, `signature_stories`) |
| `add_role(title, company, start_date, location?, industry?, close_role_ids?)` | Add a new ongoing role (post-hire update); `close_role_ids` closes prior open roles |

**Flow & interview**

| Tool | Description |
|------|-------------|
| `start_flow(job_id?)` | Create or resume a flow session (idempotent per user+job); returns `flow_id` + state |
| `advance_flow(flow_id, step, artifact_id?)` | Advance to the next step; artifact-producing steps require `artifact_id` |
| `get_flow_state(flow_id)` | Get current flow state and available actions |
| `analyze_gaps(job_id)` | Detect gaps between profile and JD |
| `run_interview(job_id)` | Start a gap-fill interview; returns `session_id` + first question |
| `send_message(session_id, message)` | Send a message in an active interview; returns next question or `{complete: true}` |
| `resolve_gap(job_id, gap_id, answer)` | Resolve ONE gap cluster in a single call — the agent-channel form of the UI's targeted gap fill. Stateless: for agents that would rather ask their own questions than run a full interview session. `gap_id` comes from `analyze_gaps`' `gap_clusters` |

**Built-in generation**

| Tool | Description |
|------|-------------|
| `generate_cv(job_id, target_pages?)` | Initiate async CV generation; optional `target_pages` pins the page count for this run; returns `cv_id`, `html_url`, `pdf_url` |
| `get_cv_status(cv_id)` | Poll CV generation status (`pending` / `generating` / `ready` / `failed`) |
| `get_cv_ats_report(cv_id)` | Persisted ATS audit report for a generated CV — named pass/fail checks + present/missing keywords, no aggregate score — plus `truthfulness`: the truthfulness check's verdict counts and `stop_and_fix` (the review panel lists an open finding — a flagged claim or an unbacked keyword; do not send before resolving it) |
| `generate_cover_letter(job_id, recipient_name?, recipient_company?, salary?, availability?, motivation?, tone?)` | Generate a cover letter (requires an existing flow session for the job); the optional inputs are the user's own words, exactly as in the web form — the letter never invents a notice period or a reason for the change; returns `cover_letter_id`, `html_url`, `pdf_url` |
| `get_cover_letter_status(cover_letter_id)` | Poll cover-letter generation status (`pending` / `generating` / `ready` / `failed`) |
| `get_cover_letter_ats_report(cover_letter_id)` | Persisted ATS audit report for a generated cover letter, with the same `truthfulness` summary |

**Bring your own intelligence (ADR-054) — à la carte, no prior `generate_*` call needed**

| Tool | Description |
|------|-------------|
| `submit_claims(claims, job_id?)` | Submit facts your agent elicited from the candidate as *itemized* claims; Applire reconciles them into the profile with agent-interview provenance (agent = interviewer, Applire = notary). Contract: `schema://claims` |
| `submit_testimony(text)` | Reconcile ONE whole free-text testimony document into the profile with receipts — the unstructured counterpart to `submit_claims`. Contract: `schema://testimony` |
| `audit_document(document_id?, document_text?)` | Truthfulness Oracle: per-claim truthfulness report — for a generated document by id, or raw text of a document your agent wrote itself |
| `render_document(document_kind, content, job_id, template?, target_pages?)` | Render your agent-authored structured content (contracts: `schema://cv` / `schema://cover-letter`) into a norms-checked, templated PDF with ATS + truthfulness reports — never rewritten |

**Applications**

| Tool | Description |
|------|-------------|
| `create_application(job_id, start_workflow?, company_name?, role_title?, deadline?, source_url?)` | Log an application to the pipeline; `start_workflow=true` atomically creates the flow session |
| `list_applications(status_filter?)` | List the application pipeline (`tracking`, `applied`, `rejected`, `offer`) |
| `get_application(application_id)` | Get details for a specific application (incl. a `stale_cv` re-tailor hint) |
| `update_application(application_id, ...)` | Update user-managed fields (status, notes, deadline, source URL), pin the submitted CV/letter, or dismiss the stale-CV hint |

#### MCP Resources

- `profile://current` — Current Master Profile (JSON)
- `job://{job_id}` — Job analysis
- `cv://{cv_id}` — Generated CV metadata
- `flow://{flow_id}` — Flow session state
- `schema://cv` — Versioned tailored-CV content contract for `render_document` (`{schema_version, json_schema}`)
- `schema://cover-letter` — Versioned cover-letter content contract for `render_document`
- `schema://claims` — Versioned itemized-claims contract for `submit_claims`
- `schema://testimony` — Versioned free-text testimony contract for `submit_testimony`
- `guide://usage` — The agent-usage guide + honesty contract (same content as `get_guide`)

---

## 🧪 Testing

### Backend Tests

```bash
# Run all tests
pytest

# Run with coverage (enforces ≥75% threshold)
pytest --cov=applire --cov-fail-under=75

# Generate HTML coverage report
pytest --cov=applire --cov-report=html
```

### Frontend Tests

```bash
# Run unit tests
npm test

# Run E2E tests (Playwright)
npm run test:e2e

# Run E2E tests in UI mode
npm run test:e2e:ui
```

### CI/CD Pipeline

GitHub Actions runs, against a mocked LLM provider throughout:
1. ATS round-trip render guarantee (Playwright)
2. Backend unit tests (pytest, ≥75% coverage)
3. Backend integration tests (Docker stack)
4. MCP stdio tests (the agent channel)
5. Playwright IQ, OQ (desktop + mobile), and PQ suites — Chromium
6. Frontend unit tests (Vitest), lint (ESLint + i18n parity), and a production build
7. A module-system check

All tiers must pass before merge.

---

## 📁 Project Structure

```
Applire/
├── backend/
│   ├── applire/
│   │   ├── main.py              # FastAPI application entry point
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── routers/             # FastAPI route handlers
│   │   ├── services/            # Business logic layer
│   │   │   ├── interview/       # Interview Orchestrator (state machine)
│   │   │   ├── flow/            # Flow Orchestrator
│   │   │   ├── profile/         # Master Profile merge logic
│   │   │   ├── cv/              # CV generation & section editing
│   │   │   └── gap/             # Gap analysis
│   │   ├── providers/           # LLM, Auth, Storage abstractions
│   │   ├── mcp/                 # Model Context Protocol server
│   │   ├── retention/           # GDPR retention worker
│   │   └── templates/           # Jinja2 CV templates
│   ├── alembic/                 # Database migrations
│   ├── tests/                   # Pytest test suite
│   └── requirements.txt
├── frontend/
│   ├── app/                     # Next.js App Router pages
│   ├── components/              # React components
│   ├── lib/                     # Utilities and API clients
│   └── public/
├── docs/
│   ├── TESTING.md               # Testing strategy and commands
│   └── CI_CD_GUIDE.md           # CI/CD pipeline documentation
├── tests/                       # Integration and E2E tests
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 🗺️ Roadmap

### ✅ Current Release (v0.44.0-beta)

- [x] Pipeline v2 is the default for CV and cover letter: the AI writes the whole document, and deterministic code only checks it. Findings go back into one review loop; nothing that passed review is deleted or rewritten after the last model call (`CV_PIPELINE` / `LETTER_PIPELINE=v1` switch back)
- [x] Profile content — skills, education, industry, language levels, recorded name forms — is written in the document's language, and the cover letter is told its language and checked for it: on the document-quality bench, mixed-language text fell by about 55 per 100 documents on the reference model and by about 315 on a small model
- [x] The cover letter fits one page by measuring its own render, ends with a call to action instead of a list of keywords, never turns an interview answer that only held back detail into an admission, and names a lack only when you recorded it and the posting asks for it
- [x] Every generation prompt carries a short pipeline context and a reasoning field the model fills first and Applire strips before storage (`PROMPT_CONTEXT`)
- [x] The truthfulness check no longer grades a sentence as backed when it names a standard your profile never mentions, and the health hub finds skill durations marked as your own words that Applire actually calculated
- [x] The interview shows when an answer was not recorded, no longer stores "I can't give more detail" as a denial, and no longer records taking part in something as the skill the question asked about
- [x] Choose how your photo is framed, once, for every template and the Word file

### ⏳ Next Up

Applire ships in dessert-named releases, each tracked as a public [milestone](https://github.com/Applire/Applire/milestones) — follow along on the [blog](https://applire.de/en/blog/):

- [x] **Spaghettieis** — shipped with v0.38.0-beta (milestone closed, 27 issues). Parallel applications became first-class: an application dashboard with status tracking, one-click re-tailoring across multiple jobs, refreshed job-ad analysis, and better progress feedback on long-running steps
- [x] **Tiramisu** — shipped with v0.39.0-beta. **Truthfulness Oracle**: every generated document ships with a deterministic truthfulness report — is each claim grounded in your profile, is every number backed, did a "targets 70%" quietly become "achieved 70%"? In the UI and as the `audit_document` MCP tool, which also audits documents your agent wrote itself; likewise **`render_document`** (your agent's own content through Applire's norms-checked renderer, never rewritten), **`submit_claims`** / **`submit_testimony`** (agent-run interviews landing in the profile with receipts) and **`resolve_gap`**. The flavour closed on evidence *selection*: one write path into the profile, and a review loop whose verdict covers the document as composed
- [x] **Stracciatella** — shipped with v0.41.1-beta. Felix takes the controls: choose the leading document language per application (detection becomes a default, not a law), structured Master-Profile editors replacing the raw-JSON view, and pinning must-appear facts to a document — plus hardening of the delivered documents (the v0.39 ship-gate findings and the prompt-injection defence)
- [x] **Nougat** — shipped with v0.42.0-beta. The self-hosting operator: a version-jump notice for settings a release introduced that your `.env` does not set (with upgrade notes per release), a published qualification matrix of which models are good enough for Applire's prompts, an ops and health layer with token tracking — plus the consolidation of the Stracciatella findings (the proven delivered-document bugs and the review-loop rulings)
- [x] **Strawberry** — shipped with v0.43.0-beta. Multi-user capability: built-in accounts and optional single sign-on, roles, an administration area for people, personal tokens for agents and scripts, and every person's data private to them
- [ ] **Hazelnut** — build 1 shipped with v0.44.0-beta, the flavour continues. Document quality: the AI writes the whole CV and cover letter, and deterministic code only checks and blocks instead of rewriting the text after the last model call, measured on a document-quality bench (language mix, underselling, duplicates, upselling)

Beyond that, without dates: **country packs beyond DACH** as a community contribution surface. The hosted demo and **Applire Cloud (SaaS) are paused** while we focus on the open-source core and the agent channel — the [waitlist](https://applire.de) hears first when that changes.

### 🔭 Future Vision

- [ ] **Mock Interview Preparation**: AI-powered practice sessions with role-specific questions
- [ ] **Career Path Advisory**: Skill gap analysis and training recommendations
- [ ] **Job Search & Recommendation**: Curated job suggestions based on Master Profile
- [ ] **MCP Marketplace Listings**: Distribution via agent marketplaces

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. **Fork the repository**
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Commit your changes**: `git commit -m 'feat: add amazing feature'`
4. **Push to the branch**: `git push origin feature/amazing-feature`
5. **Open a Pull Request**

### Development Guidelines

- Follow **PEP 8** for Python code (enforced by `black`)
- Use **TypeScript** for all frontend code (strict mode, no `any`)
- Write **tests** for new features (≥75% backend coverage)
- Keep commits **atomic** and use [Conventional Commits](https://www.conventionalcommits.org/)
- All schema changes go through **Alembic migrations** — never raw DDL

### Code Style

```bash
# Backend: Format with Black
black .

# Frontend: Lint with ESLint
npm run lint
```

### Contributor License Agreement

By submitting a pull request you agree to the [Applire CLA](CLA.md). This allows us to maintain the open-core model while keeping the Community Edition fully open-source. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## 🤖 How Applire is built (AI disclosure)

Applire is built with an AI coding assistant: Claude by Anthropic, used through Claude Code. The roles are split like this:

- **Tobias Rosenbaum, the founder, is product owner and software architect.** He decides what Applire does and which architecture decisions stand. He does not review the code line by line. His checks are a ruling on every architecture decision record Claude drafts, an acceptance test on his own instance, and the approval of each release.
- **Claude does the design and the programming.** It proposes the technical design and the user interface, and writes almost all of the code and the documentation, this README included.

Almost every commit carries a `Co-Authored-By: Claude` line.

---

## 💛 Support the project

Applire is built part-time by a solo founder and stays fully open source (AGPL-3.0, no feature held back). Every feature and every release is checked against **real LLM providers** before it ships — adversarial passes, blind hiring-panel reviews, agent-channel journeys — and Applire is built in public together with AI coding agents. Sponsorship pays for exactly that: the API credits, the AI coding tools, and the infrastructure behind applire.de and the release images.

If Applire saves you an evening of CV tailoring, consider **[sponsoring the project on GitHub](https://github.com/sponsors/Applire)**. Sponsors are named in the release notes and here in the README, and every release post states what sponsorship paid for in that cycle.

---

## 💬 Community & Support

### Get Help

- 🌐 **[applire.de](https://applire.de)** — Website, [blog](https://applire.de/en/blog/) (English and German), and beta waitlist
- 📖 **[Documentation](docs/)** — Testing, CI/CD, and architecture guides
- 🐛 **[GitHub Issues](https://github.com/Applire/Applire/issues)** — Report bugs and request features
- 💬 **[GitHub Discussions](https://github.com/Applire/Applire/discussions)** — Ask questions and share ideas

---

## 📄 License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see the [LICENSE](LICENSE) file for details.

### Why AGPL?

We chose AGPL to ensure that:
- ✅ **The software remains free and open source** — Always accessible to everyone
- ✅ **Modifications must be shared** — Even when used as a service (SaaS)
- ✅ **The community benefits** — All improvements flow back to the project
- ✅ **Your privacy is protected** — Full transparency in how your data is processed
- ✅ **No vendor lock-in** — You control your data and infrastructure

### Commercial Licensing

For organizations that cannot comply with AGPL requirements (e.g., proprietary SaaS offerings), commercial licenses are available. Contact **kontakt@applire.de** for details.

---

## 🙏 Acknowledgments

- **Mistral AI** for EU-hosted LLM infrastructure
- **FastAPI** and **Next.js** communities
- All contributors and early adopters
- DACH industry professionals who provided domain expertise
- The open-source community for inspiration and tools

---

## 📬 Contact

- **Website**: [applire.de](https://applire.de) — [blog](https://applire.de/en/blog/) and beta waitlist
- **Email**: kontakt@applire.de
- **Issues**: [GitHub Issues](https://github.com/Applire/Applire/issues)
- **Security**: kontakt@applire.de (see [SECURITY.md](SECURITY.md))

---

<div align="center">

**Built with ❤️ for job seekers in the DACH market**

*Open source. Privacy-first. Agent-ready. Truthful by design.*

[⭐ Star us on GitHub](https://github.com/Applire/Applire)

</div>
