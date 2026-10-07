# 🚀 CI/CD Pipeline Guide for Engineers

## Overview

The Applire CI/CD pipeline automatically runs tests on every push and pull request. **Test failures are now reported directly in your PR** — no need to visit GitHub Actions!

---

## What Happens When You Push

```
Your Code Push
    ↓
GitHub Actions Triggered
    ↓
Test Suites Run in Parallel:
  • Backend Unit Tests (2 min)
  • Integration & E2E Tests — the fenced NoAuth test harness (10 min)
  • Postgres-only Tests — races, constraints, migrations
  • Real-auth Lane — login on, production topology
  • Frontend unit tests, lint and production build
    ↓
Results Posted to Your PR
```

---

## Where to Find Test Results

### 1. **PR Annotations** (Most Important!)
When tests fail, you'll see **red annotations** directly on the code:

```
❌ Line 42 in profile.py
   AssertionError: expected 200, got 422
```

**How to see them:**
- Go to your PR → **Checks** tab → Click on a failed test
- Or look for red squiggly lines in the **Files changed** tab

### 2. **PR Comments** (Summary)
A bot automatically posts a comment with:
- ✅ Which tests passed
- ❌ Which tests failed
- 📋 Next steps to fix

Example:
```
❌ Test failures detected

Failed components:
- Backend Unit Tests: failure
- Backend Integration Tests: success

What to do:
1. Check the Annotations tab above for specific test failures
2. Review the Checks section for detailed error messages
3. Download test artifacts for full reports
4. Fix the issues and push again
```

### 3. **Job Summary** (Overview)
Click the **Summary** tab to see:
- Test status table
- Coverage reports
- Artifact links

### 4. **GitHub Actions** (Full Details)
If you need the complete logs:
- Go to **Actions** tab
- Find your workflow run
- Click on a failed job for full logs

---

## Common Scenarios

### Scenario 1: Unit Test Fails

**You'll see:**
1. Red annotation on the failing test file
2. PR comment: "Backend Unit Tests: failure"
3. Link to download coverage report

**What to do:**
```bash
# Run locally to debug
pytest tests/unit/ -v --tb=short

# Fix the issue
# Push again
```

### Scenario 2: Integration Test Fails

**You'll see:**
1. Red annotation in the test file
2. PR comment: "Backend Integration Tests: failure"
3. Docker logs available in artifacts

**What to do:**
```bash
# Run locally with Docker
docker-compose up -d
pytest tests/integration/ -v

# Fix the issue
# Push again
```

### Scenario 3: E2E Test Fails

**You'll see:**
1. Red annotation in the Playwright test
2. PR comment: "E2E Tests: failure"
3. Playwright report and videos in artifacts

**What to do:**
```bash
# Run locally (requires running stack)
npx playwright test --headed  # See the browser

# Debug specific test
npx playwright test --debug

# Fix the issue
# Push again
```

### Scenario 4: All Tests Pass ✅

**You'll see:**
1. Green checkmark on all checks
2. PR comment: "✅ All tests passed! This PR is ready to merge."

---

## Running Tests Locally

### Backend Unit Tests
```bash
pytest tests/unit/ -v --cov=applire --cov-report=html
# View coverage: open backend/htmlcov/index.html
```

### Backend Integration Tests
```bash
docker-compose up -d
pytest tests/integration/ -v
docker-compose down -v
```

> ⚠️ `down -v` deletes the named volumes. That is fine for the throwaway CI and test stacks this guide is about, and it is **never** right on an install that holds real data — see [SELF-HOSTING.md](SELF-HOSTING.md). If you are reading this guide while operating your own instance, use `docker compose down` without `-v`.

### E2E Tests
```bash
# Requires running stack (docker-compose up -d)
npx playwright test

# View results
npx playwright show-report
```

---

## The two auth lanes (ADR-091)

Login is always on in the product, so CI has two postures:

| Lane | Stack | What it proves |
|---|---|---|
| **Integration & E2E** (harness) | `cp .env.ci .env`, `docker-compose.ci.yml` — `AUTH_HARNESS=true`, database `applire_ci`; every request is the stub administrator | Behaviour: IQ, OQ, PQ, integration, MCP stdio. `.env.ci` pins `POSTGRES_DB=applire_ci` and `docker-compose.ci.yml` overrides `env_file`, so a developer's own `.env` is never concatenated into the CI stack |
| **Real-auth lane** (job `real-auth-lane`, **blocking**) | `docker-compose.ci-auth.yml` + `.env.ci-auth` — production topology, login on, nginx on `:8091` | Access control: setup from the code in the backend log through nginx, a port-less `Origin` refused (403), `/health` returns three fields, `/api/ops/health` answers 401 then 200, logout/login, no signature in the access log, the harness refusing a non-test database; then `tests/test_auth_e2e.py` once present |
| **Postgres-only** (job `postgres-only-tests`) | one throwaway `*_test` PostgreSQL database per file | Races, the audit-log trigger, ownership constraints, migration 0076, the erasure race; a skipped test fails the job |

