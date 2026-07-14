// tests/oq/mobile/capture.spec.ts

// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import { test, expect } from "@playwright/test";

/**
 * Mobile capture stage — OQ Tests (US227, 390x844)
 *
 * US227's AC requires driving the capture -> triage -> CV-review OQ path, not
 * just visiting the dashboard. dashboard-shell.spec.ts covers the static
 * shell/grid; this file drives the actual Quick Tailor JD-capture interaction
 * (US224) at the mobile viewport: fill the URL tab, tap Analyse, and follow
 * the widget's real call sequence (analyze -> create application -> route
 * into the flow via the flow index page) — see
 * components/dashboard/QuickTailorWidget.tsx and
 * app/(shell)/flow/[flowId]/page.tsx for the sequence being mirrored here.
 *
 * Uses page.route() mocks — does NOT require a real backend response, only
 * the mock-provider Docker stack serving the branch's frontend build.
 */

const FLOW_ID = "flow-mobile-capture-0000-0000-000000000001";
const JOB_ID = "job-mobile-capture-0000-0000-000000000002";
const APP_ID = "app-mobile-capture-0000-0000-000000000003";

const MOCK_JOB_ANALYSIS = {
  id: JOB_ID,
  duplicate_of: null,
};

const MOCK_APPLICATION = {
  id: APP_ID,
  flow_session_id: FLOW_ID,
};

// No `current_step` key: the flow layout's redirect guard
// (lib/flow-routing.ts resolveFlowRedirect) fails open on an unknown step, so
// it never fights the flow index page's own advance-and-redirect effect
// below. Only `available_actions.next` (read directly by the index page) and
// `job_id` (needed for the gap_analysis branch, unused here) matter.
const MOCK_FLOW_STATE = {
  available_actions: { next: "cv_import" },
  job_id: JOB_ID,
};

const MOCK_ADVANCE = { current_step: "cv_import" };

async function setupDashboardMocks(page: import("@playwright/test").Page) {
  // GET /api/applications (dashboard grid load) and POST /api/applications
  // (Quick Tailor's create-application call, US224) share this endpoint —
  // one handler, branch on method.
  await page.route("**/api/applications", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_APPLICATION) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [] }) });
  });
  await page.route("**/api/profile/exists", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ exists: true, completeness_score: 0.8 }) })
  );
  await page.route("**/api/profile/import-jobs?active=true", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) })
  );
  await page.route("**/api/profile", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profile: { personal_info: { name: "Emma Musterfrau" } },
        stats: { positions: 3, projects: 2, certifications: 1, data_points: 20 },
      }),
    })
  );
}

async function setupCaptureMocks(page: import("@playwright/test").Page) {
  await page.route("**/api/job/analyze", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_JOB_ANALYSIS) })
  );
  await page.route(`**/api/flow/${FLOW_ID}/state`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_FLOW_STATE) })
  );
  await page.route(`**/api/flow/${FLOW_ID}/advance`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ADVANCE) })
  );
}

test.describe("Mobile Quick Tailor capture (390x844)", () => {
  test.beforeEach(async ({ page }) => {
    await setupDashboardMocks(page);
    await setupCaptureMocks(page);
  });

  test("filling the URL tab and tapping Analyse kicks off the flow, widget row does not overflow", async ({ page }, testInfo) => {
    await page.goto("/dashboard");

    const widget = page.getByTestId("quick-tailor-widget");
    await expect(widget).toBeVisible({ timeout: 10000 });

    await page.getByTestId("quick-tailor-url-input").fill("https://www.stepstone.de/stellenangebote/senior-frontend-engineer");

    // Widget row must not push the shell into horizontal overflow at 390px
    // (US224) — same verification-hierarchy check as the sibling specs: real
    // pixels, not the a11y tree.
    const overflowBeforeSubmit = await page.evaluate(() => ({
      scrollWidth: document.body.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    expect(overflowBeforeSubmit.scrollWidth).toBeLessThanOrEqual(overflowBeforeSubmit.innerWidth);

    await page.screenshot({ path: testInfo.outputPath("capture-mobile-390x844-filled.png"), fullPage: true });
    await testInfo.attach("capture-mobile-390x844-filled", {
      path: testInfo.outputPath("capture-mobile-390x844-filled.png"),
      contentType: "image/png",
    });

    await page.getByTestId("quick-tailor-submit").tap();

    // Success state: the widget's own call sequence (analyze -> create
    // application -> router.push the flow) landed the flow index page, which
    // advanced the state machine and routed on to the mocked next step.
    await expect(page).toHaveURL(`/flow/${FLOW_ID}/import`, { timeout: 10000 });
  });
});
