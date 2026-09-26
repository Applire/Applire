// tests/oq/in-flow-import-gaps.spec.ts

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire. See <https://www.gnu.org/licenses/> for the
// GNU Affero General Public License this file is distributed under.

import { test, expect } from "@playwright/test";

/**
 * In-flow CV import with a job → gap step — OQ (Frontend collector #677)
 *
 * The in-flow import (`/flow/{id}/import`, shown when the profile is below the
 * Mode-B threshold) used to hand off with `POST /api/job/{id}/gaps` — a route
 * removed with the async gap jobs (97915fc3, 2026-07-01). Only GET remains, so
 * the hand-off answered 405 and the user stayed on the import step with an
 * error. This spec drives the real button path: upload → async import job →
 * async GAP job (POST gap-jobs + poll) → advance with the gap id → the gaps
 * page, and records every request to the job's gap routes.
 *
 * Uses page.route() mocks with a stateful flow (cv_import → gap_analysis) — no
 * backend needed. The sync POST is answered 405 exactly as the real router
 * does, so a regression shows the same symptom the user saw.
 */

const FLOW_ID = "flow-import-gaps-0000-0000-000000000001";
const JOB_ID = "job-import-gaps-0000-0000-0000-000000000002";
const GAP_ID = "gap-import-gaps-0000-0000-0000-000000000003";

const MOCK_GAP_ANALYSIS = {
  id: GAP_ID,
  match_score: 0.72,
  category_a: ["Python"],
  category_b: ["Docker"],
  category_c: ["Kubernetes"],
  strengths: ["Python"],
  gap_clusters: [
    {
      id: "cluster-import-gaps-c",
      label: "Container Orchestration",
      category: "C",
      gaps: ["Kubernetes"],
      jd_skills: ["Kubernetes"],
      jd_context: "Required for production deployments",
      outcome: { asked: 0, covered: [], declined: [], session_ids: [] },
      coverage: "open",
      budget_remaining: 2,
    },
  ],
};

type GapRequest = { method: string; path: string };

async function setupMocks(page: import("@playwright/test").Page, gapRequests: GapRequest[]) {
  let currentStep = "cv_import";
  let gapPolls = 0;

  await page.route("**/api/settings", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ui_language: "en", ui_language_explicit: true, dismissed_explainers: [] }),
    }),
  );
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        current_step: currentStep,
        job_id: JOB_ID,
        user_type: "new",
        available_actions: currentStep === "cv_import" ? { next: "gap_analysis" } : {},
        gap_summary: currentStep === "cv_import" ? null : { gap_analysis_id: GAP_ID },
        job_summary: { role_title: "Senior Software Engineer" },
      }),
    }),
  );
  await page.route(`**/api/flow/${FLOW_ID}/advance`, async (route) => {
    const body = route.request().postDataJSON() as { step?: string; artifact_id?: string };
    // The real router 422s a gap_analysis advance without its artifact.
    if (body.step !== "gap_analysis" || body.artifact_id !== GAP_ID) {
      await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "artifact_id is required" }) });
      return;
    }
    currentStep = "gap_analysis";
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ current_step: "gap_analysis" }) });
  });
  await page.route("**/api/profile/uploads", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/profile/import-jobs", (route) =>
    route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ import_id: "imp-1", status: "pending" }) }),
  );
  await page.route("**/api/profile/import-jobs/imp-1", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ready", error_code: null, result: { status: "MERGED", completeness_score: 0.62 } }),
    }),
  );
  await page.route("**/api/profile", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ stats: { positions: 3 } }) }),
  );
  // Every route under the job's gap namespace is recorded.
  await page.route(`**/api/job/${JOB_ID}/**`, async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    gapRequests.push({ method: req.method(), path });
    if (path.endsWith("/gap-jobs") && req.method() === "POST") {
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({ gap_job_id: "gj-1", status: "pending" }) });
    } else if (path.endsWith("/gap-jobs/gj-1")) {
      gapPolls += 1;
      // One "processing" poll first, so the named progress step is observable.
      const ready = gapPolls > 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          ready
            ? { status: "ready", error_code: null, result: MOCK_GAP_ANALYSIS }
            : { status: "processing", error_code: null, result: null },
        ),
      });
    } else if (path.endsWith("/gaps") && req.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_GAP_ANALYSIS) });
    } else if (path.endsWith("/gaps")) {
      await route.fulfill({ status: 405, contentType: "application/json", body: JSON.stringify({ detail: "Method Not Allowed" }) });
    } else {
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) });
    }
  });
}

test.describe("In-flow CV import with a job (#677)", () => {
  test("hands off to the gaps page through the async gap job, never the removed sync POST", async ({ page }) => {
    const gapRequests: GapRequest[] = [];
    await setupMocks(page, gapRequests);

    await page.goto(`/flow/${FLOW_ID}/import`);
    const input = page.getByTestId("main-file-input");
    await expect(input).toBeAttached({ timeout: 15000 });

    await input.setInputFiles({
      name: "cv.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Alex Example\nSoftware Engineer\nPython, FastAPI"),
    });

    // The third, honestly named progress step while the gap job runs.
    await expect(page.getByText("Comparing your profile with the job…")).toBeVisible({ timeout: 10000 });

    await expect(page).toHaveURL(`/flow/${FLOW_ID}/gaps`, { timeout: 20000 });
    await expect(page.getByTestId("gap-analysis-page")).toBeVisible({ timeout: 15000 });

    const hand_off = gapRequests.filter((r) => r.path.includes("/gap-jobs"));
    expect(hand_off[0]).toEqual({ method: "POST", path: `/api/job/${JOB_ID}/gap-jobs` });
    expect(hand_off.slice(1).every((r) => r.method === "GET" && r.path.endsWith("/gap-jobs/gj-1"))).toBe(true);
    expect(gapRequests.filter((r) => r.path.endsWith("/gaps") && r.method !== "GET")).toEqual([]);
  });
});
