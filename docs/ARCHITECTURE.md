# Applire — Architecture Reference

**Audience:** Contributors, self-hosters, AI agents working on the codebase.

This document explains the *why* behind Applire's key architectural choices. It is a condensed public version of the internal arc42 + ADR documentation. When in doubt about a design decision, this document is the reference.

---

## 1. System Overview

Applire is a JD-driven CV tailoring platform with three first-class consumers:

| Consumer | Entry Point | Auth |
|---|---|---|
| Human (browser) | Next.js frontend → nginx → FastAPI | `NoAuthProvider` in Community (single-user) |
| AI Agent (local) | MCP stdio server (`python -m applire.mcp`) | `NoAuthProvider` in Community |
| Developer | REST API via nginx (`/api/*`); Swagger `/docs` in standalone dev | `NoAuthProvider` in Community |

The core workflow is always: **JD analysis → CV import → Gap analysis → Interview → CV generation**.

---

## 2. Design Principles

Every architecture decision traces back to one or more of these principles. If a proposed change violates them, it needs a new ADR.

| Principle | What it means in practice |
|---|---|
| **JD-First** | Job description drives all downstream logic. No tailoring without a JD. |
| **Stateful Backend** | Complex reasoning lives server-side. The frontend is a thin UI. Never move interview or flow logic to the client. |
| **Accumulate, Don't Overwrite** | The Master Profile grows richer over time. New data enriches it; it is never replaced. |
| **Provider Abstraction** | Auth, LLM, Storage, and OCR are all pluggable via the same factory pattern. Community ships sensible defaults; Cloud overrides without touching Community code. |
| **GDPR by Design** | Every model that stores personal data carries `expires_at` or `updated_at` + `deleted_at` from the start. Retention is automated, not manual. |
| **MCP Agent Parity** | All user-facing features must be accessible as MCP tools. Agents are a first-class consumer, not an afterthought. |
| **Open Core Discipline** | Community code (`applire.*`) and Cloud code (`applire.cloud.*`) live in separate repositories. They never mix at the source level. |

---

## 3. Architecture Decisions

### ADR-001 — JD-First Intake & Analysis

**Decision:** JD analysis happens before CV parsing. Everything downstream is informed by the JD.

**Scraping tiers:**
1. httpx + BeautifulSoup (fast, most sites)
2. Playwright/Chromium (JS-heavy sites)
3. Manual paste fallback (login-gated or hostile sites — we deliberately skip server-side scraping of these)

**Why:** Higher tailoring quality. Extracting JD requirements first lets gap detection and interview questioning be precise rather than generic. Tiered scraping maximises URL success rate without legal risk (no LinkedIn scraping).

**Consequence to watch:** Playwright adds ~400MB to the Docker image. This is intentional and shared with the PDF generation pipeline.

---

### ADR-002 — Master Profile & JSONB Persistence

**Decision:** One `master_profiles` row per user, with the full profile stored as PostgreSQL JSONB in `profile_json`. 1:1 relationship (`users` ↔ `master_profiles`).

**Why JSONB:** The profile schema evolves rapidly (new sections, sub-fields, metadata). JSONB avoids migration churn for schema additions while enabling structured queries via JSON operators. PostgreSQL 16 is required; SQLite is used only in unit tests (via `JSONB().with_variant(JSON(), "sqlite")`).

**Why 1:1:** Multi-user tenancy is Cloud-only (ADR-011). The Community Edition is single-user by design, and the data model reflects this cleanly.

**Conflict handling:** True conflicts (e.g., contradicting `start_date` for the same job) are stored in `profile_json.metadata.pending_conflicts` and must be resolved via `POST /api/profile/conflicts/{id}/resolve`. They are never auto-resolved.

---

### ADR-004 — Stateful Backend for Interview Orchestration

**Decision:** All interview state is stored in the `interview_sessions` table. The backend owns the conversation loop via a custom 4-node async state machine:

```
GapDetector → QuestionGenerator → ResponseParser → ProfileUpdater
```

**Orchestration (see ADR-049):** the state machine is hand-rolled (no framework) — and stays that way. An interim decision (ADR-045) to move to a declarative graph substrate was superseded before implementation: profile reconciliation collapsed into a single LLM call + deterministic applier (ADR-046) and reviewer loops live inside the bounded review layer (ADR-021), so no cyclic graph remained to orchestrate. ADR-049 instead unifies all interview-shaped flows into one in-house engine. Hard rule unchanged: **LLM calls stay on the provider abstraction (ADR-009)** — so providers stay pluggable and mockable. The stateful-backend, pause/resume, and one-active-session invariants below are unchanged.

**Two modes:**
- **MODE A (Targeted):** User has profile data. Focuses on filling specific gaps from gap analysis. 3–12 questions.
- **MODE B (Guided):** New user, no CV. Builds the profile section by section. 10–20 questions.

Mode is auto-detected at session creation from `completeness_score` vs `MODE_B_COMPLETENESS_THRESHOLD` (0.3), but can be overridden.

**Key invariant:** One active session per `(user_id, job_id)`. `POST /api/session` is idempotent — returns the existing session with `resumed: true` if one exists.

**Termination (issue #259, amended 2026-07-24):** the "3–12" / "10–20" question counts above are a **cost guard**, not the primary termination driver. The interview ends when the first of these fires: (1) **sufficiency** — every JD-critical concept is evidenced, explicitly denied (terminal, never re-asked), or triaged as a true gap (`services/interview/sufficiency.py`, deterministic, no LLM call); (2) the operator-configured **budget** is exhausted (`INTERVIEW_MAX_QUESTIONS_TARGETED` / `INTERVIEW_MAX_QUESTIONS_GUIDED` env vars, read via `config.Settings`, defaulting to the previous hardcoded 12/20); or (3) the user explicitly says "done". Question **ordering** additionally promotes a JD-hard-requirement concept that is keyword-only or unquantified in the vault ahead of nice-to-have breadth within its priority bucket, so a budget cut always lands on a lower-value question first — the run-4 trigger was the ceiling cutting off a required capability's quantification one question early.

---

### ADR-049 — Unified Interview Session Engine (supersedes ADR-045)

**Decision:** All interview-shaped flows — targeted (Mode A), guided (Mode B), profile enrichment (Mode C, ADR-028), and the standalone profile-review session — run on **one in-house engine** under `backend/applire/services/interview/`, replacing the parallel implementations that had accumulated (`services/session.py` for Modes A/B, a second engine inside the profile-enrich router for Mode C).

The engine's boundary rules:
- **Modes are plans.** A `ModePlan` strategy supplies only what differs per mode (gap sourcing, ceiling policy, completion effects); the engine owns the loop and lifecycle (idempotent resume, create-race handling, expiry). The gap-click micro-session is a targeted plan scoped to one gap, not a separate mode.
- **Cluster kinds are typed.** A `ClusterKind` enum (`NORMAL | GATE | CONFLICT | CONFIRMATION`) with deterministic handlers replaces string-prefix dispatch.
- **State is validated.** The session state is a Pydantic model serialized to the existing `interview_sessions.state` JSONB column — no schema change.
- **The turn is a named pipeline:** deterministic signals → special-cluster handling → reconcile (ADR-046) → advance → ask (ADR-021 review loop). Each stage is unit-testable in isolation.
- **LLM calls stay on the provider abstraction (ADR-009);** ADR-004's stateful-backend invariants carry forward unchanged. REST and MCP surfaces are unaffected.

**Why not a graph framework:** ADR-045 had adopted LangGraph, gated on a footprint spike; before implementation, ADR-046 and ADR-021 removed the cyclic-graph shape it was meant to orchestrate. ADR-049 keeps the dependency surface of the self-hosted install unchanged; a substrate can be revisited if the flows grow genuinely cyclic again.

---

### ADR-005 / ADR-017 — GDPR Retention Worker

**Decision:** A dedicated `retention` service in Docker Compose runs `python -m applire.retention` daily and enforces these TTL rules:

| Entity | TTL | Action |
|---|---|---|
| `uploads` | 7 days | Hard delete |
| `interview_sessions` | 30 days | Hard delete |
| `generated_cvs` / `generated_cover_letters` | 90 days (`GENERATED_DOCUMENTS_TTL_DAYS`, uniform — origin-blind, same for human- and agent-created documents) | Hard delete at `expires_at` |
| `applications` | 730 days inactivity (timer resets on every update) | Soft delete (`deleted_at`) |
| `applications` (cancelled by the user) | `CANCELLED_APPLICATION_TTL_DAYS` (default 7; `0` = disabled) | Soft delete, then its generated documents (incl. submitted pins) are hard-deleted |
| `master_profiles` / `users` | 730 days inactivity | Soft delete (`deleted_at`) |

**Purpose-bound exemption for submitted artifacts (2026-07, amendment):** a generated CV or cover letter that is **pinned as submitted** on an application (`applications.submitted_cv_id` / `submitted_cover_letter_id`) is exempt from the generated-documents purge while its application is alive. GDPR storage limitation is purpose-based, not calendar-based — a multi-month hiring process still needs the exact artifact that was sent (interview prep). The pin's retention clock follows the application lifecycle: once the application is soft-deleted by the inactivity rule above, the pinned document re-enters the normal purge. Nothing is retained indefinitely.

**User-initiated cancellation (2026-07, amendment):** cancelling an application (`user_status = cancelled`) shortens its retention clock to `CANCELLED_APPLICATION_TTL_DAYS` (default 7 days, env-overridable, `0` disables). The UI shows the scheduled removal date and a restore action for the whole window; after the tombstone, the worker hard-deletes the application's generated documents **including submitted pins**, regardless of `GENERATED_DOCUMENTS_TTL_DAYS` — the explicit cancellation ends the processing purpose, and the deletion was announced, never silent. Documents of a job that still has a live application are always spared.

**Why a separate service:** Data hygiene is a core operational concern (ADR-017 formalises the worker as a first-class operational building block, not a peripheral add-on). Each run emits a JSON report to stdout for audit.

**Consequence:** Every model that holds personal data must carry `expires_at` (transient data) or `updated_at` + `deleted_at` (permanent data) from the first migration.

**Two distinct concerns, gated differently (Community vs. Cloud):**
- **Right to erasure** (`DELETE /api/profile`, exposed in the UI) is always available in every edition. It is a baseline data-subject right and does not depend on the background worker running.
- **The automated daily worker** lives in Core: transient-data cleanup (`uploads` 7d, `interview_sessions` 30d) plus generated-document expiry (`generated_cvs` / `generated_cover_letters`, `GENERATED_DOCUMENTS_TTL_DAYS`, default 90 days — `expires_at` is stamped at creation, so lowering the value only affects newly created documents). Pinning a document as the submitted version (`update_application`) keeps it while the application is active. Self-hosters can raise the TTL for their jurisdiction; there is no "disable" value — `0` would mean immediate expiry, not no expiry.

All TTL values are configurable via environment variables in `applire/constants.py` — self-hosters can adjust them for jurisdiction-specific requirements.

---

### ADR-006 — CSS-Based Themes for PDF Generation

**Decision:** CVs are rendered via Jinja2 HTML + embedded CSS, with Playwright/Chromium producing the final PDF. The **same HTML is served to the frontend for live browser preview** (via `GET /api/cv/{id}/html`, injected into an `<iframe srcDoc=...>`).

**Why this matters:** There is no separate preview renderer. The preview and the PDF are guaranteed to be identical because they use the same Jinja2 template. This means:
- Theme changes only require CSS.
- Community contributors can add themes without touching Python code.
- Never use `<iframe src=...>` for CV preview — cross-origin framing is blocked by Firefox CSP. Always use `srcDoc`.

**PDF generation is async:** `generate_cv` returns immediately with `status: "pending"`. The frontend/agent polls `GET /api/cv/{id}/status` until `status: "ready"`.

**Page break control:** `avoid_page_breaks: bool` (default `True`) gates CSS rules that prevent entries from splitting across page boundaries. Can be disabled for compact layouts.

---

### ADR-007 — Open Core Architecture (AGPL-3.0)

**Decision:** Community Edition is AGPL-3.0. AGPL was chosen over MIT/Apache specifically to close the "SaaS loophole" — anyone hosting a modified version as a network service must release their modifications.

**API consumers are not derivative works:** An AI agent or application calling the MCP or REST API is not creating a derivative work under AGPL. The license restriction applies to hosting and distributing the software itself.

---

### ADR-008 — Auth Abstraction (Pluggable Backends)

**Decision:** An `AuthProvider` abstract base class lives in `applire/auth/base.py`. The factory in `applire/auth/__init__.py` instantiates the correct backend from `AUTH_PROVIDER`.

| `AUTH_PROVIDER` | Implementation | When to use |
|---|---|---|
| `none` (default) | `NoAuthProvider` — returns a fixed stub user | Community Edition, local single-user |
| `zitadel` | `ZitadelProvider` (Cloud only) | Cloud Edition, OIDC via self-hosted Zitadel |
| `oidc` | Generic OIDC (Cloud only) | Keycloak, Authentik, etc. |

**Router convention:** All routers declare `_auth: AuthProvider = Depends(get_auth_provider)`. The `_` prefix signals "infrastructure present, enforcement deferred" — the dependency is wired but unused in Community handlers. This allows Cloud backends to override without touching router code.

**Community stub:** `NoAuthProvider` returns a constant `User(id=<fixed UUID>, email="local@applire.community")`. There is one user, no login required.

---

### ADR-009 — LLM Provider Abstraction

**Decision:** An `LLMProvider` abstract base class with `acomplete()` and `aparse_json()` methods. Backend selected via `LLM_PROVIDER` environment variable. Applire is **bring-your-own-key** — no provider is privileged. `mistral` is the shipped fallback in `config.py`/`.env.example`, but any backend works with the matching key, and the choice is the operator's to make.

| `LLM_PROVIDER` | Provider | Notes |
|---|---|---|
| `mistral` (shipped fallback) | Mistral AI SDK | EU-hosted, strong German proficiency |
| `requesty` | Requesty (OpenAI-compat gateway) | EU-hosted (Frankfurt, zero-retention); defaults to the EU endpoint. An EU-resident path to Claude/GPT/Gemini via Bedrock/Azure/Vertex EU deployments |
| `openrouter` | OpenRouter API | Multi-model gateway; access Mistral, Claude, and others with one key (not EU-hosted) |
| `anthropic` | Anthropic Messages API | Claude via a Console **API key** (BYO-key) — a Claude Pro/Max/Team *subscription* cannot be used. US-hosted |
| `openai` | OpenAI SDK | Also supports LM Studio and any OpenAI-compatible endpoint via `OPENAI_BASE_URL` |
| `ollama` | Ollama REST API | Fully offline, no API costs, no key required |

> **EU data residency:** `mistral` (EU-hosted) and `requesty` (EU endpoint) keep data in-region, and a local `ollama` needs no cloud at all; `anthropic`/`openai` are US-hosted BYO-key options. A Claude *subscription* (Pro/Max/Team) is **not** usable — Anthropic permits only Console API keys (or Bedrock/Vertex, which `requesty`-EU routes through) in third-party apps.

**Why direct SDKs over LangChain:** LLM execution stays on our own provider SDKs — never LangChain's model layer (and after ADR-049 superseded ADR-045's graph substrate, no LangChain-family dependency exists at all). This reduces the dependency surface and keeps the provider contract narrow and testable.

