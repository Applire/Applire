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

**LLM boundary rule:** IQ and OQ tests never call an LLM. OQ backend routes are intercepted with `page.route()` and return deterministic fixtures. PQ tests in CI use `LLM_PROVIDER=mock`. Real-LLM PQ runs are triggered via the separate `pq.yml` workflow with `INTEGRATION_LLM=1`.

**CI pipeline order:** Unit → IQ → OQ + Integration → PQ (all within the `integration-and-e2e-tests` job in `test.yml`)

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
│   └── upload-flow.spec.ts
├── pq/                          # Performance Qualification — persona journeys
│   ├── marcus/
│   │   ├── marcus-new-user-journey.spec.ts
│   │   └── markus-complete-journey.spec.ts
│   └── felix/
│       ├── cover-letter.spec.ts
│       ├── felix-cv-design.spec.ts
│       ├── felix-cv-templates.spec.ts
│       └── felix-dashboard-sprint29.spec.ts
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
| Playwright PQ | `<persona>-<journey>.spec.ts` | `marcus-new-user-journey.spec.ts` |

---

## Coverage Gate

- Backend unit: **≥ 75%** (`--cov-fail-under=75`)
- No coverage gate on Playwright tests — functional coverage is tracked by the IQ/OQ/PQ tier structure above.

---

## Personas in PQ Tests

| Persona | Journey | Directory | Status |
|---|---|---|---|
| Marcus | New user: upload → gaps → interview → CV | `pq/marcus/` | Active (2 specs) |
| Felix | Power user: dashboard, CV design, templates, cover letter | `pq/felix/` | Active (4 specs) |
| Emma | Returning user: dashboard → one-click tailoring | To be added | Planned |
| Priya | International relocator: cultural adaptation | To be added | Planned |

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
