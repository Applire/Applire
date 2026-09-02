# Applire Test Infrastructure

## V-Model Tier Structure

Applire uses a V-model-aligned test structure with five tiers:

| Tier | Directory | Tool | LLM | Run condition |
|---|---|---|---|---|
| **Unit** | `tests/unit/` | pytest | No (no Docker) | Every commit — fast, no infrastructure |
| **Integration** | `tests/integration/` | pytest | Mock by default; real with `INTEGRATION_LLM=1` | CI (after IQ/OQ); local with Docker stack |
| **IQ** | `tests/iq/` | Playwright | No (mock via Docker stack) | CI (first E2E gate); local with Docker stack |
| **OQ** | `tests/oq/` | Playwright | No (API routes mocked via `page.route()`) | CI (after IQ); local with Docker stack |
| **PQ** | `tests/pq/` | Playwright | No (mock via Docker stack in CI) | CI (after OQ+Integration); real-LLM via separate workflow |

**LLM boundary rule:** IQ and OQ tests never call an LLM. OQ backend routes are intercepted with `page.route()` and return deterministic fixtures. PQ tests in CI use `LLM_PROVIDER=mock`. **There is no real-LLM PQ lane.** The `pq.yml` workflow is named after this tier but runs `INTEGRATION_LLM=1 pytest tests/integration/` — the backend integration tier — so no Playwright spec has ever met a real model. See *Real-LLM lanes* below.

**CI pipeline order (as `test.yml` actually runs it):** the `backend-unit-tests`, `frontend-unit-tests`, `frontend-lint`, `frontend-build-check` and `module-system-check` jobs run in parallel; the `integration-and-e2e-tests` job brings up the Docker stack, migrates, then runs **Integration → MCP stdio → IQ → OQ (desktop) → OQ (mobile) → PQ** in that order. Note that **Integration runs before IQ**, not alongside OQ — an earlier version of this line said otherwise.

---

## Vocabulary — three axes, not one ladder

Three naming systems are in use here and they describe **different axes**. Reading them as one ladder is what makes the test suite feel inconsistent when it is not. This is the same correction **ADR-073 clause 7** already made once, for arc42 §8.1's three *gating* tiers versus this file's five *kind* tiers.

| Axis | Question it answers | Values |
|---|---|---|
| **Kind** | *What does this prove?* | Unit · Integration · IQ · OQ · PQ |
| **Environment** | *Against what does it run?* | CI throwaway stack · local dev stack · the edge instance (`:8090`) |
| **Driver** | *Who makes it happen?* | CI · a subagent · a human (the founder) |

A test is one point in that space, so the same **kind** can legitimately run in more than one place. That is the whole trick: a "second IQ against edge" needs no new tier — it is `tests/iq/startup.spec.ts` (kind) pointed at edge (environment).

### The kinds, written out

The IQ/OQ/PQ names are borrowed from pharmaceutical validation, where they are always spelled out. Here they are, once, so nobody has to guess:

| Abbrev. | Stands for | What it means **in this repo** |
|---|---|---|
| **IQ** | *Installation Qualification* | Is the system installed and reachable? The Docker stack is already up when IQ runs — IQ **verifies** the installation, it does not perform it. Backend `/health` is 200, the frontend root loads, the upload input is attached. |
| **OQ** | *Operational Qualification* | Does each **page** behave correctly against a deterministic backend? Playwright with `page.route()` intercepting the API. **Not** module- or interface-level testing — that is what `tests/unit/` and `tests/integration/` are for. This narrower meaning is deliberate and is the one in force. |
| **PQ** | *Performance Qualification* | Does a **persona's journey** work end to end through the real UI and a real backend? One directory per persona. |

### E2E and UAT

Neither is a tier; both name something real.

- **E2E** is an **umbrella** for IQ + OQ + PQ — everything Playwright-driven through a browser. It is the word used by `playwright.config.ts`, by `applire-core/CLAUDE.md`, and by the CI job name *"Integration & E2E Tests"*. It is not a fourth kind and it is not a synonym for PQ.
- **UAT** is **not a CI concept at all.** It is a human exercising a built stand, and it comes in two forms:
  - **Automated UAT** — a subagent plays user stories through with synthetic test data against a **real LLM provider**, off-path and adversarially. This is step 3 of the feature pipeline. It is *driven* work with a written record (the PR body, or a collector line), not a suite that a runner executes.
  - **Real UAT** — the founder exercises the edge instance after everything above is green. Residue lands in `Documents/Runs/<Flavour>/edge-uat/`.

**Why the distinction is load-bearing:** the automated UAT is currently the *only* thing that puts a persona journey in front of a real model through the UI. See the note under *Real-LLM lanes* below.

