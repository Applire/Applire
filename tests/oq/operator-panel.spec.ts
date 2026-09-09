// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * OperatorPanel on the admin page (E060 / US312, ADR-086 clause 11).
 *
 * Founder ruling O1-4 (2026-09-09): the operator panel lives on the existing
 * admin surface, not on the dashboard — the dashboard is the candidate's
 * pipeline, and in the Strawberry admin release access to /admin is tied to a
 * user right.
 *
 * Mocked API — no backend, no provider. The panel is the operator's *pull*
 * surface; the WARNING log line is the push half and is covered by the backend
 * tests.
 */

import { test, expect } from "@playwright/test";

const HEALTHY = {
  status: "ok",
  edition: "community",
  version: "0.42.0",
  llm_provider: "openrouter",
  checked_at: "2026-09-08T18:00:00+00:00",
  components: {
    database: { status: "ok", message: "", detail: { latency_ms: 3 } },
    migrations: { status: "ok", message: "", detail: { code_head: "0063", db_head: "0063" } },
    retention: {
      status: "ok",
      message: "",
      detail: {
        last_run_at: "2026-09-08T02:00:00+00:00",
        age_seconds: 57600,
        expected_interval_seconds: 86400,
        last_run_ok: true,
        deleted: { uploads_deleted: 0 },
      },
    },
    disk: {
      status: "ok",
      message: "",
      detail: {
        free_bytes: 161382166528,
        total_bytes: 271654584320,
        free_percent: 59.4,
        warn_below_percent: 10,
      },
    },
    backup: {
      status: "ok",
      message: "",
      detail: { last_backup_at: "2026-09-06T02:00:00+00:00", age_days: 2, warn_after_days: 30 },
    },
    provider: {
      status: "ok",
      message: "",
      detail: {
        provider: "openrouter",
        model: "openai/gpt-5.6-luna",
        reachability: "ok",
        credit: "ok",
        credit_remaining: 25.4,
        checked_at: "2026-09-08T18:00:00+00:00",
      },
    },
    errors: {
      status: "ok",
      message: "",
      detail: {
        window_minutes: 60,
        counts: { total: 0 },
        scope: "this backend process since start",
      },
    },
  },
  usage: {
    today: {
      prompt_tokens: 1200,
      completion_tokens: 800,
      total_tokens: 2000,
      calls: 9,
      estimated_calls: 0,
      fully_measured: true,
    },
    window_days: 7,
    window: {
      prompt_tokens: 50000,
      completion_tokens: 21000,
      total_tokens: 71000,
      calls: 210,
      estimated_calls: 12,
      fully_measured: false,
    },
    by_document: [],
    by_application: [],
  },
};

function degraded() {
  const body = JSON.parse(JSON.stringify(HEALTHY));
  body.status = "degraded";
  body.components.retention.status = "degraded";
  body.components.retention.message = "no run for 51 h";
  body.components.retention.detail.age_seconds = 183600;
  body.components.backup.status = "degraded";
  body.components.backup.message = "no backup has ever been recorded";
  body.components.backup.detail.last_backup_at = null;
  body.components.backup.detail.age_days = null;
  body.components.provider.detail.credit = "n/a";
  body.components.provider.detail.credit_reason = "this provider has no balance to read";
  return body;
}

async function mount(page: import("@playwright/test").Page, body: unknown) {
  await page.route("**/api/ops/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) })
  );
  await page.route("**/api/admin/color-schemes**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
  );
  await page.goto("/admin/appearance");
}

test.describe("Operator panel", () => {
  test("a healthy instance is one quiet line, collapsed", async ({ page }) => {
    await mount(page, HEALTHY);
    const panel = page.getByTestId("operator-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("Everything is fine");
    // Collapsed: no component rows until the operator asks for them.
    await expect(page.getByTestId("operator-panel-row-disk")).toHaveCount(0);
    await expect(page.getByTestId("operator-panel-toggle")).toHaveAttribute(
      "aria-expanded",
      "false"
    );
  });

  test("the details open on request and carry every component", async ({ page }) => {
    await mount(page, HEALTHY);
    await page.getByTestId("operator-panel-toggle").click();
    for (const name of [
      "database",
      "migrations",
      "retention",
      "disk",
      "backup",
      "provider",
      "errors",
    ]) {
      await expect(page.getByTestId(`operator-panel-row-${name}`)).toBeVisible();
    }
    await expect(page.getByTestId("operator-panel-row-disk")).toContainText("59.4 % free");
  });

  test("a degraded instance opens itself and names the count", async ({ page }) => {
    await mount(page, degraded());
    const panel = page.getByTestId("operator-panel");
    await expect(panel).toContainText("2 things to check");
    await expect(page.getByTestId("operator-panel-toggle")).toHaveAttribute(
      "aria-expanded",
      "true"
    );
    await expect(page.getByTestId("operator-panel-row-retention")).toContainText("51 h ago");
    await expect(page.getByTestId("operator-panel-row-backup")).toContainText("No backup yet");
  });

  test("a provider without a balance says so instead of hiding the field", async ({ page }) => {
    // Founder ruling O1-6: `n/a` is a DISPLAYED state, distinct from `unknown`.
    // Ollama and any OpenAI-compatible endpoint have no balance at all, and a
    // field that disappears for four of six providers reads as a bug.
    await mount(page, degraded());
    await expect(page.getByTestId("operator-panel-row-provider")).toContainText(
      "This provider reports no balance"
    );
  });

  test("the provider row names the configured model id", async ({ page }) => {
    // Founder ruling O1-2 (2026-09-09): the model id is published so the
    // operator can match their instance against docs/llm-models.md's list.
    await mount(page, HEALTHY);
    await page.getByTestId("operator-panel-toggle").click();
    await expect(page.getByTestId("operator-panel-row-provider")).toContainText(
      "openai/gpt-5.6-luna"
    );
  });

  test("an aggregate containing estimates says so", async ({ page }) => {
    await mount(page, HEALTHY);
    await page.getByTestId("operator-panel-toggle").click();
    await expect(page.getByTestId("operator-panel")).toContainText("Includes estimates");
  });

  test("an unreachable endpoint degrades to one honest line", async ({ page }) => {
    await page.route("**/api/ops/health", (route) => route.abort());
    await page.route("**/api/admin/color-schemes**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
    await page.goto("/admin/appearance");
    await expect(page.getByTestId("operator-panel")).toContainText(
      "The instance status cannot be read right now."
    );
  });
});
