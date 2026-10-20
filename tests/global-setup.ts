import { request } from "@playwright/test";

/**
 * IQ/OQ/PQ global setup — the harness lanes (docker-compose.ci.yml, .env.ci).
 *
 * Strawberry (ADR-091, ruling RD-1): `/health` is liveness only now
 * ({status, edition, version}); `llm_provider` moved to the gated
 * `/api/ops/health`. These lanes run on the fenced NoAuth test harness, where
 * every request is the admin — so the gated report answers without a login.
 * Checks, in order: the backend is up; the harness is serving (otherwise the
 * shell would redirect every page to /login); the provider is the mock.
 *
 * `E2E_STUBBED_ONLY=1` skips the backend checks for a local run of specs that
 * stub every API call (`page.route` + tests/support/auth-fixture.ts) against a
 * bare `next dev` — nothing reaches a backend there. That promise is PROVED, not
 * assumed (#789 L15): `frontend/next.config.ts` rewrites every un-mocked `/api/*`
 * to `BACKEND_URL ?? http://localhost:8001`, so a locally served spec with an
 * incomplete `page.route` silently talks to whatever backend runs on the host
 * (a spec once passed/failed on another stack's `ui_language`). `assertNoBackendBehindFrontend`
 * therefore asks the frontend under test for `/api/auth/state` and refuses to
 * start when a backend answers.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";
// Same default as playwright.config.ts `use.baseURL`.
const FRONTEND_URL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

/**
 * Stubbed-only runs must serve the frontend with an unroutable backend
 * (`BACKEND_URL=http://127.0.0.1:9 next dev -p <port>`): Next's proxy then fails
 * (500/502 or no answer) and every un-mocked API call fails loudly instead of
 * reaching a real stack. A reply that is a backend's JSON — 2xx, or a FastAPI
 * error body (`{"detail": ...}`) — means a backend is behind the rewrite.
 */
async function assertNoBackendBehindFrontend(): Promise<void> {
  const ctx = await request.newContext({ baseURL: FRONTEND_URL });
  try {
    let res;
    try {
      res = await ctx.get("/api/auth/state", { timeout: 15_000, maxRedirects: 0 });
    } catch {
      return; // the frontend itself is not reachable (or the proxy dropped the connection): no backend answers
    }
    const type = res.headers()["content-type"] ?? "";
    let body: unknown = null;
    if (type.includes("json")) {
      try {
        body = await res.json();
      } catch {
        body = null;
      }
    }
    const fastApiError =
      body !== null && typeof body === "object" && "detail" in (body as Record<string, unknown>);
    const isBackendJson = type.includes("json") && (res.ok() || (fastApiError && res.status() >= 400 && res.status() < 500));
    if (isBackendJson) {
      throw new Error(
        `E2E_STUBBED_ONLY=1 promises that nothing reaches a backend, but ${FRONTEND_URL}/api/auth/state ` +
          `answered HTTP ${res.status()} with a backend's JSON — the frontend under test proxies /api/* to a live backend, ` +
          `so an incomplete page.route would silently run against it.\n` +
          `Serve the frontend with an unroutable backend and run again:\n` +
          `  cd frontend && BACKEND_URL=http://127.0.0.1:9 NEXT_PUBLIC_API_URL= npx next dev -p <port>\n` +
          `  E2E_BASE_URL=http://localhost:<port> E2E_STUBBED_ONLY=1 npx playwright test ...`
      );
    }
  } finally {
    await ctx.dispose();
  }
}

async function globalSetup(): Promise<void> {
  if (process.env.E2E_STUBBED_ONLY === "1") {
    await assertNoBackendBehindFrontend();
    return;
  }
  const ctx = await request.newContext({ baseURL: BACKEND_URL });
  try {
    const res = await ctx.get("/health");
    if (!res.ok()) {
      throw new Error(
        `Backend health check returned HTTP ${res.status()}. Is the Docker stack running?`
      );
    }

    const state = await ctx.get("/api/auth/state");
    const harness = state.ok() ? (await state.json()).harness === true : false;
    if (!harness) {
      throw new Error(
        "E2E (IQ/OQ/PQ) lanes run on the NoAuth test harness, but the backend reports harness=false " +
          "(login is on — every page would redirect to /login).\n" +
          "Start the CI stack before running tests:\n" +
          "  cp .env.ci .env && docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build"
      );
    }

    const ops = await ctx.get("/api/ops/health");
    if (!ops.ok() && ops.status() !== 503) {
      throw new Error(`/api/ops/health returned HTTP ${ops.status()} on the harness — expected the admin report.`);
    }
    const body = await ops.json();
    if (body.llm_provider !== "mock") {
      throw new Error(
        `E2E tests require LLM_PROVIDER=mock but the backend reports "${body.llm_provider}".\n` +
          `Start the CI stack before running tests:\n` +
          `  docker compose -f docker-compose.yml -f docker-compose.ci.yml up -d --build`
      );
    }
  } finally {
    await ctx.dispose();
  }
}

export default globalSetup;