### Real-LLM lanes — what actually exists

| Lane | Trigger | What it runs |
|---|---|---|
| `pq.yml` — *"Real-LLM Integration Tests (Manual)"* | `workflow_dispatch` | `INTEGRATION_LLM=1 pytest tests/integration/` — the **backend integration** tier, **not** `tests/pq/`. Despite the filename, no Playwright spec runs in this workflow. |
| Automated UAT | a subagent, in a work session | whatever the story needs, against a real provider on the dev stack |

So the real-LLM coverage of a persona journey is exactly one API-level test — `test_happy_path.py`'s *"CV upload → JD analysis → gap detection"*, which is Marcus's shape without a browser. **No browser journey has ever run against a real model.** Closing that is tracked on the infra collector, not here.

---

## Folder Structure

```
tests/
├── unit/                        # Unit tests — no Docker required
│   ├── conftest.py              # Overrides Docker fixture; adds backend/ to sys.path
│   ├── test_gap_analysis.py
│   ├── test_flow_orchestrator.py
│   └── ...                      # One file per module
├── integration/                 # Full-stack integration tests (pytest)
│   └── test_happy_path.py       # 16-step happy path; mock LLM by default
├── iq/                          # Installation Qualification (Playwright)
│   └── startup.spec.ts          # Health endpoint + UI reachable
├── oq/                          # Operational Qualification (Playwright, mocked)
│   ├── admin-appearance.spec.ts
│   ├── cv-color.spec.ts
│   ├── cv-preview.spec.ts
│   ├── cv-section-editor.spec.ts
│   ├── gaps-page.spec.ts
│   ├── jd-url-error.spec.ts
│   ├── match-page.spec.ts
│   ├── photo-management.spec.ts
│   ├── profile-enrichment.spec.ts
│   ├── upload-flow.spec.ts
│   └── mobile/                  # 390x844 viewport lane (US227, ADR-050 §6)
│       ├── dashboard-shell.spec.ts
│       ├── capture.spec.ts
│       ├── gaps-triage.spec.ts
│       └── cv-review.spec.ts
├── pq/                          # Performance Qualification — persona journeys
│   ├── marcus/                  # New professional, first run
│   │   ├── marcus-new-user-journey.spec.ts
│   │   └── marcus-complete-journey.spec.ts
│   ├── emma/                    # Returning power user (re-assigned 2026-09-02)
│   │   ├── emma-dashboard-journey.spec.ts
│   │   └── emma-profile-upload.spec.ts
│   └── felix/                   # Finetuner — refining a generated document
│       ├── felix-cover-letter.spec.ts
│       ├── felix-cv-design.spec.ts
│       └── felix-cv-templates.spec.ts
├── fixtures/
│   ├── profiles/sample_cv.pdf
│   └── JDs/sample_jd.txt
└── ...                          # Legacy per-iteration API tests
```

---

## Running Tests

### Unit tests (no Docker)

```bash
pytest tests/unit/ -v \
  --cov=applire --cov-config=backend/.coveragerc \
  --cov-report=html:backend/htmlcov \
  --cov-fail-under=75
```

Coverage threshold: **≥ 75%** (enforced in CI).

### Frontend unit tests (Vitest)

```bash
cd frontend && npm test
```

### Integration tests (requires Docker stack)

```bash
docker compose up -d
# Mock LLM (default — used in CI):
pytest tests/integration/ -v
# Real LLM (requires .env with a configured LLM provider):
INTEGRATION_LLM=1 pytest tests/integration/ -v
```

### IQ + OQ Playwright tests (requires Docker stack)

```bash
docker compose up -d

# Run IQ + OQ (pq/ excluded automatically via testMatch):
npx playwright test

# Run a specific spec:
npx playwright test tests/oq/gaps-page.spec.ts --headed
```

The default `playwright.config.ts` uses `testMatch: ['**/iq/**/*.spec.ts', '**/oq/**/*.spec.ts']` so PQ specs are never picked up by accident.

### Mobile-viewport OQ lane (390x844, US227)

A second Playwright project, `mobile-chromium`, runs the specs under `tests/oq/mobile/` at a
390x844 viewport with `hasTouch: true` and a mobile Chrome UA (ADR-050 §6 — the responsive
retrofit gets its own CI-caught regression lane instead of relying on the next manual UAT).
It is scoped via a per-project `testMatch`, and the desktop `chromium` project carries a
matching `testIgnore: ['**/oq/mobile/**']` so the two projects never run the same spec twice
under `workers: 1` (once desktop, once mobile — never desktop-twice or mobile-in-desktop).

