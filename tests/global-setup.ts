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
 * bare `next dev` — nothing reaches a backend there.
 */

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8001";

async function globalSetup(): Promise<void> {
  if (process.env.E2E_STUBBED_ONLY === "1") return;
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