**Temperature defaults:** `0.3` for free-text completion (`acomplete`), `0.1` for structured JSON parsing (`aparse_json`).

**Truncation is a hard error (no silent half-output):** when a model stops because it hit the token budget (`finish_reason='length'`, Anthropic `stop_reason='max_tokens'`, Ollama `done_reason='length'`), providers raise `LLMTruncatedError` rather than return a partial result. This guarantees a CV or cover letter can never be persisted as valid-but-incomplete JSON closed early under budget pressure — it fails loud and retryable.

**Reasoning ("thinking") models:** reasoning tokens count against `max_tokens`, so on short generations they can crowd out the visible answer. `acomplete`/`aparse_json` take a per-call `disable_thinking` flag — left on (`None`) for serious content (CV, cover letter, reviewers), set `True` for short "chrome" generations (interview questions, CV-section assists) so the budget reaches the answer. OpenRouter applies it via the cross-vendor `reasoning` parameter; providers without a reasoning toggle ignore the flag.

`disable_thinking` is **best-effort**, so self-hosting a thinking model needs no special configuration. Some models *mandate* reasoning and reject `reasoning:{enabled:false}` (e.g. Gemini's Flash thinking models return HTTP 400 "Reasoning is mandatory … cannot be disabled"); the OpenRouter provider catches that, retries once with reasoning left on (bounded — see below) and the budget raised to a floor, and logs a warning — the call succeeds instead of erroring. Conversely, calls that genuinely need a large output keep thinking on *and* a generous budget: CV→profile extraction uses `CV_EXTRACTION_MAX_TOKENS` and document generation `CV_GENERATION_MAX_TOKENS` (both 16384) so a rich CV plus the model's reasoning trace both fit without truncating. (`max_tokens` is a *ceiling* — you are billed for tokens actually generated, not the cap — so the headroom is free unless a generation needs it.)

**Bounding reasoning (`OPENROUTER_REASONING_EFFORT`):** on a thinking model, reasoning tokens share the `max_tokens` budget, and some models over-think simple transforms — Gemini Flash will spend ~1300 reasoning tokens drafting a cover letter, crowding out the letter itself. Set `OPENROUTER_REASONING_EFFORT=low` (or `medium`/`high`; empty = let the model decide) to cap that via OpenRouter's cross-vendor `reasoning.effort`. It is accepted even by models that mandate reasoning, so it doubles as the bound used by the fallback above. This is a deployment-wide setting today; finer per-operation control is on the roadmap.

---

### ADR-047 — LLM Output Robustness (Segmentation-First)

**The problem self-hosters hit:** a model's real *output* ceiling is usually far below its context window, and below what a big generation needs. A whole tailored CV — or a whole two-CV profile merge — is one large JSON, and on output-capped models (e.g. some Mistral mediums stop near ~8k regardless of the requested ceiling) or reasoning-mandatory models (reasoning eats the same budget), that single call **truncates**. Raising `max_tokens` does not help: it cannot exceed the model's own cap, and the bigger, slower call then hits the request timeout.

**The fix — one principle for *every* LLM call.** Each call is one of two shapes, so a capped model never has to emit a whole document in one response:

- **Small-output by contract** — the call may *read* a lot (large input is fine) but is only ever asked to *write* a little, under a small budget: a reviewer returning an approve/reject verdict, a classifier, a merge op-list. These can't truncate because they never produce bulk text.
- **Large-output → segmented** — anything that would emit a whole document is split so no single call needs a large output:
  - **CV tailoring** ("outline-then-expand"): a small outline call decides emphasis and ordering, then each work-experience entry and each section (summary, skills, education, projects) is generated under a small per-call budget, all sharing one tailoring directive so the voice stays consistent. The pieces are assembled deterministically in code, then a final pass enforces coherence and output language (ADR-038).
  - **CV import / extraction** is read section by section, so a long, dense CV is never lost to a truncated parse.
  - **Profile reconciliation** keeps its single fast-path call (ADR-046) but, when that would not fit, **falls back to reconciling entries in batches** — several small calls instead of one large one (still no multi-turn tool loop; the deterministic applier still disposes).
  - **The review/refine loop** keeps the reviewer small (a verdict that *points at* what to fix, never re-emitting the document) and lets the refiner rewrite only the flagged sections — so the quality pass can't itself truncate the result it just produced.

**Cap-aware, not budget-doubling:** on a truncation or timeout of a large generation, Applire switches to segmented mode rather than doubling the budget into a timeout. Internal LLM errors are mapped to a human message with a retry — never shown to you as "raise max_tokens".

**Structured-output shape contract (amended 2026-07-15):** every structured (`aparse_json`) call asks for a top-level JSON **object** — never a bare array — because OpenAI-style JSON mode (and the Anthropic `{`-prefill) can only emit objects; a list result rides in a named key such as `{"clusters": [...]}`. Parse failures are never silent: malformed items are dropped *with a log line*, a zero-yield parse from non-empty input logs a warning, and downstream features must fail honestly ("unavailable") rather than report an empty result as a clean bill. If you contribute a new LLM chain, follow this shape and give the mock provider a response with the **same top-level shape** a real JSON-mode backend would return — a mock that diverges from real providers turns the whole hermetic test pyramid into a false green.

**No model metadata required.** Segmentation works on *any* backend, including a local Ollama or an opaque OpenAI-compatible endpoint where the output ceiling can't be discovered. Where it *can* be discovered (OpenRouter's model metadata, Ollama's `/api/show`), Applire pre-empts the doomed first call by segmenting from the start — an optimisation, not a requirement.

**Choosing a model:** see **[`docs/llm-models.md`](llm-models.md)** for the recommended-models matrix and the minimum-capability floor (output budget, structured-output support, reasoning behaviour). Models below the floor still work via segmentation, but the recommended set gives the smoothest first-run experience.

---

### ADR-012 — Edition Gating (Import-Based Detection)

**Decision:** Edition detection uses Python import presence, not an environment variable:

```python
# applire/edition.py
try:
    import applire.cloud
    HAS_CLOUD_EDITION = True
except ImportError:
    HAS_CLOUD_EDITION = False
```

All runtime checks use `if HAS_CLOUD_EDITION`. Cloud-only service methods return HTTP 402 with an upgrade prompt in Community Edition.

**Why import-based, not env-var:** An env-var (`APPLIRE_EDITION=cloud`) creates the false impression that setting it unlocks Cloud features in a Community install. It doesn't — the code doesn't exist. The import approach is honest: if the module isn't installed, the feature doesn't exist.

**Two-repository model:**
- `applire` (this repo, AGPL-3.0): Community Edition, `applire.*` namespace only.
- `applire-cloud` (private, proprietary): Imports `applire` as a dependency, adds `applire.cloud.*`.

Cloud code **never appears** in this repository.

---

### ADR-013 — Additive Profile Enrichment

**Decision:** The Master Profile uses an **accumulation-first** merge model. The conflict bar is deliberately high.

| Scenario | Action |
|---|---|
| Different job titles for the same position | Accumulate into `role_aliases[]` |
| Different responsibility bullets | Union into `responsibilities[]` |
| Different `start_date` for the same position | **Flag as conflict** |
| Same skill, different proficiency levels | Keep the declared value already on the profile — a declared proficiency is a ceiling, never raised by a later write (ADR-061) |
| Company name variant ("Siemens AG" vs "Siemens") | Normalise, no conflict |

**Rule of thumb:** If both values can be true simultaneously, accumulate. If only one can be true, flag.

**Why:** A professional legitimately describes the same role differently across CVs targeting different audiences. "Team Lead" and "2nd Level Support Engineer" for the same job are both true. Treating them as a conflict creates friction and discards valid data. `role_aliases[]` gives the CV tailoring engine a rich palette to select from per application.

Source tracking (an `EnrichmentRecord` for every change) is mandatory — every field value is always traceable to its origin.

---

### ADR-014 — CV Upload & Parsing Pipeline

**Decision:** Two semantically distinct endpoints:
- `POST /api/profile/upload` — Human-facing, multipart file ingestion (PDF, DOCX, images, plain text).
- `POST /api/profile/import` — Structured data ingestion (LinkedIn ZIP exports, XING OAuth responses, JSON).

**OCR backends** (same factory pattern as LLM/Auth/Storage):

| `OCR_BACKEND` | Implementation | Notes |
|---|---|---|
| `mistral_vision` (default) | Mistral `pixtral-12b` via vision payload | Zero system deps, works out of the box |
| `tesseract` | `pytesseract` + local Tesseract | Fully offline; requires system dep via `docker-compose.override.yml` |

**JD context is optional on upload.** Requiring a JD before uploading a CV would block new-user onboarding. A JD-aware extraction prompt is used when `job_id` is provided; a generic prompt is used otherwise.

---

### ADR-015 — EU AI Act Compliance Boundary

**Decision:** The candidate-side workflow (tailoring your *own* CV) is **minimal risk** under the EU AI Act — the candidate is both the data subject and the controller of the process, and no employer decision is influenced. Recruiter-side functionality (ranking, scoring, candidate-mandate matching) is high-risk under Annex III Category 4 and is **not part of the Community Edition** at all.

**Why this matters for contributors:** Community Edition is deliberately **candidate-side only**. Any feature that profiles candidates against a vacancy, ranks/shortlists applicants, or otherwise materially influences a hiring decision is out of scope here — it belongs to the regulated Cloud recruiter module with its own compliance posture. Keep contributions inside the minimal-risk candidate boundary.

---

### ADR-016 — Flow Orchestrator State Machine

**Decision:** A `flow_sessions` table tracks the end-to-end user journey. Step transitions are validated against a `VALID_TRANSITIONS` dict in `applire/services/flow/orchestrator.py`.

**Linear DAG:**
```
jd_analysis → cv_import → gap_analysis → interview → cv_generation → complete
```

Returning users skip `cv_import`. **The interview offer is gap-driven (amended 2026-07-13):** at `gap_analysis`, when the analysis found items to address the interview is offered (with a skip to generation) for *both* user types; a clean sweep goes straight to `cv_generation`; a parked profile-integrity gate forces the interview (ADR-041). A user with a job but **no CV** takes the no-CV onboarding path `jd_analysis → interview` (skipping `cv_import` + `gap_analysis`) — the guided interview builds the Master Profile from scratch, then `interview → cv_generation` as usual.

Key invariants:
- One `flow_session` per `(user_id, job_id)`, enforced by a unique constraint.
- `user_type` (`"new"` | `"returning"`) is resolved once at flow creation and is **immutable** for the lifetime of the flow.
- Steps that produce artifacts (gap analysis, interview, cv generation) require `artifact_id` in `AdvanceFlowRequest` — missing `artifact_id` returns HTTP 422.
- Invalid step transitions return HTTP 409 with `allowed_transitions` for client recovery.
- `flow_sessions` carries no PII — it is a routing record. GDPR TTLs live on child records.
- Concurrent gap-analysis kickoffs are DB-arbitrated (amended 2026-07-13): partial unique indexes allow one live gap job per `job_analysis_id` and one live analysis row per `(job_analysis_id, input_fingerprint)`; services recover from a lost race by adopting the winner, and an analysis row is committed only WITH its gap clusters (never readable half-built).

---

### ADR-018 — Contributor License Agreement (CLA)

**Decision:** External contributions to this AGPL-3.0 repository require a lightweight **DCO + CLA hybrid**:
- **DCO** (`Signed-off-by` trailer on every commit) — the low-friction gate for docs, typos, and minor fixes.
- **CLA** (signed once via CLA Assistant on first qualifying PR) — required for changes touching core service logic (`applire/services/`, `applire/models/`, `applire/routers/`).

The CLA is a **license grant, not a copyright transfer** — you keep copyright of your contribution, and it remains available to the community under AGPL-3.0. The grant lets Applire also dual-license the code into the proprietary Cloud Edition, which is what keeps the open-core model viable.

---

### ADR-019 — CV Section Editor (Snapshot + Override)

**Decision:** Two JSONB columns on `generated_cvs`:
- `content_snapshot`: the structured rendering context, populated at generation time.
- `section_overrides`: user edits keyed by section ID, initially `{}`.

Re-rendering on section save uses Jinja2 only (fast, no Playwright). Playwright is only invoked on final PDF download.

**Dual save path:**
- `save_to_profile: false` (default for free-text edits) → writes to `section_overrides` only; Master Profile unchanged.
- `save_to_profile: true` (default for Kaile-assisted edits) → writes to `section_overrides` AND posts through the existing profile merge pipeline (ADR-013).

**Why not a separate `cv_documents` table:** JSONB columns on `generated_cvs` are proportional to the use case and keep the profile as the single source of truth. A new first-class model would add schema overhead for a feature that is fundamentally about transient display edits.

**Amended (2026-07-03) — gap hints derive from the Keyword Ledger × live document coverage.** The section editor's "Related gaps" originally served the stored gap-category lists with no check against the document, so a keyword the generator had already (legitimately) surfaced could still display as an open gap next to itself — and saving a section deleted matching gap entries from the stored analysis, letting a typed keyword erase an *honest gap* record. Gap hints are now computed at read time as (ledger entry × current document coverage): claimable-and-covered entries are hidden, claimable-but-uncovered hints keep the editor/Kaile call-to-action, and honest-gap hints route to the enrichment interview instead of inviting the user to write an unsupported claim into the document. Coverage is never persisted into the gap analysis; pre-ledger analyses fall back to the legacy read-only heuristic.

---

### ADR-021 — LLM Review Layer (Retry-with-Critique)

**Decision:** A generic `review_and_refine()` function (`services/reviewer.py`) wraps high-risk LLM generations. After the generator produces a draft, a second *reviewer* LLM call receives the original source material plus the draft and returns `{ approved, issues, feedback }`. On rejection (with retries remaining), the feedback is prepended to the generator prompt and a new draft is produced. The loop exits on approval or when `LLM_REVIEW_MAX_RETRIES` (default `2`) is exhausted — it **never raises**; a degraded last draft is preferable to a broken flow.

**Where applied:** profile extraction and CV tailoring (the two places hallucinations were observed — phantom work entries, JD-matching bullets with no CV basis). Setting `LLM_REVIEW_MAX_RETRIES=0` disables the layer entirely for cost-constrained self-hosters.

**Amended (2026-06-22):** the CV-extraction reviewer's fabrication test was recalibrated from *verbatim* matching to *semantic* faithfulness — paraphrase, sentence splits, and de-duplication merges are no longer flagged as "invented content" (they were exhausting both retries on nearly every upload, ~doubling latency, and crowding out real errors). In exchange, **cross-role content misattribution** (an achievement landing under the wrong employer/role on a multi-role CV) and **invented dates** (any date absent from the source must be `null`) are promoted to priority checks. The loop, schema, and retry default are unchanged.

**Amended (2026-07-26) — the loop remembers, the reviewer does not.** Because neither the reviewer nor the loop carried any state, a reviewer mistake in one round could be applied as damage in the next with nothing noticing — in the worst observed case a reviewer flagged a value that was present verbatim in the source, the generator dutifully deleted it, and the field shipped empty. The loop now keeps deterministic state **in Python** over the drafts it has already produced (no extra LLM call, nothing added to any prompt):

- **Cycle stop.** A retry that reproduces a draft already seen in this loop proves the reviewer/generator pair is oscillating and cannot converge, so the loop settles immediately instead of burning the remaining retries. It logs `REVIEW_CYCLE_DETECTED` — distinct from `REVIEW_EXHAUSTED`, so a document that shipped this way stays countable.
- **No-regression floor.** Callers may declare fields that, once populated in *any* draft, must never ship absent; the loop restores such a field from the most recent earlier draft that had it. It **fails open** — if no draft ever had the field, the settled draft ships as-is rather than a fabricated value.
- **Structural selection.** An optional non-negotiable structural predicate (e.g. "the cover letter has a real closing paragraph") plus an optional secondary structural tie-break that only ever narrows the choice among drafts the first already accepts. Both are structural checks, never quality scores, and never LLM calls. When the primary is met but the secondary is not — a genuine closing that overruns the page-norm word budget — the document ships and `LETTER_OVER_BUDGET` records the trade rather than absorbing it.

**The reviewer prompt deliberately stays memoryless.** Showing the reviewer its own prior verdicts looks like the natural fix for oscillation; it was considered and rejected. This layer pays for a *second* model call precisely to escape the bias a generator has toward its own output — a reviewer anchored to defending its earlier judgement is no longer an independent read of the source, and its input would grow with every round. All cross-round memory is deterministic Python; every parameter above defaults to off and reproduces the previous behaviour exactly.

**Amended (2026-07-28) — issues carry a severity, and only a blocking one causes a rewrite.** The original decision left severity out on purpose, to be revisited if minor issues turned out to cause unnecessary retries. They did, and for a worse reason than cost. The reviewer had no way to say *"I noticed this, but it is not worth regenerating the document"*, so every observation it made — a repeated word, a paragraph it would have ordered differently — arrived as a rejection. Testing a stronger model did not fix it: on identical inputs a frontier model filed far fewer bad issues but still filed non-blocking observations as rejections, because the gap is in the schema, not the model.

An issue is now `{ "severity": "blocking" | "minor", "issue": "…" }`:

- **blocking** — as it stands the draft would put something untrue, unsupported, or misattributed in front of a reader, or omits something the source explicitly required.
- **minor** — the draft is truthful and complete; the reviewer would simply have written it differently. Recorded and logged, never acted on.

A round that rejects a draft while raising only minor issues **ships it**. The justification is truthfulness rather than latency: as the 2026-07-26 amendment above establishes, each rewrite is a memoryless regeneration that can erode content an earlier round had right — so an unnecessary rewrite is a real chance of losing a grounded fact. Reviewers are therefore instructed to resolve doubt toward `minor`.

Parsing fails safe in both directions. Anything not readable as an explicit `minor` — absent, misspelled, or a plain issue string from an older prompt — is treated as **blocking**, and a rejection that enumerates no issue at all (with all the substance in `feedback`) still retries. Neither degenerate case can turn into a silent approval.

Every reviewer prompt composes one shared severity contract (`prompts/review_severity.py`) so the vocabulary and JSON shape cannot drift prompt by prompt, then adds a single line naming what is blocking **in its own pass** — a failure of its own numbered checks, and anything else it notices is minor by definition. When the gate ships a draft it logs `REVIEW_MINOR_ONLY`, which is also how the gate's own failure mode stays visible: a chain quietly filing genuine defects as minor shows up as a rising count rather than as a silent quality drop.

---

### ADR-027 — Cover Letter as a Parallel Document Artifact

**Decision:** Cover letters are a fully separate pipeline — their own `generated_cover_letters` table, service, router, and Jinja2 templates — **not** a discriminator column on `generated_cvs`. Each of the CV templates has a matching cover letter template (shared header style, typography, color profile) for a coherent application package.

Cover letters are **post-CV add-ons**: triggered from the CV page after the CV is ready, one per `job_analysis_id`. The flow state machine is not extended. Pre-generation inputs surface DACH conventions (Gehaltswunsch, Eintrittstermin, tone). They inherit the same 90-day retention as generated CVs (ADR-005).

---

### ADR-028 — Profile Enrichment (Mode C Interview)

**Decision:** A third GapDetector strategy ("Mode C", Profile Enrich) lets users improve Master Profile completeness **without a job description** — scanning the profile JSONB for missing achievements, context fields (team_size, budget_managed, industry_context), and summary. The four-node interview graph topology (ADR-004) is unchanged; Mode C is a new entry into the GapDetector node only. ResponseParser is wrapped with the ADR-021 reviewer for all Mode C sessions (highest corruption risk to the profile).

Dedicated `/api/profile/enrich/*` endpoints own the Mode C lifecycle; sessions reuse `interview_sessions` (its `job_analysis_id` is nullable). "N/A" decisions persist to `profile_json._meta.na_fields` — a private namespace excluded from CV rendering and future gap detection.

---

### ADR-029 — Proactive Gap Clustering & Interview UX

**Decision:** After gap analysis, a lightweight second LLM call (`cluster_gaps()`) groups the raw B/C gaps into 5–12 semantic clusters, stored in a `gap_clusters` JSONB column on `gap_analyses`. Users see meaningful topic clusters (each carrying its JD rationale: `jd_context`, `jd_skills`) instead of dozens of near-duplicate gap items. The original gap-analysis prompt is untouched (no regression risk).

Question generation returns `{ question, choices }` — optional multiple-choice (generated when a cluster has ≥2 gaps or is Category B) pre-fills an editable textarea; free-text is always available. The interview page is a 65/35 split: question/choices on the left, a live match-score gauge and per-cluster JD tracker on the right.

---

### ADR-035 — Deterministic Match-Score Computation

**Decision:** The headline "Match Score: NN%" is **computed in Python**, not emitted by the LLM. The LLM only *classifies* each JD requirement into one bucket — `direct` (1.0), `partial` (0.5), `gap` (0.0) — and Python decides each requirement's weight by membership in the JD's own lists (`required_skills` → 1.0, `nice_to_have_skills` → 0.5). The score is then `earned / N` over those weighted requirements (`services/match_score.py::compute_match_score()`, a pure, unit-testable function).

**Why:** An LLM classifies a single requirement well but should not be trusted to do arithmetic over its own classifications — the previous free-form score drifted badly (e.g. 88% emitted where the categories implied ~61%). The score and the displayed categories can now never disagree, it is reproducible, and it is explainable via a per-requirement `requirement_breakdown` JSONB column. Unclassified requirements default to `gap` (never silent credit); `N == 0` yields a `NULL` score.

**Amended (2026-06-30, ADR-048):** the classification feeding the score is now the fit-weighted slice of the **Keyword Ledger** (ADR-048 below) rather than a separate list — formula and weights unchanged (parity-tested), only the input source unified.

---

### ADR-037 — Authentication Gate Placement (Up-Front)

**Decision:** Authentication happens **up-front**, before any CV upload or LLM processing. There are **no anonymous/guest sessions** and no "claim anonymous work on login" migration — a deliberate non-feature. First successful login provisions the `User` plus an empty Master Profile in one shot (create-on-first-login, ADR-008; 1:1 User↔Profile, ADR-022 rejected the alternative).

**Why:** The first user action is a CV upload — sensitive PII immediately processed by an LLM. Gating up-front means no anonymous PII is ever stored or processed pre-consent (cleanest GDPR / EU AI Act posture, ADR-015) and avoids an anonymous-session + claim-migration engine that would also collide with the 1-User→1-Profile invariant. In Community with `NoAuthProvider` the gate is transparent (the stub user auto-resolves); it is enforced where an OIDC provider is configured.

---

### ADR-038 — LLM Output-Language Routing

**Decision:** Two language domains, routed by output kind:
- **Conversation** (interview questions and choices, MODE B questions, follow-up probes, Mode C enrichment questions) is generated in the user's **UI language** (`UserSettings.ui_language`, non-nullable, default `en`) — *regardless* of the language of the profile, JD, or injected context.
- **Documents** (tailored CV, cover letter) follow the **target-job language**, unchanged.

**Why:** A user picks a UI language because that is the language they want to *operate in*; a CV/cover letter must be in the *employer's* language to be usable. Previously the conversation generators carried no language directive and drifted to whatever language the source material (e.g. a German JD's `jd_context`) happened to be in. The directive is enforced at the system-prompt level (`with_language()`) and verified by the ADR-021 reviewer loop (`INTERVIEW_QUESTION_LANG_REVIEW_MAX_RETRIES`, default 1). Scope: English/German only.