```bash
docker compose up -d   # mock-provider stack, same as any other OQ run

# Desktop OQ lane (unchanged)
npx playwright test --project=chromium tests/oq/

# Mobile OQ lane (390x844)
npx playwright test --project=mobile-chromium

# Both lanes, one invocation (the default project list runs every project
# matching its own testMatch, so nothing doubles up)
npx playwright test
```

Each mobile spec asserts `document.body.scrollWidth <= window.innerWidth` on shell pages (no
horizontal body overflow — an a11y snapshot can't catch this, only real pixels can) and
captures a full-page screenshot via `testInfo.attach()` (also written to `test-results/`) as
an evidence artifact. Covers: dashboard/shell (hamburger → `MobileNavDrawer`, single-column
`applications-grid`), gap triage (`gaps-decision-bar` fixed at the viewport bottom, US225),
and CV review (`MobileCommandBar`'s three actions — ATS Checks, Fine-tune, Download PDF —
US226). Stays hermetic like every other OQ spec: `page.route()` mocks only, `LLM_PROVIDER=mock`,
no real backend/LLM calls.

### PQ tests (requires Docker stack)

```bash
docker compose up -d
# Mock LLM (same as CI):
npx playwright test --config=playwright.config.pq.ts
# Real LLM (requires .env with a configured LLM provider):
INTEGRATION_LLM=1 npx playwright test --config=playwright.config.pq.ts
```

The `playwright.config.pq.ts` uses `testDir: './tests/pq'` and runs both `marcus/` and `felix/` persona suites.

---

## Naming Convention

Test files are named after the module or feature they test, not the sprint they were written in.

| Tier | Pattern | Example |
|---|---|---|
| Backend unit | `test_<module>.py` | `test_gap_analysis.py` |
| Backend integration | `test_<journey>.py` | `test_happy_path.py` |
| Playwright IQ/OQ | `<page-or-feature>.spec.ts` | `gaps-page.spec.ts` |
| Playwright PQ | `<persona>-<journey>.spec.ts`, inside `pq/<persona>/` | `marcus-new-user-journey.spec.ts` |

The persona prefix is not decoration — it is the only thing that makes a mis-filed journey visible. `cover-letter.spec.ts` sat in `pq/felix/` without one and was therefore missing from this document's own spec count (4 listed, 5 present) until 2026-09-02.

---

## Coverage Gate

- Backend unit: **≥ 75%** (`--cov-fail-under=75`)
- No coverage gate on Playwright tests — functional coverage is tracked by the IQ/OQ/PQ tier structure above.

---

## Personas in PQ Tests

**The directory is a claim about whose journey a spec walks**, and it was wrong until 2026-09-02: `pq/felix/` held two specs whose own docblocks described *"the returning-user dashboard experience"* and *"a returning user (with an existing profile)"*. That is **Emma**. Felix is the **finetuner** — section-level editing against a live preview — and Emma is the **returning power user** (retention, one-click parallel tailoring). Both were moved to `pq/emma/`, which is why Emma went from *Planned* to *Active* without a line of new test code being written. Take the persona from `Personas/Persona-and-Channel-Model.md`, never from a filename.

| Persona | Journey | Directory | Status |
|---|---|---|---|
| Marcus | New user: upload → gaps → interview → CV | `pq/marcus/` | Active (2 specs) |
| Emma | Returning power user: dashboard, My Documents, Quick Tailor, profile refresh | `pq/emma/` | Active (2 specs — re-assigned from `felix/` 2026-09-02) |
| Felix | Finetuner: CV design, template choice, cover-letter editing | `pq/felix/` | Active (3 specs) |
| Priya | International relocator: cultural adaptation | To be added | Planned |

**Felix's own core loop still has no PQ.** What `pq/felix/` covers is the *periphery* of finetuning — colour, template, cover-letter body. The loop `Personas/Finetuner.md` actually describes (gap card → section editor → KaileChat → save scope → live re-render) is covered only at **OQ** (`oq/cv-section-editor.spec.ts`), i.e. against a mocked backend. Relevant to E058, which rebuilds exactly that surface.

---

## Troubleshooting

**Unit tests fail with import errors:**
```bash
python --version   # must be 3.12+
pip install -r backend/requirements.txt
pytest tests/unit/ -vv --tb=long
```

**Playwright IQ/OQ tests fail:**
```bash
node --version     # must be 20+
npx playwright install --with-deps chromium
docker compose up -d          # ensure stack is running
npx playwright test --headed  # see browser
npx playwright test --debug   # step through
```

**PQ tests fail or skip:**
Ensure the Docker stack is fully running and `LLM_PROVIDER=mock` is set in the environment (`.env.ci` or `.env`).
```bash
curl http://localhost:8001/health
curl http://localhost:3000
```

---

*Last updated: 2026-05-02*