The harness refuses to start on a database that is not a throwaway test database (SQLite in memory, or a name ending in `_ci`/`_test` with no profile at boot) — never copy `AUTH_HARNESS=true` into a real install's `.env`.

---

## Understanding Test Reports

### JUnit XML Reports
Each test suite generates a JUnit XML report:
- **Backend Unit**: `backend/test-results.xml`
- **Backend Integration**: `test-results.xml`
- **E2E**: `test-results/`

These are automatically parsed by `dorny/test-reporter` to create PR annotations.

### Coverage Reports
Coverage reports are uploaded as artifacts:
- **Backend Coverage**: `backend-coverage-report/` (HTML)
- **Playwright Report**: `playwright-report/` (HTML)

Download them from the **Artifacts** section in GitHub Actions.

---

## Troubleshooting

### "Tests pass locally but fail in CI"

**Common causes:**
1. **Environment differences** - CI uses Ubuntu, you might use macOS/Windows
2. **Missing dependencies** - Run `pip install -r requirements.txt` or `npm ci`
3. **Port conflicts** - CI uses isolated Docker network, your local might have conflicts
4. **Timing issues** - CI might be slower, tests might timeout

**How to debug:**
```bash
# Run tests exactly like CI does
docker-compose up -d
docker-compose exec -T backend pytest tests/unit/ -v
docker-compose logs  # See service logs
```

### "I don't see annotations in my PR"

**Possible reasons:**
1. Tests haven't finished yet (wait 5-10 minutes)
2. Tests passed (no annotations for passing tests)
3. Refresh your browser

### "Artifacts are missing"

**Why:**
- Artifacts are kept for 30 days
- Some artifacts only appear if tests fail

**How to get them:**
1. Go to **Actions** tab
2. Find your workflow run
3. Scroll down to **Artifacts** section
4. Download what you need

---

## Best Practices

### 1. Read the Annotations First
Don't go to GitHub Actions immediately. Check the **Checks** tab in your PR for annotations.

### 2. Run Tests Locally Before Pushing
```bash
# Quick check
pytest tests/unit/ -q

# Full check
pytest tests/unit/ -v
npx playwright test
```

### 3. Check Coverage
Make sure your changes don't drop coverage below 75%:
```bash
pytest tests/unit/ --cov=applire --cov-fail-under=75
```

### 4. Read Error Messages Carefully
The annotations include:
- File name and line number
- Exact error message
- Stack trace (if available)

### 5. Push Incrementally
Don't make huge changes. Push frequently so you can catch issues early.

---

## Pipeline Status

### Current Configuration

| Component | Timeout | Coverage gate | Status |
|-----------|---------|---------------|--------|
| Backend Unit | 5 min | ≥75% | ✅ |
| Backend Integration | 20 min | — | ✅ |
| E2E (IQ + OQ + PQ) | 10 min | — | ✅ |
| Postgres-only Tests | 20 min | — | ✅ |
| Real-auth Lane | 30 min | — | ✅ (blocking) |

### Branches Monitored
- `main` — stable, always deployable
- Feature/sprint branches — CI runs on PRs to `main`

### Triggers
- Push to monitored branches
- Pull requests to monitored branches

---

## Getting Help

### For Test Failures
1. Check the PR annotations
2. Read the PR comment
3. Run tests locally with `--tb=long` for more details
4. Check the artifacts for full reports

### For Pipeline Issues
1. Check GitHub Actions logs
2. Review `.github/workflows/test.yml`
3. Check Docker logs: `docker-compose logs`

### For Questions
- Open a GitHub issue tagged `question`
- Check the `docs/TESTING.md` guide
- Review `docs/ARCHITECTURE.md` for architectural context

---

## Quick Reference

```bash
# Run all tests locally
pytest tests/unit/ -v
docker-compose up -d && pytest tests/integration/ -v
npx playwright test

# Check coverage
pytest tests/unit/ --cov=applire --cov-report=html

# Debug a specific test
pytest tests/unit/test_iter11_profile.py::TestSchemaModels::test_profile_schema -vv

# Run E2E in headed mode (see the browser)
npx playwright test --headed

# View Playwright report
npx playwright show-report
```

---

*Last updated: 2026-05-13*
