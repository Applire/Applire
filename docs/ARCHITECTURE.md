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

**Why not LangGraph:** LangGraph was evaluated and deemed over-engineered for a 4-node linear graph. The custom state machine is simpler, has no additional dependencies, and is independently testable per node. LangGraph may be revisited if the workflow grows to 8+ nodes with conditional back-edges.

**Two modes:**
- **MODE A (Targeted):** User has profile data. Focuses on filling specific gaps from gap analysis. 3–12 questions.
- **MODE B (Guided):** New user, no CV. Builds the profile section by section. 10–20 questions.

Mode is auto-detected at session creation from `completeness_score` vs `MODE_B_COMPLETENESS_THRESHOLD` (0.3), but can be overridden.

**Key invariant:** One active session per `(user_id, job_id)`. `POST /api/session` is idempotent — returns the existing session with `resumed: true` if one exists.

---

### ADR-005 / ADR-017 — GDPR Retention Worker

**Decision:** A dedicated `retention` service in Docker Compose runs `python -m applire.retention` daily and enforces four TTL rules:

| Entity | TTL | Action |
|---|---|---|
| `uploads` | 7 days | Hard delete |
| `interview_sessions` | 30 days | Hard delete |
| `generated_cvs` / `generated_cover_letters` | 90 days (human) / 24 hours (agent) | Hard delete at `expires_at` |
| `master_profiles` / `users` | 730 days inactivity | Soft delete (`deleted_at`) |

**Why a separate service:** Data hygiene is a core operational concern (ADR-017 formalises the worker as a first-class operational building block, not a peripheral add-on). Each run emits a JSON report to stdout for audit.

**Consequence:** Every model that holds personal data must carry `expires_at` (transient data) or `updated_at` + `deleted_at` (permanent data) from the first migration.

**Two distinct concerns, gated differently (Community vs. Cloud):**
- **Right to erasure** (`DELETE /api/profile`, exposed in the UI) is always available in every edition. It is a baseline data-subject right and does not depend on the background worker running.
- **The automated daily worker** lives in Core but applies edition-specific defaults. In a single-user self-host you manage your *own* data (candidate-side only, see ADR-015), so transient-data cleanup (`uploads` 7d, `interview_sessions` 30d) stays on by default as hygiene, but **automated deletion of your own generated artifacts (`generated_cvs`, `generated_cover_letters`) defaults to disabled** (`GENERATED_DOCUMENTS_TTL_DAYS=0` ⇒ no auto-expiry) so you keep your own CVs. A self-hoster who operates the instance *for others* should re-enable strict TTLs.

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

**Why direct SDKs over LangChain:** Consistent with the decision not to use LangGraph (ADR-004). Reduces the dependency surface and keeps the provider contract narrow and testable.

**Temperature defaults:** `0.3` for free-text completion (`acomplete`), `0.1` for structured JSON parsing (`aparse_json`).

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
| Same skill, different proficiency levels | Keep higher, no conflict |
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

Returning users skip `cv_import`; users with a sufficiently complete profile may skip `interview`. A user with a job but **no CV** takes the no-CV onboarding path `jd_analysis → interview` (skipping `cv_import` + `gap_analysis`) — the guided interview builds the Master Profile from scratch, then `interview → cv_generation` as usual.

Key invariants:
- One `flow_session` per `(user_id, job_id)`, enforced by a unique constraint.
- `user_type` (`"new"` | `"returning"`) is resolved once at flow creation and is **immutable** for the lifetime of the flow.
- Steps that produce artifacts (gap analysis, interview, cv generation) require `artifact_id` in `AdvanceFlowRequest` — missing `artifact_id` returns HTTP 422.
- Invalid step transitions return HTTP 409 with `allowed_transitions` for client recovery.
- `flow_sessions` carries no PII — it is a routing record. GDPR TTLs live on child records.

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

---

### ADR-021 — LLM Review Layer (Retry-with-Critique)

**Decision:** A generic `review_and_refine()` function (`services/reviewer.py`) wraps high-risk LLM generations. After the generator produces a draft, a second *reviewer* LLM call receives the original source material plus the draft and returns `{ approved, issues, feedback }`. On rejection (with retries remaining), the feedback is prepended to the generator prompt and a new draft is produced. The loop exits on approval or when `LLM_REVIEW_MAX_RETRIES` (default `2`) is exhausted — it **never raises**; a degraded last draft is preferable to a broken flow.

**Where applied:** profile extraction and CV tailoring (the two places hallucinations were observed — phantom work entries, JD-matching bullets with no CV basis). Setting `LLM_REVIEW_MAX_RETRIES=0` disables the layer entirely for cost-constrained self-hosters.

**Amended (2026-06-22):** the CV-extraction reviewer's fabrication test was recalibrated from *verbatim* matching to *semantic* faithfulness — paraphrase, sentence splits, and de-duplication merges are no longer flagged as "invented content" (they were exhausting both retries on nearly every upload, ~doubling latency, and crowding out real errors). In exchange, **cross-role content misattribution** (an achievement landing under the wrong employer/role on a multi-role CV) and **invented dates** (any date absent from the source must be `null`) are promoted to priority checks. The loop, schema, and retry default are unchanged.

---

### ADR-027 — Cover Letter as a Parallel Document Artifact

**Decision:** Cover letters are a fully separate pipeline — their own `generated_cover_letters` table, service, router, and Jinja2 templates — **not** a discriminator column on `generated_cvs`. Each of the CV templates has a matching cover letter template (shared header style, typography, color profile) for a coherent application package.