**Amended (2026-06-10):** the document-side "target-job language" is now resolved deterministically. `job_analyses.jd_language` stores the language the JD is *written in* — detected in code by a stopword/umlaut scorer (`applire/utils/language_detection.py`) at analysis time — and both document generators route on it. The previous source, `language_requirement`, describes what the job demands of the candidate (e.g. "Bilingual DE/EN") and misrouted mixed-language postings; CV tailoring additionally receives an explicit `OUTPUT LANGUAGE` directive instead of inferring the language itself. In the same change, the cover-letter date became system-injected (`applire/utils/letter_date.py`) rather than LLM-generated.

**Amended (2026-07-05):** the document-language enforcement pass now covers **project bullets** (nested under work entries and standalone) — previously it reviewed only the summary, work bullets, and skills, so English project text could ship in a German CV — and it runs after **every** step that adds prose to the document, including the deterministic project-nesting copy from the Master Profile. Standing rule: the language pass is the last prose writer; only language-invariant facts (certifications, dates, metrics) may be added after it.

---

### ADR-039 — ATS Parseability (Every Template, Local Audit, CI Guarantee)

**Decision:** Every CV and cover-letter template must be ATS-parseable — there is no dedicated "ATS template" (the "Universal ATS" theme once named in ADR-006 was withdrawn; a user has no reason to pick a template that hurts their application). A deterministic, fully local audit engine (`applire/services/ats_audit.py`, pypdf text extraction — no LLM, no network, no external checker services) verifies after each render that contact details, every work-history entry, education, and skills survive machine parsing in the correct reading order, and reports which of the job's ATS keywords appear in the final text.

- The audit runs inside the async generation job; its report is persisted alongside the document (nullable `ats_report` JSONB on `generated_cvs` / `generated_cover_letters`, sharing the document's retention TTL) and exposed via REST and MCP as a **checks panel** — named pass/fail checks and missing keywords, deliberately **no aggregate 0–100 score** (same no-synthetic-numbers philosophy as ADR-035).
- An audit-engine error never fails generation: the report stays `NULL` and the error is logged.
- **CI guarantee:** a parametrised suite renders every template with DE and EN fixture documents and asserts every audit check passes. Where a template's visual layout conflicts with extraction order, the layout changes.

**Why:** An ATS parses the CV before any human reads it. Keyword *content* was already covered (JD keyword extraction → gap detection → guarded incorporation in tailoring, ADR-021); this decision closes the *format* half with verifiable, sovereignty-friendly evidence — documents never leave the system to be checked.

**Amended (2026-06-30, ADR-048):** the keyword *content* path was wired to the CV only — not the cover letter or either reviewer — so a >90%-fit profile produced a 13/22 cover letter. ADR-048 closes the content half: the audit engine is unchanged, but the panel now annotates each missing keyword as *missing-claimable* (profile-backed — should have been surfaced) vs *missing-honest-gap* (not in the profile), and honest gaps route to the interview instead of dead-ending in the panel.

---

### ADR-040 — Truthful Output (Attestation & Transparency Tier)

**Decision:** LLM-derived content that can leave the system as a truth claim (profile facts from extraction/merge/interview; delivered CV/cover-letter bullets) must carry **two** controls, not one: the existing ADR-021 prevention reviewer *and* a user-facing **transparency/attestation surface**. A single reusable "what changed & why" component is shown at three touchpoints — a skippable confirm of what was read from a CV, the auto-resolved assumptions after a merge, and an answer→field change summary after the interview.

- The surfaces are sourced **only** from the durable decision trail (the `EnrichmentRecord` / `FieldChange` history mandated by ADR-013) and the current persisted artifacts — **never** the source upload, which is hard-deleted after 7 days (ADR-005). Transparency therefore survives for the full life of the profile.
- Attestation is an active **nudge, not a hard gate**: the user is prompted to confirm but the flow is never blocked.
- **Pre-download (amended 2026-07-01):** before a CV or cover letter downloads, a quiet notice replaces the old diff modal. The deterministic generated-CV-vs-Master-Profile check is trimmed to unambiguous red flags — an invented company, an invented skill, or a changed employment date (a rephrased job *title* is expected tailoring and is left to the ADR-021 reviewer). When a red flag is present the notice is shown and cannot be suppressed; otherwise it degrades to a plain "AI-generated content can have faults — review before you submit" notice with a **"Don't show this again"** control. Suppression is a single user setting (`hide_predownload_notice`) shared across both documents.

**Why:** A self-checking LLM reviewer reduces how often a fabrication occurs but cannot, by itself, let the *user* catch one — and false content in a CV reaches a real recruiter under the user's name. A prevention reviewer plus an animated user review are complementary: one lowers how often the failure happens, the other lets a human stop it before it ships.

- **Reconciler stance guard (ADR-046 amended 2026-07-05; grounding superseded in part by ADR-061, 2026-07-27):** an interview answer that *denies* experience ("I have never built a production RAG system") must never become a profile fact. The reconciler prompt carries an explicit stance rule and must list every denied item in a `denials` array; a deterministic guard inside the engine strips any op content matching the model's own denials (never-claim outranks claim, ADR-040) everywhere — this half is unchanged. Since ADR-059 the denial also *persists*: it is recorded in the profile's metadata with a receipt, reported honestly to the caller (`denial_recorded`), and enforced as a deterministic keyword-ledger floor so later gap analyses cannot re-infer the denied concept via adjacency.

  **Testimony vs. coverage (ADR-061, 2026-07-27):** the *grounding* half of the guard was rebuilt after a real German run silently dropped five true claims in one turn — a qualifier scoped by an earlier clause ("PP" affirmed a sentence after "SAP-Rollout"), a parenthetical gloss ("OEE" vs. the op's spelled-out "OEE (Overall Equipment Effectiveness)"), and a German compound tail ("Sauberraumbereich" vs. "Sauberraum-Management") — because one literal-substring predicate was answering two different questions. *Coverage* ("is this token present in this text?") stays exactly as it was: the shared `surface_present` predicate, unchanged, still the sole instrument for the ATS panel, the keyword ledger, and the retention-scoring corpus check above — so those three can never disagree on presence. *Testimony* ("did the candidate say they have this?") is now a separate, explicitly-named predicate at the reconcile seam: literal/aliased matches are accepted deterministically (the overwhelming majority, at no extra cost); anything left is adjudicated by a narrow LLM call — *does this turn state the candidate has X — yes/no/unclear — and quote the span that says so* — and the returned quote is verified as a literal substring of the turn before the "yes" is ever trusted. A fabricated-but-plausible quote, a "no"/"unclear" answer, a malformed response, or a provider outage/timeout all fall back the same way: never to confirmed. A same-turn skill/language/certification claim that clears neither the deterministic nor the adjudicated path is no longer discarded — it is written to the vault as `unconfirmed`, a third state that is visible to the candidate and candidate-confirmable, but can never back a CV bullet, a letter sentence, or a `direct` keyword-ledger row (mirroring the `unproven`-never-claimable safety property the ADR-059 denial-revocation amendment already established). A denied token is still stripped outright regardless — never-claim continues to outrank claim.
- **Skill canonicalization (ADR-046 amended 2026-07-15; proficiency merge amended 2026-07-27 by ADR-061 clause 5):** the reconciler merges *near-duplicate* skills instead of appending them — an incoming skill whose normalized tokens are contained in (or heavily overlap) an existing one folds into it, unioning its experience evidence, so repeated imports don't accumulate "Team Leadership" next to "Team Leadership and Mentorship". The merge no longer keeps the higher of the two proficiency tiers: an already-declared proficiency is a ceiling and is never raised by a later write, on either door — the #304 regression was exactly this "keep higher" rule silently promoting a deliberately modest self-declaration. An incoming value only fills a genuinely unrecognised existing value. Compound-vs-atomic phrasings ("Docker & Kubernetes" arriving beside an existing "Docker") are never merged silently — the user is asked. The same near-duplicate predicate drives the tailored-CV skills dedup and the `skills-near-dupe` ATS audit check, so the profile, the rendered CV, and the audit can never disagree about what counts as "the same skill".
- **Three-band identity for every entity section (ADR-046 amended 2026-07-16):** the skills policy is generalised by a section-agnostic dedupe module (`applire/services/profile/reconcile/dedupe.py`) so education, certifications, languages, and publications get the same treatment — a strict near-duplicate on every identity field auto-merges (filling only empty fields), a bare single-word overlap asks the user instead of guessing, anything else is appended as new. Languages treat containment as identity ("German" vs "German (Native)" is one language). Work, project, and volunteer entries additionally get a deterministic guard when the reconciler creates a new entry without targeting an existing one: a near-duplicate organisation with the same start month adopts the existing entry; a weaker match asks. Publications gain their previously missing reconciler operation (`upsert_publication`) — before this they could not be merged or deduplicated at all.

---

### ADR-048 — The Keyword Ledger (Unified JD-Expectation Classification)

**Decision:** A JD's expectations (`required_skills`, `nice_to_have_skills`, `keywords`) are unified into a single **Keyword Ledger** built in the gap step (`gap_analyses.keyword_ledger` JSONB). One entry per expectation carries *both* a **concept** (the requirement, drives the fit score) and its literal **surface forms** (the alias strings an ATS scans for, e.g. `Kubernetes`/`K8s`, drive coverage), classified `direct`/`partial`/`gap` against the profile with the supporting **evidence**, a `fit_weight` (required 1.0 / nice-to-have 0.5 / pure-keyword 0.0), and a derived `claimable` flag. Fit scoring (ADR-035), the CV and cover-letter generators, both reviewers, the ATS panel (ADR-039), and honest-gap interview routing all read from this one ledger — no consumer classifies JD expectations independently any more. Both generators receive the **claimable** entries with their evidence ("surface these where the profile supports them") plus the honest-gap entries as an explicit **do-not-claim** list; the reviewers add a claimable-coverage check that feeds the existing refine loop.

**Why:** Chocolate UAT found a >90%-fit profile producing a 13/22 cover letter — because the three keyword lists fed three different consumers and the cover-letter writer/reviewers never saw the keywords they were graded on. The ledger reconciles them so fit and coverage are explainable against the same evidence. Standing principle: **grounding strictly outranks coverage** — every derived expectation is an LLM estimate of an unknowable target (we never see the recipient's real ATS), so generosity lives only in the alias layer, never in pushing the writer to claim something the profile does not support. Genuinely-absent keywords are reported honestly and routed to the interview for profile enrichment, never fabricated.

**Amended (2026-07-27) — four statuses, and what the writers do about the unmet ones.** The status vocabulary becomes `direct` / `partial` / `gap` / `denied`. `gap` now means strictly *unknown* (no signal — never asked, or asked and unanswered); `denied` means the candidate was asked and stated they do not have it. `claimable` is unchanged (`direct` or `partial`), so a denial is still never claimable — it is simply no longer indistinguishable from "we don't know", which the interview, both writers and the review loop each need in order to behave differently. Two consequences for generated documents. First, a `partial` entry may now record **what makes it partial**: when the candidate does not have the named requirement but holds an adjacent capability (the posting asks for TOGAF, the profile has arc42), the ledger names the adjacent capability, and the writers are instructed to give *that* prominence rather than to surface the posting's own term — previously the only derivable instruction was "surface TOGAF", which is an instruction to over-claim. The deterministic coverage check correspondingly stops demanding such a term appear literally. Second, `denied` and adjacent-`partial` requirements are routed to an explicit **positioning decision** (a scoped claim, a transfer argument in the candidate's own words, or a brief honest de-emphasis) instead of the previous claimable/do-not-claim binary; the CV gains a matching rule, where before it received only a do-not-claim list.

**Amended (2026-07-27, same day) — the ATS audit grades the *document*, not the candidate.** A ledger status may change how a missing keyword is *explained*; it must never change whether it counts. Adding statuses leaked across that line in three places.

- The **present-but-unsupported truthfulness warning** (below, 2026-07-03) is now raised only for `gap` (unknown) requirements. It matches keywords by normalised substring and so cannot see negation — meaning the honest sentence the amendment above asks for ("I have not worked in X") was flagged as if it claimed X. The project's position: truthfulness is owed to a reader that parses meaning — a human, or an LLM screener — and not to a keyword counter. A negated sentence is honest to every such reader; that a substring counter also scores it as coverage is the counter's concern. The direction-aware version of this check already lives in the Truthfulness Oracle, which evaluates clause by clause and still audits any positive claim smuggled alongside a denial.
- The **adjacency exemption is one shared predicate**, so the ATS panel and the pre-generation coverage check cannot disagree. Previously the panel reported a missing keyword the pipeline had deliberately, correctly declined to write.
- **Page-length budgeting scores role relevance and bullet retention on the adjacent capability** (arc42), not on the posting's term (TOGAF). No bullet contains the latter, so protecting it protected nothing, while the bullet the writer was told to promote counted as irrelevant and was first to be dropped when the CV overran. The adjacent capability is used for retention only — it never makes the posting's term read as present.

Keyword coverage itself is unchanged and stays status-blind: a denied requirement remains in the denominator, because a real ATS does not care *why* a word is absent.

---

**Amended (2026-07-03) — the two-axis consumption model.** Every ledger entry is read along two orthogonal axes: the **evidence axis** (`direct`/`partial`/`gap` against the profile — owned by gap analysis, changes only when the profile or JD changes) and the **coverage axis** (surface-form presence in the extracted document text — owned by the ATS audit, changes on generation and on section edits). Every UI surface derives its view from (entry × current coverage) at read time; coverage is never written back into the evidence record. The ATS panel gains the fourth quadrant: a keyword **present in the document but not supported by the profile** is flagged as a truthfulness warning ("remove it or add evidence"), where previously only *missing* keywords were bucketed. The fit score is labelled **profile match** and the ATS count **document coverage**, making their deliberately different refresh cadences legible as two measurements rather than one inconsistent number.

---

### ADR-041 — Master Profile Health (Integrity Tiering & Standalone Review)

**Decision:** The Master Profile gains a deterministic **health assessment** that classifies issues — merge *conflicts*, *accuracy / merge-loss* (an extracted-vs-stored data-point reconciliation), and *completeness* gaps — each at a **severity** of `info`, `review`, or `critical`. A standalone **"profile review"** interview (no job description required) and a Master Profile **Health panel** let the user resolve them, reusing the existing interview engine (guided Mode B, conflict questions, Mode C enrichment) and the ADR-040 correction routing.

- **`review` / `info`** issues stay optional (a dismissible nudge), preserving the friction-free happy path (ADR-037).

**Why:** Previously, merge conflicts and extraction errors only surfaced when a job application happened to make them relevant — so a profile could carry a silent error indefinitely, with no way to review the profile on its own. Severity tiering forces the serious cases to be corrected while keeping the common case frictionless.

**Amended (2026-06-17) — reversibility tiering.** Designing for the user who errs *by accident*, the model is organised around **reversibility** rather than a severity number:

- **Pre-merge gate** (destructive if merged): a file that doesn't look like a CV (e.g. an accidentally-uploaded manual) or a CV whose **name doesn't match the profile** are confirmed *before* the additive merge commits — the safe default is *don't merge*. Name divergence is detected **deterministically** (no LLM): the system flags the *difference*, the **user** decides whether it's really their CV. A deferred gate parks the CV in the uploaded-documents list with a *Continue* action.
- **Post-merge staged review** (non-destructive): ordinary discrepancies (same role, different dates/titles) **never overwrite** existing data — they are staged as conflicts and surfaced in the Health panel. Such contradictions are `review`, not `critical`.
- *Completeness* is a separate, never-blocking **score**, not a severity-tagged issue. The severity field is `profile_mismatch_severity` (distinct from the ADR-021 reviewer severity).

**Amended (2026-06-24) — unified, role-aware completeness model.** The completeness score and the enrichment-interview gap list are derived from **one** field-level model, so they can no longer disagree (previously a presence-only section score could read "99% complete" while a separate per-field scanner offered 22 questions): `gaps = expected − present`; `score = (present ∩ expected) / expected`, weighted by section.

- **Expected fields are role-aware.** Every entry expects a floor of `start_date`, `end_date`, `achievements`; the role-conditional fields (`team_size`, `budget_managed`, `industry_context`) are expected only where the role warrants them — a team lead is asked about team size and budget, an individual-contributor or junior role is not.
- The role→expected-fields judgment is made by an **LLM at write time** (on import / merge / edit, from the entry's title + description) and **stored** on the entry; the health **read path stays LLM-free and deterministic** — the LLM enriches the data, it never computes the score live. With no annotation (legacy data, or no provider configured) the model falls back to the floor only, so health never hard-depends on an LLM.
- **Presence always counts; role only gates absence** — a field the user filled in always scores, so misclassifying a role can at most skip a question, never hide data. Completion-gap questions are **targeted** at the specific role and field rather than open-ended.

---

### ADR-042 — Master Profile Versioning & Merge Undo

**Decision:** Before any CV merge commits, the current Master Profile is **snapshotted** (a copy of `profile_json`, keyed to that merge's enrichment record, in a dedicated `profile_snapshots` table). An **"undo last merge"** action restores the most recent snapshot, clears the conflicts that merge introduced, and recomputes profile health. Snapshot *capture* is unconditional on every merge; the MVP exposes undo of the last merge (a full version-history UI is a later addition).

- If edits were made *after* the merge being undone, undo still restores the pre-merge state but **warns** that those later changes will be discarded (a coarse whole-profile restore; per-field revert is deferred).
- Snapshots are bounded per profile and are profile-derived data — purged with the profile under the existing retention/erasure rules, with no dependency on the (already-deleted) source upload.

**Why:** The ADR-041 pre-merge gate reduces bad merges but cannot eliminate regret — a user can confirm "merge anyway" and later wish they hadn't. Because the Master Profile is a single JSON document, a pre-merge snapshot is a near-free, reliable escape hatch, reusing the existing enrichment-record trail rather than a parallel history system.

---

### ADR-044 — Unified Experience Abstraction (supersedes ADR-043)

**Decision:** Jobs, projects, and volunteering share one capability contract — a base `ExperienceBase` (role, dates, an `is_current` tri-state marker — `null` unknown / `true` ongoing / `false` ended, so a current position's missing end date is never treated as a profile gap — location, responsibilities, achievements, technologies) that `WorkEntry`, `ProjectEntry`, and `VolunteerActivity` each extend with kind-specific fields (`WorkEntry`: company, team size, budget; `ProjectEntry`: name, description, url, `associated_experience`; `VolunteerActivity`: organization, cause). Each kind keeps its own section-mapped list on the profile (mirroring CV sections), all stored additively in `profile_json` (ADR-002 JSONB — no new table, no migration; legacy profiles load via the `model_validator`). The profile-side derived computations — experience-years, skill accrual, and profile stats — iterate **all** experiences via a single `all_experiences` accessor (so volunteering/projects count toward domain experience). A project may link to *any* parent experience (a job or a volunteer role, by name/label) or stand alone. (Tailored-output rendering of projects in the PDF templates is a deferred follow-up; the field is captured, stored, and available to the tailoring input now.)

**Why:** The capability set (time span, applied skills, achievements, can-contain-projects) is orthogonal to the *kind* of engagement. The previous separate-entity model (a) wrongly denied volunteering achievements and skills, and (b) counted only `work_experience` toward experience-years and skill accrual — so e.g. managing software for an NGO never counted toward domain experience, a correctness defect for a tailoring product. CV extraction and the ADR-021 reviewer still capture projects **as projects** (not folded into work experience), with kind-appropriate anti-fabrication rules. A profile headline and an education `notes` field are deferred extensions; **references are deliberately not stored** (third-party personal data — data minimisation), and the reviewer treats their absence as correct, not as data loss.

---

### ADR-050 — Mobile Support via Responsive Retrofit

**Decision:** Mobile support is delivered by making the single Next.js frontend responsive with Tailwind breakpoints — there is no native app, no separate mobile route tree, and (for now) no PWA. Below the `md` breakpoint the fixed sidebar shell becomes a top app bar with drawer navigation, and feature pages stack single-column (390px is the supported floor). Existing panels and dialogs re-present as bottom sheets on small viewports; document-centric screens (CV review) use a floating bottom command bar whose actions open the same components used on desktop — divergence is presentation-only, logic and API calls are never forked. The decision requires zero backend changes.

**Why:** One codebase and one design system mean the mobile experience inherits every future feature by default, at the cost of having no offline/home-screen/share-sheet capability until a PWA layer is added later. New frontend contributions are expected to render correctly at 390px; a mobile-viewport Playwright project guards the core tailoring path.

---

### ADR-051 — Region-Keyed Length Norms & Length-Budgeted CV Tailoring

**Decision:** Document length norms are **data, not constants**: a region-keyed registry (`applire/norms.py`) holds each hiring region's norms — currently a single DACH row (CV: 2 pages standard, 3 max for senior profiles; cover letter: 1 page). No component hard-codes a page number; prompts, budgets, and audit checks all read the registry, so supporting a new market later means adding a row, not touching the pipeline. The page target for a generation resolves as: per-request `target_pages` (REST `POST /api/cv/generate` and the MCP `generate_cv` tool) > the user's `target_cv_pages` setting > the region standard — and is persisted on the `generated_cvs` row. Users may deliberately exceed the norm; the system obeys and the ATS `page-length` check reports the deviation as an advisory, never a failure.

Enforcement is two-stage and fully deterministic. **Feedforward:** before any LLM call, a pure budget computer (`applire/services/cv_budget.py`) assigns each work entry a bullet ceiling from recency × JD relevance (relevance = claimable Keyword-Ledger hits via the shared ATS presence predicate; when a cross-language JD/profile pair yields zero hits everywhere, tiers fall back to recency alone). The budgets ride into both generation paths (single-call and segmented outline-then-expand, ADR-047). **Feedback:** the end-of-generation audit render already yields the exact PDF page count; on overrun a bounded condense pass (max 2 iterations, no LLM) drops whole bullets in a fixed priority order — bullets without a claimable-keyword hit first, nested project bullets before role bullets, oldest roles collapsing toward one-liners — then re-renders and re-audits. Roles are never removed (a DACH CV must not show employment gaps), and condensation is omission-only: nothing is ever added or rewritten (ADR-040 boundary). The condense loop runs only during generation; user section-edits are never condensed, and the content snapshot is rebuilt from the final condensed data so the section editor can't resurrect trimmed bullets.

**Why:** A 6-page CV is discarded unread by many DACH reviewers — length is a domain rule for this product, not a taste preference. Detecting the overrun without a lever (the audit check alone) would just tell users about a problem the product caused; budget-then-condense makes the norm hold by construction while a deliberate user override stays in control.

**Amended (2026-07-16) — cover letters get enforcement too.** The registry row gains a `letter_body_word_budget` (DACH: 300 words) that feeds the letter prompt, and when the rendered letter still exceeds the region's page norm, exactly one condense-regenerate pass rewrites it to the budget — routed back through the full grounding review, since a letter is coherent prose with no whole-bullet omission unit (a deliberate, letter-scoped exception to the CV's "no LLM condense" rule). The pass is skipped when the user has edited letter sections (user edits always win), and the 1-page audit check remains as the honest backstop.

---

### ADR-052 — Truthfulness Oracle (accepted 2026-07-18, **v1 shipped**)

**Decision:** A core verification service (`applire/services/oracle/`) audits any document against the Master Profile with a failure mode *uncorrelated* with the LLM writer: deterministic claim extraction (bullets, sentences, skills), then **deterministic checks first** — number/date provenance (every percent/currency/year/number figure must trace to the profile), grounding via the same shared presence predicate the ATS checks use, and target-vs-achieved stance from versioned DE+EN marker data files (did "targets a 70% reduction" become "reduced by 70%"?) — with narrow, budget-capped LLM entailment calls only where determinism cannot decide, and structurally unable to overrule a deterministic red flag. Verdicts are typed (`grounded` / `inflated` / `misattributed` / `unbacked` / `unverifiable`) and carry pointers to profile evidence, including enrichment-history receipt ids (ADR-046). The `misattributed` verdict (v2 role attribution) is fully deterministic: tailored work entries carry their source experience id and vault evidence units know which experiences own them (an associated project's evidence also counts for the position it is nested under) — a claim whose only qualifying evidence belongs to a different position than the one it is rendered under is flagged, while same-role or role-agnostic (summary, skills) backing clears it; ambient year figures are exempt, and claims without a position anchor (raw external text, legacy data) are never flagged. Surfaces (all in the tree): a pre-delivery truthfulness report persisted with every generated CV/letter in the same commit that flips it `ready` (never blocking delivery — ADR-040 attestation stays the gate), `GET /api/cv/{id}/truthfulness-report` (+ cover-letter twin) feeding the UI report panel, and the `audit_document` MCP tool, which also accepts documents Applire did **not** write.

**Why:** An LLM reviewing LLM output fails in a correlated way — the same weights that inflated a claim will approve it. Blind adversarial testing (2026-07-18) showed exactly this class surviving the existing review pass; that exact case is now a regression fixture. Deterministic code checking against a provenance-carrying profile has a structurally different failure mode. Stated limit (embedded in every report): the Oracle verifies document ↔ profile consistency; it cannot prove the profile itself.

---

### ADR-054 — "Bring Your Own Intelligence": À-la-Carte MCP Surface (accepted 2026-07-18, amended 2026-07-22 — **four agent-door tools shipped**)

**Decision:** On the agent channel, Applire never competes with the calling agent. Strategy, prose, and interviewing belong to the caller when its model is strong; Applire offers what an agent structurally lacks, each as a standalone tool that requires no prior `generate_*` call: `audit_document` (ADR-052 — **shipped**: pass a generated-document id or raw external text, get the per-claim truthfulness report), `render_document` (**shipped**: agent-authored structured content in, norms-checked and templated PDF out, ATS + truthfulness reports attached inline), `submit_claims` (**shipped**: agent-elicited facts entering the profile reconciliation with agent-interview provenance), and `resolve_gap` (**shipped 2026-07-22**: resolve one gap cluster in a single stateless call — pass a `gap_id` from `analyze_gaps` plus the candidate's testimony, and Applire asks the scoped question, reconciles the answer, and marks the gap addressed). The built-in `generate_cv` / `generate_cover_letter` remain fully supported as the convenience path — the weaker the caller's model, the more of the pipeline it should use. The human-door (browser) experience is unaffected.

**Resolving gaps one at a time (`resolve_gap`):** the browser UI lets a user click a single gap and answer one scoped question to resolve it; `resolve_gap` is that same targeted micro-session projected onto the agent channel as a stateless one-shot — no session handle, no free-text "I'm done" signal to end an interview. "Continue or stop" is simply "call it again for the next gap, or move on to rendering". It is the *guided* per-gap path (Applire generates the question and reconciles the answer, updating the profile); `submit_claims` is the lower-level alternative that records agent-gathered testimony without a question. Both keep the anti-fabrication floor. The returned `status` is honest — `addressed` (the testimony wrote a change), `denial_recorded` (the testimony explicitly denied a skill — recorded to the vault, never silently dropped, so a later `analyze_gaps` cannot re-infer the denied concept via adjacency), `no_change` (a valid answer that added and denied nothing), or `needs_confirmation` (the reconciler needs the human to disambiguate; the parked prompt is returned). It refuses to run while a full interview is active for the job (so it can't silently discard that progress), and the `answer` must be testimony, not a control word. Agents that want a full multi-turn interview still use `run_interview`/`send_message`.

**The rendering contract (`render_document`):** the document content shapes are a **public, versioned contract**, published as MCP resources `schema://cv` and `schema://cover-letter` (`{schema_version, json_schema}`; currently `cv/1` and `cover-letter/1` — versions bump on breaking changes). Applire renders, checks, and *reports* — it never rewrites the caller's content: no condense pass (page-length norms are advisory checks in the ATS report), unknown fields are rejected with their field paths instead of silently dropped, letter date/sign-off chrome is injected only when omitted, and `photo_url` always comes from the stored profile, never from submitted content. Rendered rows carry `origin='agent'` and are badged in the UI ("Agent-authored") — third-party content is never presented as Applire-authored.

**The claims contract (`submit_claims`):** claims are **free-text testimony** — `{statement, question?, gap?}`, published as MCP resource `schema://claims` (`claims/1`, ≤20 claims per call). The caller never authors profile operations: agent = interviewer, Applire = notary. Each statement runs through the same reconciliation engine as the built-in interview, with a stricter anti-fabrication floor — only the candidate's *statement* can ground a skill/certification/figure claim (an agent-authored question cannot smuggle one in), and signature-story upserts are dropped when their outcome/benchmark figures don't appear in the statement. Ambiguous or conflicting claims are never silently applied; they are parked for the user to resolve in the profile Health hub (and survive later CV imports). Gap-linked claims (`gap` = an exact concept string from `analyze_gaps` output, requires `job_id`) upgrade the matching keyword-ledger entry only when the claim actually changed the profile. Claims are recorded, not verified — the profile stays self-attested (the ADR-052 limit, stated in the tool description).

**Why:** Capable agents already out-write built-in single-pass generation; forcing them through it subtracts value. Enabling them — while keeping verification, state, norms, and rendering on Applire's side — makes the tool *more* valuable as agent models improve, not less.

---

### ADR-055 — Signature Stories: a First-Class Profile Entity (accepted 2026-07-19)

**Decision:** The Master Profile gains `signature_stories` — typed narrative evidence (`title`, `challenge`, `mechanism`, `outcome`, `benchmark?`), anchored to the experience it happened in via `experience_refs` (the same provenance pattern skills use) and carrying no date span of its own. Stories are written **only through the ADR-046 reconciler** (a new `upsert_story` op whose prompt rules require the source material to actually narrate the arc — never synthesized from a bare skill or tag; normalized-title identity, append over ask on near-duplicates), land with full-prose enrichment receipts, and are indexed by the Truthfulness Oracle as evidence units — a figure stated only in a story's `outcome` still grounds a document claim, with a citable receipt. v1 ingestion is interview answers, agent claims (`submit_claims`, ADR-054), and profile-section edits (`signature_stories` is a patchable section); consumers are `get_profile` on the agent channel, the single-call CV writer, the Oracle, and a read-only profile-page section. Since E045, story upserts are covered by the deterministic stance guard on both interview sources: a story restating a denied token, or whose `outcome`/`benchmark` figures don't appear in the candidate's actual words, is dropped whole — story figures feed the Oracle's number provenance and cannot be paraphrased into existence.

**Why:** Blind testing showed the strongest candidate evidence — the concrete "what was hard, what I did, what it measurably changed" story — was being shredded into bullets and tags at import, so agents bypassed the profile for raw documents. A typed story survives intact, verifies deterministically, and is reusable across every application in a campaign instead of drifting per document.

---

### ADR-056 — Layered Agent Guidance & Honesty Contract (accepted 2026-07-19)

**Decision:** There is exactly **one** canonical agent-usage guide, shipped as package data (`backend/applire/mcp/AGENT_GUIDE.md`) and served through progressive disclosure rather than always-on prose: a `get_guide` MCP tool returns it on demand, the same content is exposed as the `guide://usage` resource and an MCP prompt, and the server's `instructions` field carries only a short pointer ("call `get_guide` before your first application run"). The guide states Applire's **honesty contract in two halves**: what is *enforced by Applire* (the `submit_claims` stance and figure guards — only the candidate's own statement can ground a skill/certification/figure; the pre-delivery Truthfulness Oracle audit on every generated document; render-never-rewrites on `render_document`) and what is *expected of the calling agent* (ground every document claim in the candidate's own data, never fabricate, surface honest gaps to the human instead of papering over them). Standing convention for the tool surface: a tool's schema description carries its contract plus **at most one load-bearing safety line**; all workflow guidance prose lives in the guide. A CI drift guard fails the build when any registered tool name is missing from the guide. `get_guide` sits alongside the surface as a meta/guidance tool — the à-la-carte application surface is the ADR-054 tools (four agent-door tools incl. `resolve_gap`, added 2026-07-22) + `get_guide` (ADR-056, meta).

**Why:** The 24 tool schemas already cost roughly 4.4k tokens of always-on context in every connected MCP client — growing each description with guidance prose taxes every call to pay for the first one. And of the possible delivery channels (server instructions, resources, prompts), a **tool call is the only channel every MCP client universally supports**, so the guide is served through one, with the other channels as mirrors for clients that do support them.

---

### ADR-057 — The Prism: Truthful Positioning as the Leading Principle (accepted 2026-07-21)

**Decision:** Applire's leading principle — above the four pillars — is the **Prism**: present the strongest case the candidate's data can *truthfully* support. Concretely: a gap identified by `analyze_gaps` is never treated as a deficiency until classified with the human — an evidenced strength is shown, a *likely-but-unstated* strength is elicited and imported (via `resolve_gap` / `submit_claims`) before being treated as absent, and a *true* gap is either positioned honestly or de-emphasized. Positioning means **truthful altitude selection**: argue the lowest level of abstraction at which the candidate has real, evidenced experience, with the level stated explicitly — bounded by the truthfulness audit (any inflated/unbacked/misattributed verdict rejects the claim). The doctrine lives in the agent guide (`get_guide`); no new required tools.

**Why:** Real-world testing showed a truthful pipeline can still produce a truthful-but-*flat* document — faithful to a profile that never captured the candidate's most relevant experience, and silent on why a gap isn't a deal-breaker. Verification without positioning is the flat CV; positioning without verification is lying. The two are designed as a pair.

*Amended 2026-07-24:* the deferred positioning step is partially un-deferred for the built-in letter, scoped strictly to **prompt-input threading**: the letter prompt now receives the JD's company/domain for concrete engagement, interview answers tagged to true gaps as the honest transfer argument, and an availability statement sourced from vault data only (detected concurrent open roles; nothing is invented when the vault is silent). No new LLM passes, templates, or UI positioning flow — those stay deferred. All positioning output remains bounded by the truthfulness audit.

---

### ADR-058 — One Capability Core, Two Doors: the Door-Parity Invariant (accepted 2026-07-23)

**Decision:** Applire's product is its **deterministic capability core** — the Master Profile vault (reconciliation/notary), the Truthfulness Oracle, norms-checked rendering, the application tracker, and the deterministic analysis layer (keyword ledger, ATS audit). Each capability is a named operation with a public contract, consumed identically by the web UI and the MCP tools. The standing **door-parity invariant**: no capability may exist only inside the built-in generation pipeline — anything the pipeline can do to a user's documents must be reachable à-la-carte with equal semantics (same validation, same norms enforcement, same audit hooks); intentional differences are named parameters, never accidents of the entry path. Parity is CI-tested. The built-in pipeline itself is reframed as Applire's **bundled agent** for users who don't bring their own: fully supported, but with a written capability floor — it is frozen at its current feature set (bugfix-only) rather than competing with caller models on prose. The flow state machine becomes a browser-side convenience: no core operation requires a flow session.

**Why:** Every capability that lived only inside the pipeline eventually surfaced as an agent-door limitation discovered the hard way. Making parity a tested invariant turns that defect class into a failing build — and it resolves the human-door/agent-door tension structurally: both doors are callers of the same core, so the user without an agent loses nothing while the user with one is never boxed in.

*Amended 2026-07-24:* the freeze boundary is now ruled explicitly. **Allowed under the freeze:** threading *existing* vault/ledger/interview data into *existing* prompts, prompt-rule wording, and deterministic guards or post-passes — these improve inputs, not capability. **Recorded exceptions (both bounded):** the letter positioning inputs (see the ADR-057 amendment) and one interview prompt rule — for cluster concepts already evidenced but unquantified, the existing question generator may ask one quantification follow-up (team size / scale / outcome), and may ask availability once when the job description requires it and the vault holds none; "I don't have numbers" is a valid terminal answer, never re-asked. No new modes, engines, or question-ceiling changes.

---

### ADR-059 — Negative Testimony as First-Class Vault Data (accepted 2026-07-23)

**Decision:** An explicit denial ("I did not personally configure the embedding models") is treated as testimony, not as a no-op. It persists in the profile metadata as a `denied_concepts` entry (concept, verbatim statement, source, date) with an enrichment receipt even when nothing else changed; the caller — `submit_claims`, `resolve_gap`, or an interview turn — receives the honest status `denial_recorded` instead of `no_change`. The keyword ledger then enforces a **deterministic denial floor**: any concept matching a persisted denial is forced to `gap` / not-claimable with an honest evidence marker, and no LLM adjacency inference ("RAG typically involves embeddings") can override it. Matching reuses the reconciler stance guard's own predicate — alias-aware, word-boundary-safe for short tokens ("AI"/"ML" never match inside ordinary words), unicode-folded — so the same-turn guard and the durable floor can never disagree. Denials are concept-scoped, never topic-radius: denying hands-on embeddings work leaves an evidenced "RAG" claim intact.

**Why:** The truthfulness product must never invite an untruth. Before this, a candidate's honest limit vanished silently and the next gap analysis re-marked the denied capability as claimable — inviting the agent (or the user) to write it into a CV. Known open point: there is no un-denial path yet; superseding a stale denial after genuine counter-testimony is a deliberate follow-up decision.


**Amended (2026-07-27) — a denial changes the requirement's status.** ADR-059 always said a denial must never mark a gap addressed or upgrade the ledger, but that rule lived only in the build-time floor. The per-turn interview path, which upgrades a requirement the moment an answer changes the profile, had no notion of an answer's *polarity* — so an honest answer that denied one requirement while supplying real evidence for another could flip the denied concept to claimable, with the candidate's own denial sentence stored as its supporting evidence. Polarity is now consulted at every point the ledger is written, not only when it is built; a denial is recorded as the requirement's status rather than silently discarded; and a requirement's status is scoped to the job that asked for it, while the verbatim statement stays in the profile — not as a record of an absence, but because that sentence is already in the vault and must be marked as a limit so nothing reads it as evidence, and because the honest "what I do bring instead" half of it is what a cover letter argues from. Detecting that an answer *is* a denial remains a model judgement; a deterministic cross-language classifier was considered and rejected.

---

### ADR-062 — Facts are Deterministic, Judgements are the Model's (accepted 2026-07-28)

**Decision:** Code in the generation path may compute a **fact** and hand it to a writer or reviewer prompt as ground truth. It may not compute a **judgement** and do the same.

A *fact* is settled by the data structure alone — a keyword-ledger status, an enum, a count, a date, a page budget, a schema type, whether a document has a closing paragraph. A *judgement* requires reading prose for meaning: whether a stated limit bounds a particular claim, whether a sentence testifies to availability, whether prose demonstrates leadership, whether a bullet reports a target or a result, whether a negation attaches to the concept next to it. If a rule has to read prose to reach its verdict, it is judging, whatever its implementation.

Judgements go to the model **with the facts and one rule**. The reference implementation: the vault hands the writer the candidate's persisted denial statements verbatim and unpaired, plus the single instruction *"a concept named inside a stated limit as something the candidate DOES have is a STRENGTH, not a limit."*

Three supporting rules:
- **Deletion over repair.** A heuristic of this class found wrong is removed, along with whatever was built on it — a tuned matcher keeps the defect and adds a constant.
- **No prompt may carry two blocks that can contradict each other about one concept.** Overlapping deterministic blocks are reconciled before the prompt is built, and the keyword ledger arbitrates. A contradiction reaching the model is a defect in the caller, and it is how a review loop fails to terminate.
- **Two exemptions**, both narrow: heuristics whose output is a *measurement* (a log or a metric, never an instruction), and fail-safe scrubbing — PII redaction and the ADR-040 never-claim floor stay deterministic and stay deliberately over-broad, because they fail toward *saying less* rather than toward *saying something untrue*.

**Why:** A code rule that pattern-matches text to decide a question of meaning does not merely lose precision — it can be *anti-correlated* with the truth. The decision was forced by a real case. A matcher paired each vault denial to the claimable concepts it supposedly limited, by testing whether their text overlapped. But an honest denial names the adjacent strengths that transfer ("no IFS/BRC experience, but ten years of ISO 9001 audit practice"), so the concepts a denial overlaps hardest are exactly the ones it does *not* limit. It flagged four of one candidate's strongest qualifications as bounded — twice quoting the same clause as both the evidence and the limit — and the writer, told to name "both halves" for each, produced a letter denying experience the profile positively evidenced.

Removing that matcher was not enough, and the second finding is what made this a decision rather than a bug fix. Three deterministic blocks reached the reviewer in one prompt, each labelled as ground truth, disagreeing about one concept: the ledger marked it claimable, an unmet-requirement check demanded a gap argument for it (while naming the supporting project in its own evidence field), and a consistency check forbade naming it as an absence. No draft can satisfy all three, so the loop ran to exhaustion.

There is also a testing consequence worth stating plainly for contributors. CI mocks the LLM provider, and a mock cannot evaluate what an instruction *does*. A marker list passes its own unit tests by construction; the question it cannot answer is how a real model behaves when told to obey it. Every failure in this class was found by a real-provider end-to-end run, several after surviving multiple releases. Changes to prompt-facing rules are therefore evidenced by a real run, not by a green suite — CI keeps its job of pinning shape, wiring, and the facts.

**A fourth supporting rule, added by amendment on the same day:** the fact/judgement line applies to a control's **remediation** as much as to its detection. The cover-letter figure guard removes an unattributable number by deleting the sentence that carries it — a decision that was right about every individual figure and wrong about the sentence. In one German letter it deleted the closing paragraph (a tenure figure, "14 years of Lean expertise", collided with two unrelated counts that happen to contain 14) and four genuine achievement figures from another paragraph. Both were facts computed wrongly and both are fixed; the judgement underneath — *which employer is this sentence about?* — is not, because that guard runs **after** the review loop finishes, so nothing downstream sees what it removed and it has no model-side replacement today. The disposition is the same as the rest of this ADR: give the reviewer the fact (which vault entries a figure appears under) and let it judge and rewrite, rather than deleting text behind everyone's back.

The general lesson for contributors: a check that runs last is a check nothing else can catch. If you add a deterministic pass after the review loop, assume its mistakes ship.

**Status:** The rule is in force for new code. Six existing sites are known violations and are being replaced incrementally rather than all at once.

---

### ADR-063 — One Write Path: Every Vault Mutation Is a Typed Op (accepted 2026-07-28)

**Decision:** There is exactly one piece of code permitted to write the Master Profile. Everything that wants to change the vault produces a list of typed reconciliation ops and hands them to that committer, which owns the transaction and every invariant: pre-merge snapshot, stance and attribution guards, denial ledger, skill enrichment, the enrichment trail, and the completeness recompute.

Three terms that had been used interchangeably are now distinct, because conflating them is what allowed the defects below:

| Term | Means | How many |
|---|---|---|
| **Channel** | how a request arrives — the web UI (REST) or an agent (MCP) | 2, fixed |
| **Intake** | *what* arrives — `Document`, `Statement`, `FieldEdit`, `Decision`, `Binary` | extensible |
| **Write path** | the code turning an intake into committed vault state | **exactly 1** |

Intake adapters are pure functions of the shape `(payload, profile) -> list[ReconcileOp]` — no database, no LLM, no async — so they are unit-testable in isolation. Adding a new intake adds one pure function. Adding a new channel adds none: a channel selects an adapter and supplies provenance, and may never vary the invariant set. Where behaviour legitimately differs it is a named parameter, per the door-parity invariant in ADR-058.

Enforcement is structural rather than editorial: writes are guarded at the attribute itself, so the rule holds regardless of how the write is spelled. The first draft of this ADR specified a source grep instead, and an adversarial review killed it before implementation — three existing sites write the field as a constructor keyword argument (`MasterProfile(profile_json=…)`), which no assignment-pattern regex can see. That correction is worth more than the rule it fixed: **an enforcement mechanism that can be defeated by a change in call syntax is not enforcement**, and the only reason it was caught is that the decision was reviewed before any code was written against it.

**Why:** An audit measured seven invariants across every code path that writes the profile. The architecture documentation described five such paths; the grep found sixteen assignment sites across nine modules — eleven distinct writers. The documentation was not *wrong* about any individual writer. It was **lossy**: eleven writers had been grouped into five narrative descriptions, and prose can be accurate about each one while making the comparison *between* them impossible. That is the general lesson, and it is worth more than the specific bugs — when a component has many similar-but-not-identical implementations of the same responsibility, the useful artefact is a matrix, not a description. Four defects had been sitting in plain sight in that description:

- **Undo only ever worked for imports.** The pre-merge snapshot documented as unconditional had two call sites, both on the CV import path. Undo after a testimony submission or an interview restores a much older snapshot and discards everything since. The response does carry a `discarded_later_edits` warning — but it is derived from the enrichment trail, so the trail-less writers below make it silently under-report. And no UI control or MCP tool calls the endpoint at all, so the guarantee is currently unreachable through either door.
- **Four writers left no audit trail**, making their changes invisible in the enrichment history — and, as a consequence, invisible to the undo warning above.
- **The CV section editor bypassed every truthfulness guard.** Text edited in a generated CV and saved back to the profile skipped reconciliation, the stance and denial checks and the attribution guard. Since the Truthfulness Oracle treats the vault as ground truth, a claim the Oracle had rejected on the document could be entered through the editor and thereafter count as grounded — the guard could be routed around by the person it protects.
- **One operation behaved differently depending on the channel.** A skills edit through the UI was enriched; the identical edit through the MCP tool was not, because the two call sites passed different arguments. This is precisely what ADR-058's parity invariant forbids, and it survived because that ADR's parity test suite had not been built yet.

The remedy needed no *new abstraction*. ADR-046's typed op vocabulary was already the right one — the writers that used it were the ones holding the most invariants. It had simply never been made mandatory, so the others mutated the profile dictionary directly, and those were exactly the ones missing the guards. It does need some new **ops**: `SetField` is fill-only by design (a real change is meant to go through `FlagConflict`), so an authorised overwrite of a disputed field, a boolean flip such as closing a role, and a profile-level metadata write each need an op that does not exist yet. That is listed as work rather than assumed away — an earlier draft of this ADR claimed the vocabulary already reached that far, and it does not.

One more correction worth knowing, because it changes what the committer is: **it flushes, it does not commit.** The interview path deliberately leaves persistence to a caller that writes interview-session state in the same transaction, so a committer that closed the transaction itself would split an atomic write in two. The committer owns the invariant set and the write; the caller owns the transaction boundary.

**Consequence worth knowing as a contributor:** the contract test suite for the vault is eight assertions against the committer, plus the one-line structural gate proving there is nothing else to test — rather than eleven writers times eight invariants, a suite that grows with every writer and silently misses the twelfth.

**Trade-offs accepted:** writing a single field now means constructing an op, which is more ceremony than assigning to a dict. The profile photo is an explicit non-participant — binary data fits an op vocabulary poorly — and is named as such rather than being silently absent. Bringing the existing eleven writers onto the single path is a real if bounded refactor, sequenced over several release cycles; the correctness fixes (the section editor, the channel asymmetry) land first and independently.

---

### ADR-064 — A Denial Is Scoped to the Level It Denies (accepted 2026-07-29)

**Decision:** A denial records *which level* it denies, as `denial_level: "direct" | "partial" | None`. It is stored on the denial record itself (`ProfileMetadata.denied_concepts[]`) and mirrored onto the Keyword Ledger entry, which is rebuilt from scratch on every gap analysis. The four-value `status` enum is unchanged.

- `denied` + `direct` — the full match is ruled out, **adjacent coverage is still unknown**
- `denied` + `partial` — adjacent is ruled out too, the question is exhausted

On a direct-level denial of a JD-critical requirement, the interview asks **exactly one** follow-up about the *skill area* rather than the named form — "you don't have TOGAF; did you work with other architecture frameworks?" A second denial is terminal. The trigger is a deterministic read of the field; only the question's wording comes from the model.

A probe can **never un-deny**. The original denial stands permanently and is never claimable. What a probe can produce is separate, directly attested evidence, which makes the *requirement* partial. Stated as the rule an implementation must satisfy: **partial by attested adjacent evidence, never partial by inference over a denial.**

Answer choices gain a **coverage** rule alongside their truthfulness rules: where the question admits them, one option at each level, and the denial option is always present and never softened.

**Why:** The ledger has had a `partial` state since the keyword-ledger work shipped, and the interview could never produce it. Trace one candidate: asked "5+ years of TOGAF?", holding eight years of Zachman, they answer "No". The reconciler records a denial, the interview's gap cursor advances on any denial, the denial persists, and a ledger floor bars the entry from ever rising above "gap". The Zachman question is never asked, and that evidence could not register even if it arrived by another route.

Three surfaces caused it and they share one root: **everything that collects information was binary, while only the thing that stores it had three levels.** The status vocabulary could not say "direct ruled out, adjacent unknown". The code prohibited the probe — a denial cannot reach a follow-up at all, because the follow-up branch is only reachable when an answer was *neither* addressed nor a denial. And the suggested answer choices carried four rules about *truthfulness* and none about *coverage*, so the model drafted several variations of the single most plausible answer — flavours of "yes". Since those choices pre-fill the candidate's answer, that last one is an honesty defect in the interface itself.

A note for anyone reading the code alongside this: `prompts/interview.py` still contains a `RESPONSE_PARSER_SYSTEM_PROMPT` with a three-way `gap_resolution` enum and a rule *"do not keep probing a gap the candidate has explicitly ruled out"*. **That prompt has no call sites** — denial detection moved to the reconciler and this was left behind. It is removed as part of implementing this decision. It is named here because the decision's own first draft blamed it, and a fix written there would not have run.

The net effect is worth naming precisely, because it runs opposite to the usual concern: the system recorded a **broader denial than the candidate made**, suppressing true and relevant experience. On a product that competes on honesty, under-claiming is the more damaging failure, and it is much harder to notice than overclaiming.

Two things the implementation changed about the decision, both worth knowing if you read this code:

- **"Have we already asked?" is a separate field from "what did they say?"** `DeniedConcept.probe_asked` records that the one permitted probe was *issued*; `denial_level` records what the candidate actually stated. Tying the bound to `denial_level` alone let a second probe fire when a probe went unanswered — and the tempting repair (treat a vague answer as a denial of adjacency) would record a denial nobody made, which is the exact defect this decision removes. **A control's bookkeeping does not belong in a field that means something a person said.**
- **Each answer choice is tagged with its level by the generator** (`{"text", "level"}`), and deterministic code compares that tag rather than guessing from the wording. This replaced a phrase-list matcher that was **silently deleting honest denial choices** as if they were unsupported claims — so the denial option was doubly unreachable: never drafted, and removed when it was. The public API still returns plain strings.

**Status:** The ledger floor is **unchanged** — narrowing it is accepted in principle but not built. The attempt let a denied concept's own adjacency reasoning survive as claimable whenever an unrelated sibling term was present, which is the incident the floor exists to prevent. It also turned out not to be needed for the case that motivates this decision: the adjacent evidence a probe elicits becomes *its own* ledger entry, which the denial never matched, so the floor never blocked it. `denial_level: "partial"` closes further questioning, not the entry: a later CV import or testimony can still move it.

---

### ADR-065 — Skills Decompose into Skill and Specialisation (accepted 2026-07-29)

**Decision:** A skill has two levels — the skill area and the **specialisation**, the named form of it. Job requirements decompose the same way, so a ledger entry holds the skill area, the specialisation and any threshold separately instead of one glued string like "5+ years TOGAF".

| skill | specialisation |
|---|---|
| Projektmanagement | PRINCE2 · Scrum · IPMA |
| Enterprise Architecture | TOGAF · Zachman · ArchiMate |
| Qualitätsmanagement | ISO 9001 · IATF 16949 |
| Rechnungslegung | IFRS · HGB |
| Pflege | Intensivpflege · Anästhesiepflege |
| Lean Management | SMED · 5S · Kanban |

The point is that partial coverage becomes **computable rather than elicited**: same skill area, different specialisation, so the requirement is partially met — deterministically, with no interview turn. `specialisation` is the field name because it is the only word with coverage across occupations; "method" fails for AWS and Intensivpflege, "standard" fails for Scrum, and "technology" fails for everything outside IT.

**There is no internal library, taxonomy, or lookup table, and the ADR forbids adding one later.** Deciding that PRINCE2 is a form of Project Management is a *judgement*, so under the fact/judgement rule it belongs to the model — derived from its world knowledge at extraction time and then stored as data. A curated hierarchy covering every European occupation would be permanently incomplete and is exactly the maintenance treadmill this project is built to avoid. Deterministic code **compares** the stored values; it never derives a hierarchy, widens a skill, or decides that two differently-named skill areas are the same.

A hallucinated decomposition is caught by a **review pass, not a rule table** — detecting nonsense is also a judgement. A flagged decomposition is written `unconfirmed`: visible, confirmable by the candidate, never claimable. Nothing here gates document delivery.

**The main risk, stated up front:** the two decompositions have to agree on a label. If a job description yields "Enterprise Architecture" and the profile holds "IT-Architektur", the matching problem has moved up one level rather than been solved. The mitigation is *priming*, not matching: job analysis is given the skill labels already in the profile and told to reuse one where it fits. If measurement shows frequent disagreement, the decision gets revisited — not patched with a fuzzy label matcher, which would be precisely the mistake the fact/judgement rule exists to prevent.

**Also settled here:** `work_experience[].technologies` is removed, along with the extraction rule that fed it. It was a bag of tool names attached to the wrong entity — rendered by none of the CV templates, absent from the tailored-CV schema, with no status slot, and duplicating the skill's own experience references in the opposite direction. The skill/specialisation pair replaces what it was reaching for, on the entity that already carries provenance, status and evidence.

**Sequencing:** ADR-064 ships first and works alone — the interview asks the follow-up. This decision then makes most of those follow-ups unnecessary, and gets built with real data from 064 about how often a probe actually finds partial coverage.

---

### CV Theming & Color (ADR-020, 023, 024, 025, 026)

A cluster of Community rendering decisions a contributor will encounter in the CV pipeline:

- **ADR-020 — Icon Set Registry:** Optional inline SVG icons live in a Python dict registry (`applire/icon_sets.py`); `icon_set` is `none | outline | filled`. All SVGs use `currentColor`, so icons recolour automatically with the template's CSS accent. New sets = one dict block.
- **ADR-023 — CV color profiles are a separate system from app color schemes.** App `color_schemes` are instance-wide UI theming (operator-managed, long-lived); CV `color_profiles` are per-document artifacts generated at CV-creation time and garbage-collected with the 90-day `generated_cvs` TTL. The two are not linked at the data-model level.
- **ADR-024 — Companies Registry:** A `companies` table caches scraped brand data by domain (favicon/meta colors) so repeated applications to the same employer cost zero marginal scraping. Intentionally more than a cache — a foundation for future company-level intelligence.
- **ADR-025 — Color detection cascade:** favicon-first → meta-tag → LLM last resort → user default → system default `#2b5fa8`. No paid brand API in Community (Brandfetch/Clearbit are deferred to Cloud — they would add a US sub-processor). EU-hosted Mistral only for the LLM fallback.
- **ADR-026 — Multi-slot color schema:** `ColorContext` exposes `primary`/`surface`/`secondary`/`surface_text` etc., all derived at render time from a single seed hex (Phase 1, no DB migration). `surface_text` is auto-derived via WCAG luminance so no template can pick a low-contrast color.

---

### ADR-033 / ADR-034 — nginx Reverse Proxy as Standard Entry Point

**Decision:** An `nginx` service is the standard entry point for Community Edition. All external traffic enters on port 80; nginx routes `/api/`, `/static/`, and `/health` to the backend container and everything else to the frontend. `NEXT_PUBLIC_API_URL` is set to `""` (empty string), so all frontend `fetch()` calls use relative paths (e.g. `/api/profile/exists`). The browser resolves these against the current origin — no configuration needed regardless of hostname, IP, or DHCP lease.

**The proxy config ships inside a published image (amended 2026-07-03).** The nginx service uses `ghcr.io/applire/applire-nginx`, built from `nginx/Dockerfile` (`FROM nginx:alpine` + `COPY self-hosted.conf`), so the routing config travels *inside* the image rather than being bind-mounted from the host. Every service in the self-host compose is now a pre-built image, so `docker compose pull && up -d` fetches a complete, working stack with **no config file to place on the host** — previously a self-hoster who wrote their own compose or fetched only `docker-compose.yml` got an nginx with no server block and had to debug it by hand. Operators who want a custom domain or TLS can still bind-mount their own file over `/etc/nginx/conf.d/default.conf`. (The dev stack keeps stock `nginx:alpine` + a mounted `nginx/dev.conf` for HMR.)

**Why this matters:** Same-origin routing eliminates CORS as a problem class entirely. Self-hosters do not need to know or configure their server's IP address; `docker compose up` produces a working stack reachable from any machine on the network.

**Why nginx over Traefik (ADR-034):** The four-service topology (`postgres`, `backend`, `frontend`, `nginx`) is static, so Traefik's Docker-label auto-discovery adds no benefit — and it would require Docker-socket access (effectively host root), an unjustified liability for a privacy-conscious self-host product. A single ~40-line config file is auditable and universally familiar to operators.

**TLS:** Out of scope for Community (self-hosters run on a LAN or behind their own proxy). The Cloud Edition terminates TLS via **Caddy** (ADR-036) — a Cloud-only concern not present in this repository.

**Ports** (canonical list — other docs reference this table rather than restating it):

| Port | Purpose |
|---|---|
| **80** | nginx — the entry point for the whole stack; this is the URL you use (`http://localhost`) |
| 5433 | PostgreSQL — published for direct DB access / inspection |
| 11434 | Ollama — only when started with `docker compose --profile ollama up` |
| 3000 / 8001 | Frontend / backend **only when run standalone in development** (`next dev` / `uvicorn --port 8001`). In the Docker stack these stay internal (`expose`d, not published) and are reached through nginx on port 80. |

---

## 4. Data Model Highlights

### Master Profile JSONB Shape

```json
{
  "personal_info": { "name": "string", "email": "string", "phone": "string", ... },
  "work_experience": [{
    "id": "uuid",
    "company": "string",
    "role": "string (primary title)",
    "role_aliases": ["Team Lead", "2nd Level Support Engineer"],
    "start_date": "2020-01",
    "end_date": "2023-06",
    "is_current": false,
    "responsibilities": ["bullet 1", "bullet 2"],
    "achievements": ["achievement 1"],
    "technologies": ["Python", "FastAPI"]
  }],
  "projects": [{
    "id": "uuid",
    "name": "string",
    "description": "string",
    "technologies": ["string"],
    "achievements": ["quantified outcome"],
    "start_date": "2021-03",
    "end_date": null,
    "associated_experience": "name/label of a work or volunteer entry (optional)"
  }],
  "skills": [{ "name": "Python", "proficiency": "expert", "years_experience": 8 }],
  "education": [{ "id": "uuid", "degree": "M.Sc.", "institution": "string", "year": 2015 }],
  "certifications": [],
  "languages": [],
  "signature_stories": [{
    "id": "uuid",
    "title": "short label",
    "challenge": "what was hard / the situation",
    "mechanism": "what the candidate actually did or built",
    "outcome": "the measurable result — stated figures verbatim",
    "benchmark": "what makes the number meaningful (optional)",
    "experience_refs": ["id of the experience it happened in"]
  }],
  "metadata": {
    "completeness_score": 0.85,
    "pending_conflicts": [],
    "enrichment_history": []
  }
}
```

### Key Tables

| Table | Purpose | GDPR TTL |
|---|---|---|
| `users` | Identity record | Soft-delete after 730d inactivity |
| `master_profiles` | JSONB career data | Soft-delete after 730d inactivity |
| `job_analyses` | Parsed JD data | No TTL (not PII) |
| `gap_analyses` | Gap detection results | No TTL (linked to job) |
| `interview_sessions` | Interview state (JSONB) | 30-day hard delete |
| `flow_sessions` | Journey routing record | No TTL (no PII) |
| `generated_cvs` | PDF + snapshot + overrides | 90d (uniform, origin-blind) |
| `uploads` | Raw uploaded files | 7-day hard delete |

---

## 5. Community vs. Cloud Boundary

This repository is the Community Edition. The table below documents what is and is not in scope.

> **Cloud status (ADR-053, 2026-07-18):** the Cloud Edition buildout is **paused** — no launch dates. The boundary below still governs the code: the auth abstraction and edition gating stay intact and tested, so nothing in Community may hard-code single-edition assumptions. "Cloud ✅" rows describe the design intent for when the pause lifts, not a shipped product.

| Feature | Community | Cloud |
|---|---|---|
| Master Profile (JSONB, enrichment, conflicts) | ✅ | ✅ |
| JD analysis + gap detection | ✅ | ✅ |
| Interview Orchestrator (Mode A + B) | ✅ | ✅ |
| CV generation (7 templates: Classic German, Modern Swiss, Executive, Academic, …) | ✅ | ✅ (+ premium themes) |
| CV Section Editor (Finetuner) | ✅ | ✅ |
| Cover letter generation | ✅ | ✅ |
| Profile Enrichment (Mode C, no-JD) | ✅ | ✅ |
| Deterministic match score | ✅ | ✅ |
| MCP Server (stdio) | ✅ | ✅ |
| Flow Orchestrator | ✅ | ✅ |
| GDPR Retention Worker | ✅ (configurable; user-artifact auto-delete off by default) | ✅ (strict, mandatory) |
| Right to erasure (`DELETE /api/profile`) | ✅ | ✅ |
| Auth provider abstraction | Interface + `NoAuthProvider`; OIDC self-host opt-in (Keycloak/Authentik) | ✅ |
| Auth enforcement (managed Zitadel OIDC) | ❌ | ✅ |
| Recruiter features (ranking, matching, scoring — EU AI Act high-risk) | ❌ (candidate-side only, minimal risk) | ✅ (regulated module) |
| Managed hosting | ❌ | ✅ |
| MCP Cloud Layer (SSE + auth + metering) | ❌ | ✅ |
| B2B multi-tenancy (RLS) | ❌ | ✅ |
| Recruiter Intelligence | ❌ | ✅ |
| S3 storage backend | ❌ | ✅ |
| Analytics dashboard + billing (Paddle) | ❌ | ✅ |

---

## 6. Testing Strategy Summary

Three tiers (see `docs/TESTING.md` for full details):

| Tier | When | Blocking |
|---|---|---|
| Unit tests (`pytest tests/unit/`) | Local, pre-commit | No (advisory) |
| CI: unit + integration + E2E | GitHub Actions, post-commit | **Yes** |
| Manual QA | Pre-rollout | **Yes** |

Coverage gate: `≥75%` backend unit coverage (`--cov-fail-under=75`).

**All CI tests mock LLM providers** — never call real Mistral/OpenAI/OpenRouter in CI. Unit tests run without Docker. Integration tests spin up the full Docker stack automatically.

**Module system:** All JavaScript/TypeScript uses ES modules (`"type": "module"` in both root and `frontend/package.json`). Never use `require()` in test files.

---

## 7. Key Files Quick Reference

| File | Purpose |
|---|---|
| `backend/applire/constants.py` | All thresholds, TTLs, edition flags |
| `backend/applire/services/flow/orchestrator.py` | Flow state machine — `VALID_TRANSITIONS` |
| `backend/applire/services/interview/signals.py` | Done-signal detection (deterministic, no LLM) |
| `backend/applire/auth/base.py` | `AuthProvider` ABC |
| `backend/applire/providers/` | LLM, OCR, Storage factories |
| `backend/applire/routers/cv.py` | CV HTML + PDF endpoints |
| `backend/applire/edition.py` | `HAS_CLOUD_EDITION` import-based detection |
| `frontend/components/cv/CVPreview.tsx` | CV preview (`srcDoc` pattern — never `src`) |
| `nginx/dev.conf.template` | nginx routing: `/api/*` → backend, `/*` → frontend |