Cover letters are **post-CV add-ons**: triggered from the CV page after the CV is ready, one per `job_analysis_id`. The flow state machine is not extended. Pre-generation inputs surface DACH conventions (Gehaltswunsch, Eintrittstermin, tone). They inherit the same 90-day/24-hour retention as generated CVs (ADR-005).

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

---

### ADR-039 — ATS Parseability (Every Template, Local Audit, CI Guarantee)

**Decision:** Every CV and cover-letter template must be ATS-parseable — there is no dedicated "ATS template" (the "Universal ATS" theme once named in ADR-006 was withdrawn; a user has no reason to pick a template that hurts their application). A deterministic, fully local audit engine (`applire/services/ats_audit.py`, pypdf text extraction — no LLM, no network, no external checker services) verifies after each render that contact details, every work-history entry, education, and skills survive machine parsing in the correct reading order, and reports which of the job's ATS keywords appear in the final text.

- The audit runs inside the async generation job; its report is persisted alongside the document (nullable `ats_report` JSONB on `generated_cvs` / `generated_cover_letters`, sharing the document's retention TTL) and exposed via REST and MCP as a **checks panel** — named pass/fail checks and missing keywords, deliberately **no aggregate 0–100 score** (same no-synthetic-numbers philosophy as ADR-035).
- An audit-engine error never fails generation: the report stays `NULL` and the error is logged.
- **CI guarantee:** a parametrised suite renders every template with DE and EN fixture documents and asserts every audit check passes. Where a template's visual layout conflicts with extraction order, the layout changes.

**Why:** An ATS parses the CV before any human reads it. Keyword *content* was already covered (JD keyword extraction → gap detection → guarded incorporation in tailoring, ADR-021); this decision closes the *format* half with verifiable, sovereignty-friendly evidence — documents never leave the system to be checked.

---

### ADR-040 — Truthful Output (Attestation & Transparency Tier)

**Decision:** LLM-derived content that can leave the system as a truth claim (profile facts from extraction/merge/interview; delivered CV/cover-letter bullets) must carry **two** controls, not one: the existing ADR-021 prevention reviewer *and* a user-facing **transparency/attestation surface**. A single reusable "what changed & why" component is shown at four touchpoints — a skippable confirm of what was read from a CV, the auto-resolved assumptions after a merge, an answer→field change summary after the interview, and a generated-CV-vs-Master-Profile diff with an attestation nudge before download.

- The surfaces are sourced **only** from the durable decision trail (the `EnrichmentRecord` / `FieldChange` history mandated by ADR-013) and the current persisted artifacts — **never** the source upload, which is hard-deleted after 7 days (ADR-005). Transparency therefore survives for the full life of the profile.
- Attestation is an active **nudge, not a hard gate**: the user is prompted to confirm but the flow is never blocked.

**Why:** A self-checking LLM reviewer reduces how often a fabrication occurs but cannot, by itself, let the *user* catch one — and false content in a CV reaches a real recruiter under the user's name. A prevention reviewer plus an animated user review are complementary: one lowers how often the failure happens, the other lets a human stop it before it ships.

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

**Decision:** Jobs, projects, and volunteering share one capability contract — a base `ExperienceBase` (role, dates, location, responsibilities, achievements, technologies) that `WorkEntry`, `ProjectEntry`, and `VolunteerActivity` each extend with kind-specific fields (`WorkEntry`: company, team size, budget; `ProjectEntry`: name, description, url, `associated_experience`; `VolunteerActivity`: organization, cause). Each kind keeps its own section-mapped list on the profile (mirroring CV sections), all stored additively in `profile_json` (ADR-002 JSONB — no new table, no migration; legacy profiles load via the `model_validator`). The profile-side derived computations — experience-years, skill accrual, and profile stats — iterate **all** experiences via a single `all_experiences` accessor (so volunteering/projects count toward domain experience). A project may link to *any* parent experience (a job or a volunteer role, by name/label) or stand alone. (Tailored-output rendering of projects in the PDF templates is a deferred follow-up; the field is captured, stored, and available to the tailoring input now.)

**Why:** The capability set (time span, applied skills, achievements, can-contain-projects) is orthogonal to the *kind* of engagement. The previous separate-entity model (a) wrongly denied volunteering achievements and skills, and (b) counted only `work_experience` toward experience-years and skill accrual — so e.g. managing software for an NGO never counted toward domain experience, a correctness defect for a tailoring product. CV extraction and the ADR-021 reviewer still capture projects **as projects** (not folded into work experience), with kind-appropriate anti-fabrication rules. A profile headline and an education `notes` field are deferred extensions; **references are deliberately not stored** (third-party personal data — data minimisation), and the reviewer treats their absence as correct, not as data loss.

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

**Decision:** An `nginx` service is the standard entry point for Community Edition (config at `nginx/self-hosted.conf`). All external traffic enters on port 80; nginx routes `/api/`, `/static/`, and `/health` to the backend container and everything else to the frontend. `NEXT_PUBLIC_API_URL` is set to `""` (empty string), so all frontend `fetch()` calls use relative paths (e.g. `/api/profile/exists`). The browser resolves these against the current origin — no configuration needed regardless of hostname, IP, or DHCP lease.

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
| `generated_cvs` | PDF + snapshot + overrides | 90d (human) / 24h (agent) |
| `uploads` | Raw uploaded files | 7-day hard delete |

---

## 5. Community vs. Cloud Boundary

This repository is the Community Edition. The table below documents what is and is not in scope.

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
